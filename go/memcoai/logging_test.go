package memcoai

import (
	"bytes"
	"context"
	"encoding/hex"
	"fmt"
	"io"
	"log/slog"
	"os"
	"regexp"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc/codes"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/logging"
	"github.com/memcoai/memcoai/go/internal/testserver"
)

func TestTheLifecycleIsLoggedThroughTheCallersLogger(t *testing.T) {
	f := started(t)
	if err := f.client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	if err := f.client.Close(ctx); err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		"level=INFO msg=connected logger=memco.client target=" + f.server.Address + " tls=false",
		"level=INFO msg=\"closed connection\" logger=memco.client target=" + f.server.Address,
		"level=DEBUG msg=\"credential taken from the token argument\" logger=memco.config",
		"level=DEBUG msg=endpoint logger=memco.config target=" + f.server.Address + " tls=false source=\"the host argument\"",
	} {
		if !f.logs.Has(want) {
			t.Errorf("no %q in\n%s", want, f.logs)
		}
	}
	if !regexp.MustCompile(`level=DEBUG msg="rpc ok" logger=memco.client rpc=ListDomains elapsed_ms=\d+`).MatchString(f.logs.String()) {
		t.Errorf("no rpc record in\n%s", f.logs)
	}
}

func TestAReturnedErrorIsNeverAlsoLogged(t *testing.T) {
	f := started(t)
	f.server.Memory.Fail(&testserver.Failure{Code: codes.Unauthenticated, Details: "invalid credential"})
	expect[*AuthenticationError](t, f.client.Connect(ctx))
	_, err := f.client.Memory.Search(ctx, "q", SearchParams{Domain: "coding"})
	expect[*AuthenticationError](t, err)
	_, err = f.client.Memory.Search(ctx, " ", SearchParams{Domain: "coding"})
	expect[*InvalidRequestError](t, err)
	f.server.Memory.Fail(nil)
	f.server.Memory.Delay("Search", time.Second)
	go func() { _, _ = f.client.Memory.Search(ctx, "q", SearchParams{Domain: "coding"}) }()
	for len(f.server.Memory.Calls()) < 3 {
		time.Sleep(5 * time.Millisecond)
	}
	short, cancel := context.WithTimeout(ctx, 10*time.Millisecond)
	defer cancel()
	expect[*TimeoutError](t, f.client.Close(short))

	out := f.logs.String()
	for _, leaked := range []string{"invalid credential", "Authentication", "credential rejected", "failed", "must not be empty", "level=ERROR"} {
		if strings.Contains(out, leaked) {
			t.Errorf("%q was logged:\n%s", leaked, out)
		}
	}
	// Control: the capture works and holds records of this client.
	if !f.logs.Has("msg=endpoint") {
		t.Fatalf("nothing was captured:\n%s", out)
	}
}

func TestAFailedToolCatalogIsHandledAndSaysSo(t *testing.T) {
	f := connected(t)
	f.server.Memory.Fail(nil)
	for range 3 {
		f.server.Memory.FailNext("ListTools", testserver.Failure{Code: codes.Unavailable, Details: "catalog down"})
	}
	if _, err := f.client.Memory.StartSession(ctx, "coding"); err != nil {
		t.Fatal(err)
	}
	if !f.logs.Has(`level=WARN msg="listTools failed while opening a session; treating every tool as available" logger=memco.memory`) {
		t.Fatalf("got\n%s", f.logs)
	}
}

func TestADeprecationNoticeIsOneWarningPerMessage(t *testing.T) {
	f := connected(t)
	f.server.Memory.Respond("ListDomains", &memoryv1.ListDomainsResponse{
		Deprecated: true, DeprecationMessage: "upgrade the SDK", SunsetDate: "2027-01-01",
	})
	for range 2 {
		if _, err := f.client.Memory.ListDomains(ctx); err != nil {
			t.Fatal(err)
		}
	}
	want := `level=WARN msg="upgrade the SDK (stops working on 2027-01-01)"`
	if got := strings.Count(f.logs.String(), want); got != 1 {
		t.Fatalf("%d notices in\n%s", got, f.logs)
	}
	if !f.logs.Has("warning=MemcoDeprecationWarning") {
		t.Fatalf("got\n%s", f.logs)
	}
}

