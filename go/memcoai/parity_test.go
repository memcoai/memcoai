package memcoai

import (
	"reflect"
	"regexp"
	"slices"
	"strings"
	"testing"

	"github.com/memcoai/memcoai/go/internal/memory"
)

func methods(v any) []string {
	var names []string
	kind := reflect.TypeOf(v)
	for i := range kind.NumMethod() {
		names = append(names, kind.Method(i).Name)
	}
	return names
}

func TestASessionOffersEveryOperationButTheOnesItAnswers(t *testing.T) {
	var want []string
	for _, name := range methods(&MemoryOperations{}) {
		if !slices.Contains([]string{"ListDomains", "ListTools", "StartSession"}, name) {
			want = append(want, name)
		}
	}
	want = append(want, "Tools", "Close")
	slices.Sort(want)
	if got := methods(&Session{}); !reflect.DeepEqual(got, want) {
		t.Fatalf("Session has %v, want %v", got, want)
	}
}

func TestNoScopedParameterNamesAScope(t *testing.T) {
	for _, params := range []any{ScopedSearchParams{}, ScopedCreateMemoryParams{}, ScopedEnrichMemoryParams{}} {
		kind := reflect.TypeOf(params)
		for i := range kind.NumField() {
			if name := kind.Field(i).Name; name == "Domain" || name == "SessionID" {
				t.Errorf("%s has %s", kind.Name(), name)
			}
		}
	}
	session := reflect.TypeOf(&Session{})
	for i := range session.NumMethod() {
		method := session.Method(i).Type
		for in := range method.NumIn() {
			if method.In(in).Kind() == reflect.String && session.Method(i).Name == "ShareFeedback" {
				t.Errorf("Session.ShareFeedback takes a string: a session id")
			}
		}
	}
}

func TestTheOfferedToolsAreTheSessionsOperations(t *testing.T) {
	snake := regexp.MustCompile(`([a-z])([A-Z])`)
	var got []string
	for _, name := range methods(&Session{}) {
		if name == "Tools" || name == "ImportMemories" || name == "Close" {
			continue
		}
		got = append(got, strings.ToLower(snake.ReplaceAllString(name, "${1}_${2}")))
	}
	want := slices.Clone(memory.OfferedTools)
	slices.Sort(want)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}
