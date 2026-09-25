//go:build systemtest

package systemtest

// Sessions acting as an external user, against the live service. The fake
// server already proves which key each call carries; what only the real
// service can prove is what those keys do. A write made through a session is
// the user's: the user finds it, and the user alone can revert it, since a
// revert answers only for its caller's own writes. Two sessions running at
// once each act as their own user, each in a network of its own, so a crossed
// key would show as one finding what the other wrote. And closing a session
// really revokes its key.
//
// The memories follow the lifecycle suite's rules: the run marker, here the
// user's external id, lives only in the query, and the insight is plain prose
// about this SDK, distinct from every other write, so none reads as the same
// knowledge as another. A write a failing test leaves behind goes with its
// customer network.

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"os"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/mem"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"

	adminv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/admin/v1"
	authv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/auth/v1"
	"github.com/memcoai/memcoai/go/internal/config"
	"github.com/memcoai/memcoai/go/memcoai"
)

var (
	opening = note{
		query: "How does the Memco Go SDK open a memory session as one of an organisation's own users?",
		title: "Go SDK impersonated sessions",
		content: "The Memco Go SDK opens a memory session as one of an organisation's own users when " +
			"StartSession is given the ExternalID option. It mints an impersonation key under the API " +
			"client's token, lists the domains under that key so the session learns its limits as that " +
			"user, and only then starts the session.\n\n" +
			"Every call made through the session, its tools and the memories it returns included, " +
			"carries that key rather than the client's token. Closing the session ends the key on the " +
			"service instead of leaving it live until it expires.",
	}
	renewal = note{
		query: "When does the Memco Go SDK renew an impersonation key?",
		title: "Go SDK impersonation key renewal",
		content: "The Memco Go SDK renews an impersonation key once four fifths of its lifetime have " +
			"passed. Every goroutine that finds the key due waits on one shared mint, so a burst of calls " +
			"mints a single key and a failed mint reaches all of them at once. The key it replaces is " +
			"ended only after the last call still carrying it has returned, so a renewal never revokes a " +
			"key under a call in flight.",
	}
	sweep = note{
		query: "What happens to open impersonation keys when a Memco Go SDK client closes?",
		title: "Go SDK impersonation key cleanup",
		content: "Closing a Memco Go SDK client waits for the calls in flight, then ends every " +
			"impersonation key a session left open under the client's own token, bounded by the client's " +
			"timeout rather than the caller's context, so a cancelled close never strands a key. A key the " +
			"service reports as already gone counts as ended, and one it cannot end is logged by its id, " +
			"never its value, and expires on its own.",
	}
)

// asked is the note with the run marker added to the query alone.
func asked(n note, marker string) note {
	n.query += " (system test " + marker + ")"
	return n
}

// carries reports whether a memory was written with marker in its query,
// which becomes one of its intents.
func carries(memory memcoai.Memory, marker string) bool {
	return slices.ContainsFunc(memory.Intents, func(intent string) bool { return strings.Contains(intent, marker) })
}

// frame is a message the proxy passes on unread.
type frame struct{ data []byte }

// raw carries frames as they are, under the proto codec's name, so neither
// end sees anything but protobuf.
type raw struct{}

func (raw) Marshal(v any) (mem.BufferSlice, error) {
	return mem.BufferSlice{mem.SliceBuffer(v.(*frame).data)}, nil
}

func (raw) Unmarshal(data mem.BufferSlice, v any) error {
	v.(*frame).data = data.Materialize()
	return nil
}

func (raw) Name() string { return "proto" }

// keyProxy stands between a client and the service, passing every call on as
// it is and keeping each impersonation key the service mints. The SDK shows a
// key's value nowhere, which is the point; only the value lets the test ask
// the service whether a closed session's key still works.
type keyProxy struct {
	address  string
	upstream *grpc.ClientConn

	mu   sync.Mutex
	keys []*adminv1.ImpersonationKey
}

func startProxy(t *testing.T) *keyProxy {
	t.Helper()
	cfg, err := config.Resolve(config.Input{Token: "unused"}, os.Getenv, slog.New(slog.DiscardHandler))
	if err != nil {
		t.Fatal(err)
	}
	transport := insecure.NewCredentials()
	if cfg.TLS {
		transport = credentials.NewTLS(&tls.Config{MinVersion: tls.VersionTLS12})
	}
	upstream, err := grpc.NewClient(cfg.DialTarget(), grpc.WithTransportCredentials(transport))
	if err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	p := &keyProxy{address: listener.Addr().String(), upstream: upstream}
	server := grpc.NewServer(grpc.ForceServerCodecV2(raw{}), grpc.UnknownServiceHandler(p.forward))
	go func() { _ = server.Serve(listener) }()
	t.Cleanup(func() {
		server.Stop()
		_ = upstream.Close()
	})
	return p
}

