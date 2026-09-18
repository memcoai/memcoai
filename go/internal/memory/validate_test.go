package memory

import (
	"bytes"
	"errors"
	"log/slog"
	"reflect"
	"strings"
	"testing"

	"google.golang.org/grpc/codes"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/fault"
)

// rejected fails unless err is a local INVALID_ARGUMENT with exactly want.
func rejected(t *testing.T, err error, want string) {
	t.Helper()
	var f *fault.Error
	if !errors.As(err, &f) {
		t.Fatalf("got %v, want %q", err, want)
	}
	if f.Kind != fault.KindAPI || f.Code != codes.InvalidArgument || f.Detail != want {
		t.Fatalf("got %s %v %q, want %q", f.Code, f.Kind, f.Detail, want)
	}
}

func debugLog() (*slog.Logger, *bytes.Buffer) {
	var buffer bytes.Buffer
	return slog.New(slog.NewTextHandler(&buffer, &slog.HandlerOptions{Level: slog.LevelDebug})), &buffer
}

func TestABlankValueIsRefusedByItsFieldName(t *testing.T) {
	for _, value := range []string{"", " ", "\t\n", "\u00a0\u2003"} {
		rejected(t, present(value, "query"), "query must not be empty")
	}
	if err := present(" x ", "query"); err != nil {
		t.Fatal(err)
	}
}

func TestCharactersAreCountedAsCodePoints(t *testing.T) {
	cases := map[string]int{"": 0, "abc": 3, "é": 1, "👍👍": 2, "日本語": 3, "e\u0301": 2}
	for value, want := range cases {
		if got := CountCharacters(value); got != want {
			t.Errorf("%q: got %d, want %d", value, got, want)
		}
	}
}

func TestNothingIsCheckedAgainstAnUnreportedCap(t *testing.T) {
	if err := within(strings.Repeat("x", 1<<20), "query", 0); err != nil {
		t.Fatal(err)
	}
	if err := count(1<<20, "feedback", 0); err != nil {
		t.Fatal(err)
	}
}

func TestALongValueIsRefusedAndMeasured(t *testing.T) {
	rejected(t, within("hello", "query", 4), "query is 5 characters, which exceeds the limit of 4")
	if err := within("👍👍👍👍", "query", 4); err != nil {
		t.Fatalf("four emoji refused at a cap of four: %v", err)
	}
	if err := within("four", "query", 4); err != nil {
		t.Fatal(err)
	}
	rejected(t, count(3, "feedback", 2), "feedback has 3 entries, which exceeds the limit of 2")
	if err := count(2, "feedback", 2); err != nil {
		t.Fatal(err)
	}
}

func TestTrimmingKeepsTheFirstEntriesAndSaysSo(t *testing.T) {
	log, out := debugLog()
	values := []string{"a", "b", "c"}
	got := trimmed(values, 2, "sources", log)
	if strings.Join(got, ",") != "a,b" || strings.Join(values, ",") != "a,b,c" {
		t.Fatalf("got %v, input %v", got, values)
	}
	if !strings.Contains(out.String(), "level=DEBUG msg=trimmed field=sources from=3 to=2") {
		t.Fatalf("got %q", out.String())
	}
}

func TestNothingToTrimSaysNothing(t *testing.T) {
	log, out := debugLog()
	values := []string{"a", "b"}
	if got := trimmed(values, 2, "sources", log); len(got) != 2 {
		t.Fatalf("got %v", got)
	}
	if got := trimmed(values, 0, "sources", log); len(got) != 2 {
		t.Fatalf("got %v", got)
	}
	if out.Len() != 0 {
		t.Fatalf("got %q", out.String())
	}
}

func TestTheScopeNeedsADomainOrASession(t *testing.T) {
	const neither = "pass a domain or a session_id: a request needs one of them to name a domain"
	rejected(t, checkScope("", ""), neither)
	rejected(t, checkScope("  ", ""), "domain must not be empty")
	rejected(t, checkScope("", " "), "session_id must not be empty")
	rejected(t, checkScope(" ", "session-a"), "domain must not be empty")
	for _, ok := range [][2]string{{"coding", ""}, {"", "session-a"}, {"coding", "session-a"}} {
		if err := checkScope(ok[0], ok[1]); err != nil {
			t.Errorf("%v: %v", ok, err)
		}
	}
}

func TestABlankTagIsRefusedByWhichSetItIsIn(t *testing.T) {
	rejected(t, checkTags([]*memoryv1.Tag{{Type: "", Value: "go"}}, "tag"), "tag type must not be empty")
	rejected(t, checkTags([]*memoryv1.Tag{{Type: "language", Value: " "}}, "tag"), "tag value must not be empty")
	rejected(t, checkTags([]*memoryv1.Tag{{Type: "a", Value: "b"}, {Value: "c"}}, "memories[3] tag"),
		"memories[3] tag type must not be empty")
	if err := checkTags([]*memoryv1.Tag{{Type: "language", Value: "go", Version: new(string)}}, "tag"); err != nil {
		t.Fatalf("an empty version is not the tag's to refuse: %v", err)
	}
	if err := checkTags(nil, "tag"); err != nil {
		t.Fatal(err)
	}
}

// The caps are int32 on the wire, so a malformed listing can report a negative
// one; it is read as unreported rather than crashing or refusing everything.
func TestANegativeCapIsUnreported(t *testing.T) {
	if err := within("hello", "query", -1); err != nil {
		t.Fatal(err)
	}
	if err := count(3, "feedback", -1); err != nil {
		t.Fatal(err)
	}
	if err := together("t", "c", "title and content", -1); err != nil {
		t.Fatal(err)
	}
	log, _ := debugLog()
	for _, values := range [][]string{nil, {"a", "b"}} {
		if got := trimmed(values, -1, "tags", log); !reflect.DeepEqual(got, values) {
			t.Fatalf("trimmed %v to %v", values, got)
		}
	}
}
