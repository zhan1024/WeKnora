package file

import (
	"context"
	"io"
	"net/http"
	"os"
	"testing"
	"time"

	"github.com/Tencent/WeKnora/internal/utils"
)

// ZOS integration tests are opt-in: they talk to a real bucket, so the
// credentials come from the environment instead of being committed:
//
//	ZOS_ACCESS_KEY, ZOS_SECRET_KEY   (required; tests skip when unset)
//	ZOS_ENDPOINT  (default https://huanan2.zos.ctyun.cn)
//	ZOS_BUCKET    (default bucket-c019)
//	ZOS_PREFIX    (default knowledgeBase-test)
var (
	zosEndpoint = envOr("ZOS_ENDPOINT", "https://huanan2.zos.ctyun.cn")
	zosHost     = "huanan2.zos.ctyun.cn"
	zosBucket   = envOr("ZOS_BUCKET", "bucket-c019")
	zosPrefix   = envOr("ZOS_PREFIX", "knowledgeBase-test")
)

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func zosCredentials(t *testing.T) (ak, sk string) {
	t.Helper()
	ak, sk = os.Getenv("ZOS_ACCESS_KEY"), os.Getenv("ZOS_SECRET_KEY")
	if ak == "" || sk == "" {
		t.Skip("set ZOS_ACCESS_KEY and ZOS_SECRET_KEY to run ZOS integration tests")
	}
	return ak, sk
}

func TestZZTmpS3(t *testing.T) {
	ak, sk := zosCredentials(t)
	t.Setenv("SSRF_WHITELIST", zosHost)
	utils.ResetSSRFWhitelistForTest()
	svc, err := NewS3FileServiceWithOptions(zosEndpoint, ak, sk, zosBucket, "huanan2", zosPrefix+"/", true)
	if err != nil {
		t.Fatal("s3 new:", err)
	}
	ctx := context.Background()
	path, err := svc.SaveBytes(ctx, []byte("hello zos s3"), 999999, "tmp-zos-check.txt", false)
	if err != nil {
		t.Fatal("s3 save:", err)
	}
	t.Log("saved:", path)
	url, err := svc.GetFileURL(ctx, path)
	if err != nil {
		t.Fatal("s3 presign:", err)
	}
	t.Log("presigned:", url)
	resp, err := http.Get(url)
	if err != nil {
		t.Fatal("s3 download:", err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	t.Log("download status:", resp.StatusCode, string(body))
	if err := svc.DeleteFile(ctx, path); err != nil {
		t.Fatal("s3 delete:", err)
	}
	t.Log("deleted ok")
}

func TestZZTmpMinio(t *testing.T) {
	ak, sk := zosCredentials(t)
	t.Setenv("SSRF_WHITELIST", zosHost)
	utils.ResetSSRFWhitelistForTest()
	svc, err := NewMinioFileService(zosHost, ak, sk, zosBucket, true)
	if err != nil {
		t.Fatal("minio new:", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	path, err := svc.SaveBytes(ctx, []byte("hello zos minio"), 999999, "tmp-zos-check.txt", false)
	if err != nil {
		t.Fatal("minio save:", err)
	}
	t.Log("saved:", path)
	url, err := svc.GetFileURL(ctx, path)
	if err != nil {
		t.Fatal("minio presign:", err)
	}
	t.Log("presigned:", url)
	resp, err := http.Get(url)
	if err != nil {
		t.Fatal("minio download:", err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	t.Log("download status:", resp.StatusCode, string(body))
	if err := svc.DeleteFile(ctx, path); err != nil {
		t.Fatal("minio delete:", err)
	}
	t.Log("deleted ok")
}