func (p *keyProxy) forward(_ any, stream grpc.ServerStream) error {
	method, _ := grpc.MethodFromServerStream(stream)
	var request, response frame
	if err := stream.RecvMsg(&request); err != nil {
		return err
	}
	incoming, _ := metadata.FromIncomingContext(stream.Context())
	outgoing := metadata.MD{}
	if bearer := incoming.Get("authorization"); len(bearer) > 0 {
		outgoing.Set("authorization", bearer...)
	}
	ctx := metadata.NewOutgoingContext(stream.Context(), outgoing)
	if err := p.upstream.Invoke(ctx, method, &request, &response, grpc.ForceCodecV2(raw{})); err != nil {
		return err
	}
	if method == adminv1.AdminService_ImpersonateExternalUser_FullMethodName {
		key := &adminv1.ImpersonationKey{}
		if err := proto.Unmarshal(response.data, key); err != nil {
			return err
		}
		p.mu.Lock()
		p.keys = append(p.keys, key)
		p.mu.Unlock()
	}
	return stream.SendMsg(&response)
}

func (p *keyProxy) minted() []*adminv1.ImpersonationKey {
	p.mu.Lock()
	defer p.mu.Unlock()
	return slices.Clone(p.keys)
}

// through connects an API client to the service by way of the proxy.
func (p *keyProxy) through(t *testing.T) *memcoai.Client {
	t.Helper()
	administrator(t)   // skips without the API client's credentials
	plaintext := false // to the proxy on loopback; it dials the service as configured
	return connect(t, memcoai.Options{
		ClientID: os.Getenv(clientIDEnv), ClientSecret: os.Getenv(clientSecretEnv),
		Host: p.address, TLS: &plaintext,
	})
}

// endAgain ends an impersonation key directly, as the SDK would.
func (p *keyProxy) endAgain(ctx context.Context, externalID, keyID string) error {
	token, err := authv1.NewTokenServiceClient(p.upstream).IssueToken(ctx, &authv1.IssueTokenRequest{
		GrantType: "client_credentials", ClientId: os.Getenv(clientIDEnv), ClientSecret: os.Getenv(clientSecretEnv),
	})
	if err != nil {
		return err
	}
	authorized := metadata.AppendToOutgoingContext(ctx, "authorization", "Bearer "+token.GetAccessToken())
	_, err = adminv1.NewAdminServiceClient(p.upstream).EndImpersonation(authorized,
		&adminv1.EndImpersonationRequest{ExternalId: externalID, KeyId: keyID})
	return err
}

// keyWorks reports whether the service still takes key, by connecting with it.
func keyWorks(ctx context.Context, key *adminv1.ImpersonationKey) (bool, error) {
	client, err := memcoai.NewClient(memcoai.Options{Token: key.GetValue()})
	if err != nil {
		return false, err
	}
	defer func() { _ = client.Close(ctx) }()
	err = client.Connect(ctx)
	var rejected *memcoai.AuthenticationError
	if errors.As(err, &rejected) {
		return false, nil
	}
	return err == nil, err
}

