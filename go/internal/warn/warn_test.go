package warn

import (
	"bytes"
	"log/slog"
	"strings"
	"testing"
)

func capture(t *testing.T) (*slog.Logger, *bytes.Buffer) {
	t.Helper()
	Reset()
	t.Cleanup(Reset)
	var buffer bytes.Buffer
	return slog.New(slog.NewTextHandler(&buffer, nil)), &buffer
}

func TestADeprecationIsSurfacedOncePerMessage(t *testing.T) {
	log, out := capture(t)
	Deprecation(log, "upgrade the SDK", "2027-01-01")
	Deprecation(log, "upgrade the SDK", "2027-01-01")
	if got := strings.Count(out.String(), "upgrade the SDK"); got != 1 {
		t.Fatalf("surfaced %d times: %q", got, out.String())
	}
	want := `level=WARN msg="upgrade the SDK (stops working on 2027-01-01)" warning=MemcoDeprecationWarning`
	if !strings.Contains(out.String(), want) {
		t.Fatalf("got %q", out.String())
	}
}

func TestAChangedMessageIsSurfacedAgain(t *testing.T) {
	log, out := capture(t)
	Deprecation(log, "first", "")
	Deprecation(log, "second", "")
	if !strings.Contains(out.String(), "msg=first") || !strings.Contains(out.String(), "msg=second") {
		t.Fatalf("got %q", out.String())
	}
}

func TestTheMessageIsRelayedVerbatimWithoutASunset(t *testing.T) {
	log, out := capture(t)
	Deprecation(log, "API v1 is retired; migrate to v2.", "")
	if !strings.Contains(out.String(), `msg="API v1 is retired; migrate to v2."`) {
		t.Fatalf("got %q", out.String())
	}
}

func TestAnEmptyMessageSaysNothing(t *testing.T) {
	log, out := capture(t)
	Deprecation(log, "", "2027-01-01")
	if out.Len() != 0 {
		t.Fatalf("got %q", out.String())
	}
	Deprecation(log, "control", "")
	if out.Len() == 0 {
		t.Fatal("control: a non-empty message is not written")
	}
}

func TestOnceWritesANamedWarningOnce(t *testing.T) {
	log, out := capture(t)
	Once(log, "DeprecationWarning", "MEMCO_API_KEY is deprecated")
	Once(log, "DeprecationWarning", "MEMCO_API_KEY is deprecated")
	if got := strings.Count(out.String(), "MEMCO_API_KEY"); got != 1 {
		t.Fatalf("written %d times", got)
	}
	if !strings.Contains(out.String(), "warning=DeprecationWarning") {
		t.Fatalf("got %q", out.String())
	}
}

func TestResetSurfacesAgain(t *testing.T) {
	log, out := capture(t)
	Deprecation(log, "again", "")
	Reset()
	Deprecation(log, "again", "")
	if got := strings.Count(out.String(), "again"); got != 2 {
		t.Fatalf("written %d times", got)
	}
}