func TestAMalformedSunsetDateIsLeftOff(t *testing.T) {
	f := connected(t)
	f.server.Memory.Respond("ListDomains", &memoryv1.ListDomainsResponse{DeprecationMessage: "migrate", SunsetDate: "soon"})
	if _, err := f.client.Memory.ListDomains(ctx); err != nil {
		t.Fatal(err)
	}
	if !f.logs.Has("level=WARN msg=migrate ") {
		t.Fatalf("got\n%s", f.logs)
	}
}

func TestNoCredentialReachesAnyRecord(t *testing.T) {
	const secret = "sk-secret-credential"
	f := connected(t, func(o *Options) { o.Token = secret })
	if _, err := f.client.Memory.StartSession(ctx, "coding"); err != nil {
		t.Fatal(err)
	}
	if got := f.server.Memory.Metadata()[0].Get("authorization"); len(got) != 1 || got[0] != "Bearer "+secret {
		t.Fatalf("control: the credential was not sent: %q", got)
	}
	if !f.logs.Has("credential taken from the token argument") {
		t.Fatal("control: no configuration records were captured")
	}
	for _, leaked := range []string{secret, "Bearer"} {
		if f.logs.Has(leaked) {
			t.Fatalf("%q was logged:\n%s", leaked, f.logs)
		}
	}
}

// stderr points os.Stderr, which the SDK's own logger writes to, at a file for
// the rest of the test, and returns what has been written there so far.
func stderr(t *testing.T) func() string {
	t.Helper()
	file, err := os.CreateTemp(t.TempDir(), "stderr")
	if err != nil {
		t.Fatal(err)
	}
	original := os.Stderr
	os.Stderr = file
	t.Cleanup(func() {
		os.Stderr = original
		_ = logging.SetLevel("info")
	})
	return func() string {
		data, _ := os.ReadFile(file.Name())
		return string(data)
	}
}

func TestNoneSilencesTheDedicatedLoggerDeprecationsIncluded(t *testing.T) {
	read := stderr(t)
	run := func(level string) {
		f := started(t, func(o *Options) {
			o.Logger = nil
			o.LogLevel = level
		})
		f.server.Memory.Respond("ListDomains", &memoryv1.ListDomainsResponse{DeprecationMessage: "notice at " + level})
		if err := f.client.Connect(ctx); err != nil {
			t.Fatal(err)
		}
	}
	run("info")
	if out := read(); !strings.Contains(out, "notice at info") || !strings.Contains(out, "msg=connected") {
		t.Fatalf("control: the default logger wrote nothing at info:\n%s", out)
	}
	before := len(read())
	run("none")
	if after := read()[before:]; after != "" {
		t.Fatalf("none let records through:\n%s", after)
	}
}

func TestADiscardingLoggerSilencesTheClient(t *testing.T) {
	read := stderr(t)
	run := func(logger *slog.Logger) {
		f := connected(t, func(o *Options) {
			o.Logger = logger
			o.LogLevel = "debug"
		})
		if _, err := f.client.Memory.ListDomains(ctx); err != nil {
			t.Fatal(err)
		}
	}
	run(nil)
	if read() == "" {
		t.Fatal("control: the SDK's own logger wrote nothing at debug")
	}
	before := len(read())
	run(slog.New(slog.NewTextHandler(io.Discard, nil)))
	if after := read()[before:]; after != "" {
		t.Fatalf("a client with its own logger wrote to stderr:\n%s", after)
	}
}

// Options is the caller's own struct, so printing or logging it is the likeliest
// way for a token to reach a log.
func TestPrintedOptionsNeverShowTheToken(t *testing.T) {
	opts := Options{Token: "sk-live-secret", Host: "memco.example:443"}
	var logged bytes.Buffer
	slog.New(slog.NewTextHandler(&logged, nil)).Info("configured", "opts", opts)
	slog.New(slog.NewJSONHandler(&logged, nil)).Info("configured", "opts", opts)
	rendered := []string{
		fmt.Sprint(opts), fmt.Sprintf("%v %+v %#v %s %q %x", opts, opts, opts, opts, opts, opts),
		fmt.Sprint(&opts), logged.String(),
	}
	for _, text := range rendered {
		if strings.Contains(text, "secret") || strings.Contains(text, hex.EncodeToString([]byte("secret"))) {
			t.Errorf("the token was printed: %s", text)
		}
		if !strings.Contains(text, "memco.example") && !strings.Contains(text, hex.EncodeToString([]byte("memco.example"))) {
			t.Errorf("control: the host was not printed: %s", text)
		}
	}
}