// act writes, finds and reverts a memory as one user, failing if a search ever
// returns what other wrote. It returns rather than fails, so it can run on a
// goroutine of its own.
func act(ctx context.Context, client *memcoai.Client, domain, user, other string, fields note) error {
	session, err := client.Memory.StartSession(ctx, domain, memcoai.ExternalID(user))
	if err != nil {
		return fmt.Errorf("%s: opening: %w", user, err)
	}
	defer session.Close(ctx)
	before, err := session.Search(ctx, fields.query, memcoai.ScopedSearchParams{})
	if err != nil {
		return fmt.Errorf("%s: searching first: %w", user, err)
	}
	if slices.ContainsFunc(before.Memories, func(m memcoai.Memory) bool { return carries(m, user) }) {
		return fmt.Errorf("%s: its memory was found before it was written", user)
	}
	write, err := session.CreateMemory(ctx, memcoai.ScopedCreateMemoryParams{
		Query: fields.query, Title: fields.title, Content: fields.content, Source: memcoai.DataSourceAgent,
	})
	if err != nil {
		return fmt.Errorf("%s: writing: %w", user, err)
	}
	if write.OperationID == "" {
		return fmt.Errorf("%s: the write was accepted without an operation id to revert", user)
	}
	deadline := time.Now().Add(ingestTimeout)
	for pause := firstPoll; ; pause = min(2*pause, pollInterval) {
		result, err := session.Search(ctx, fields.query, memcoai.ScopedSearchParams{})
		if err != nil {
			return fmt.Errorf("%s: searching: %w", user, err)
		}
		if other != "" && slices.ContainsFunc(result.Memories, func(m memcoai.Memory) bool { return carries(m, other) }) {
			return fmt.Errorf("%s's session found what %s wrote, in a network it is not in", user, other)
		}
		if slices.ContainsFunc(result.Memories, func(m memcoai.Memory) bool { return carries(m, user) }) {
			break
		}
		if time.Now().Add(pause).After(deadline) {
			return fmt.Errorf("%s's memory never became searchable within %s", user, ingestTimeout)
		}
		time.Sleep(pause)
	}
	undo, err := session.RevertMemory(ctx, write.OperationID)
	if err != nil {
		return fmt.Errorf("%s: reverting: %w", user, err)
	}
	if undo.Outcome != memcoai.RevertOutcomeMemoryRemoved {
		return fmt.Errorf("reverting as %s, who wrote it, reported %s", user, undo.Outcome)
	}
	return nil
}

func TestASessionActsAsAnExternalUserAndItsKeyEndsWithIt(t *testing.T) {
	admin := administrator(t)
	network, user := placed(t, admin, rootNetwork(t, admin))
	proxy := startProxy(t)
	client := proxy.through(t)
	ctx := t.Context()
	t.Logf("[%s] acting as %s", network.Domain, user.ExternalID)

	session, err := client.Memory.StartSession(ctx, network.Domain, memcoai.ExternalID(user.ExternalID))
	if err != nil {
		t.Fatal(err)
	}
	if err := act(ctx, client, network.Domain, user.ExternalID, "", asked(opening, user.ExternalID)); err != nil {
		t.Fatal(err)
	}
	keys := proxy.minted()
	if len(keys) != 2 {
		t.Fatalf("two sessions minted %d keys", len(keys))
	}
	// The session act opened has closed; this one is still open, so its key
	// is refused below because of the close, not because it never worked.
	if works, err := keyWorks(ctx, keys[0]); err != nil || !works {
		t.Fatalf("the open session's key does not work: %v", err)
	}
	if works, err := keyWorks(ctx, keys[1]); err != nil || works {
		t.Fatalf("a closed session's key still works: %v", err)
	}
	session.Close(ctx)
	if works, err := keyWorks(ctx, keys[0]); err != nil || works {
		t.Fatalf("the key still works after its session closed: %v", err)
	}
	// Ending an ended key is answered NOT_FOUND, which the SDK counts as
	// ended: any other answer would have every close after a key ran out log
	// a warning, and stop the client's sweep at that key.
	if err := proxy.endAgain(ctx, user.ExternalID, keys[0].GetKeyId()); status.Code(err) != codes.NotFound {
		t.Fatalf("ending an ended key answered %v", err)
	}
	if len(proxy.minted()) != 2 {
		t.Fatalf("the sessions minted %d keys", len(proxy.minted()))
	}
}

func TestTwoUsersActAtOnceEachUnderAKeyOfItsOwn(t *testing.T) {
	admin := administrator(t)
	root := rootNetwork(t, admin)
	_, first := placed(t, admin, root)
	_, second := placed(t, admin, root)
	proxy := startProxy(t)
	client := proxy.through(t)
	ctx := t.Context()
	t.Logf("[%s] acting as %s and %s at once", root.Domain, first.ExternalID, second.ExternalID)

	errs := make(chan error, 2)
	go func() {
		errs <- act(ctx, client, root.Domain, first.ExternalID, second.ExternalID, asked(renewal, first.ExternalID))
	}()
	go func() {
		errs <- act(ctx, client, root.Domain, second.ExternalID, first.ExternalID, asked(sweep, second.ExternalID))
	}()
	for range 2 {
		if err := <-errs; err != nil {
			t.Error(err)
		}
	}
	// One key per session, each ended by its own session's close before the
	// client closes.
	keys := proxy.minted()
	if len(keys) != 2 {
		t.Fatalf("two sessions minted %d keys", len(keys))
	}
	for _, key := range keys {
		if works, err := keyWorks(ctx, key); err != nil || works {
			t.Errorf("%s still works after its session closed: %v", key.GetKeyId(), err)
		}
	}
}
