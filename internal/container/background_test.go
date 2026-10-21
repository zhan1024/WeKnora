package container

import (
	"context"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/require"

	"github.com/Tencent/WeKnora/internal/agent/tools"
	"github.com/Tencent/WeKnora/internal/application/service"
	"github.com/Tencent/WeKnora/internal/background"
	"github.com/Tencent/WeKnora/internal/sandbox"
	"github.com/Tencent/WeKnora/internal/stream"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/Tencent/WeKnora/internal/types/interfaces"
)

func TestBackgroundActivityRegistryUsesRedisBindingStore(t *testing.T) {
	mini := miniredis.RunT(t)
	client := redis.NewClient(&redis.Options{Addr: mini.Addr()})
	t.Cleanup(func() { _ = client.Close() })

	store := newSessionBindingStore(client)
	require.IsType(t, &sandbox.RedisSessionSandboxBindingStore{}, store)
	require.IsType(t, &sandbox.RedisActivityRegistry{}, newBackgroundActivityRegistry(store))
}

func TestBackgroundActivityRegistryIsProcessLocalWithoutRedis(t *testing.T) {
	store := newSessionBindingStore(nil)
	require.IsType(t, &sandbox.MemorySessionSandboxBindingStore{}, store)
	require.IsType(t, &background.MemoryActivityRegistry{}, newBackgroundActivityRegistry(store))
}

func TestBackgroundActivityRegistryWithoutBindingStoreIsProcessLocal(t *testing.T) {
	require.IsType(t, &background.MemoryActivityRegistry{}, newBackgroundActivityRegistry(nil))
}

func TestBackgroundNoticeInboxFollowsTheStreamManager(t *testing.T) {
	mini := miniredis.RunT(t)
	redisStreams, err := stream.NewRedisStreamManager(mini.Addr(), "", "", 0, "test", time.Hour)
	require.NoError(t, err)
	t.Cleanup(func() { _ = redisStreams.Close() })
	require.IsType(t, &stream.RedisNoticeInbox{}, newBackgroundNoticeInbox(redisStreams))

	require.IsType(t, &background.MemoryNoticeInbox{}, newBackgroundNoticeInbox(stream.NewMemoryStreamManager()))
	require.IsType(t, &background.MemoryNoticeInbox{}, newBackgroundNoticeInbox(nil))
}

type nameCleaner struct {
	names []string
	stops []types.CleanupFunc
}

func (c *nameCleaner) Register(fn types.CleanupFunc) { c.RegisterWithName("", fn) }

func (c *nameCleaner) RegisterWithName(name string, fn types.CleanupFunc) {
	c.names = append(c.names, name)
	c.stops = append(c.stops, fn)
}

func (c *nameCleaner) Promote(string) {}

func (c *nameCleaner) Cleanup(context.Context) []error {
	var errs []error
	for _, fn := range c.stops {
		if err := fn(); err != nil {
			errs = append(errs, err)
		}
	}
	return errs
}

func TestNewShellJobServiceSetsHandler(t *testing.T) {
	bg := service.NewBackgroundWork(background.NewMemoryActivityRegistry(nil), background.NewMemoryNoticeInbox(nil))
	svc := newRemoteShellJobService(nil, nil, bg, nil)
	require.Equal(t, svc, bg.Handler)
	require.False(t, svc.RemoteAvailable())
	_, err := svc.SubmitShellJob(context.Background(), interfaces.SessionRef{TenantID: 1, SessionID: "s"},
		tools.ShellJobSubmitRequest{Command: "true"})
	require.Error(t, err)
}

func TestShellJobSweeperStartsOnlyWhenRemoteAvailable(t *testing.T) {
	bg := service.NewBackgroundWork(background.NewMemoryActivityRegistry(nil), background.NewMemoryNoticeInbox(nil))
	local := newRemoteShellJobService(nil, nil, bg, stream.NewMemoryStreamManager())
	cleaner := &nameCleaner{}
	startShellJobSweeper(local, cleaner)
	require.Empty(t, cleaner.names)

	mini := miniredis.RunT(t)
	client := redis.NewClient(&redis.Options{Addr: mini.Addr()})
	t.Cleanup(func() { _ = client.Close() })
	remoteBG := service.NewBackgroundWork(background.NewMemoryActivityRegistry(nil), background.NewMemoryNoticeInbox(nil))
	remote := newRemoteShellJobService(client, &service.PinnedSessionSandbox{}, remoteBG, nil)
	require.True(t, remote.RemoteAvailable())
	startShellJobSweeper(remote, cleaner)
	require.Equal(t, []string{"ShellJobSweeper"}, cleaner.names)
	for _, err := range cleaner.Cleanup(context.Background()) {
		require.NoError(t, err)
	}
}
