package memory

import (
	"encoding/json"
	"errors"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/memcoai/memcoai/go/internal/fault"
)

func operation(t *testing.T, name string) Operation {
	t.Helper()
	for _, op := range Operations {
		if op.Name == name {
			return op
		}
	}
	t.Fatalf("no operation %q", name)
	return Operation{}
}

func TestTheOperationsAreTheOfferedTools(t *testing.T) {
	var names []string
	for _, op := range Operations {
		names = append(names, op.Name)
	}
	if !reflect.DeepEqual(names, OfferedTools) {
		t.Fatalf("got %v, want %v", names, OfferedTools)
	}
}

func TestEveryOfferedToolHasTheServicesDescription(t *testing.T) {
	for _, name := range OfferedTools {
		got := Description(name)
		if got == "" || strings.Contains(got, "${") {
			t.Errorf("%s: %q", name, got)
		}
	}
	if Description("teleport") != "" {
		t.Error("an invented tool has a description")
	}
}

func TestNoSchemaOffersABoundArgument(t *testing.T) {
	if len(Operations) == 0 {
		t.Fatal("control: no operations to check")
	}
	for _, op := range Operations {
		properties, _ := Schema(op)
		for _, bound := range []string{"domain", "session_id", "source", "timeout"} {
			if _, ok := properties[bound]; ok {
				t.Errorf("%s offers %s", op.Name, bound)
			}
		}
	}
}

func TestEveryArgumentAndFieldIsDescribed(t *testing.T) {
	described := 0
	for _, op := range Operations {
		properties, _ := Schema(op)
		if len(properties) != len(op.Arguments) {
			t.Errorf("%s: %d properties for %d arguments", op.Name, len(properties), len(op.Arguments))
		}
		for name, raw := range properties {
			property := raw.(map[string]any)
			if text, _ := property["description"].(string); text == "" {
				t.Errorf("%s.%s is undescribed", op.Name, name)
			} else {
				described++
			}
			items, _ := property["items"].(map[string]any)
			fields, _ := items["properties"].(map[string]any)
			for field, rawField := range fields {
				if text, _ := rawField.(map[string]any)["description"].(string); text == "" {
					t.Errorf("%s.%s.%s is undescribed", op.Name, name, field)
				} else {
					described++
				}
			}
		}
	}
	if described < 20 {
		t.Fatalf("only %d descriptions were checked", described)
	}
}

func TestRequiredArgumentsAreExact(t *testing.T) {
	want := map[string][]string{
		"search":         {"query"},
		"get_memory":     {"idx"},
		"create_memory":  {"query", "title", "content"},
		"enrich_memory":  {"memory_idx", "title", "content"},
		"share_feedback": {"feedback"},
		"revert_memory":  {"operation_id"},
	}
	for name, required := range want {
		_, got := Schema(operation(t, name))
		if !reflect.DeepEqual(got, required) {
			t.Errorf("%s: required %v, want %v", name, got, required)
		}
	}
}

func TestTheArgumentsAreSpelledInSnakeCase(t *testing.T) {
	properties, _ := Schema(operation(t, "enrich_memory"))
	for _, name := range []string{"memory_idx", "title", "content", "tags", "sources"} {
		if _, ok := properties[name]; !ok {
			t.Errorf("enrich_memory has no %s: %v", name, properties)
		}
	}
	if _, ok := properties["memoryIdx"]; ok {
		t.Error("a camelCase argument")
	}
}

func TestTagsAndRatingsAreObjectsWithAClosedShape(t *testing.T) {
	properties, _ := Schema(operation(t, "search"))
	tagSchema := properties["tags"].(map[string]any)
	if tagSchema["type"] != "array" {
		t.Fatalf("tags: %v", tagSchema)
	}
	items := tagSchema["items"].(map[string]any)
	if items["type"] != "object" || items["additionalProperties"] != false ||
		!reflect.DeepEqual(items["required"], []string{"type", "value"}) {
		t.Fatalf("tag items: %v", items)
	}
	var fields []string
	for name := range items["properties"].(map[string]any) {
		fields = append(fields, name)
	}
	slices.Sort(fields)
	if !reflect.DeepEqual(fields, []string{"type", "value", "version"}) {
		t.Fatalf("tag fields: %v", fields)
	}

	properties, _ = Schema(operation(t, "share_feedback"))
	rating := properties["feedback"].(map[string]any)["items"].(map[string]any)
	if !reflect.DeepEqual(rating["required"], []string{"idx", "relevant", "correct"}) {
		t.Fatalf("rating: %v", rating)
	}
	relevant := rating["properties"].(map[string]any)["relevant"].(map[string]any)
	if relevant["type"] != "boolean" {
		t.Fatalf("relevant: %v", relevant)
	}
	sources := func() map[string]any {
		p, _ := Schema(operation(t, "enrich_memory"))
		return p["sources"].(map[string]any)
	}()
	if !reflect.DeepEqual(sources["items"], map[string]any{"type": "string"}) {
		t.Fatalf("sources: %v", sources)
	}
}

func TestParameterCopyComesFromTheManifest(t *testing.T) {
	if len(Operations) == 0 {
		t.Fatal("control: no operations to check")
	}
	copyOf := func(tool, parameter string) string {
		for _, entry := range ToolCopies {
			if entry.Name != tool {
				continue
			}
			for _, p := range entry.Parameters {
				if p.Name == parameter {
					return p.Description
				}
			}
		}
		return ""
	}
	fields := 0
	for _, op := range Operations {
		properties, _ := Schema(op)
		for _, argument := range op.Arguments {
			got := properties[argument.Name].(map[string]any)["description"]
			manifest := copyOf(op.Name, argument.Name)
			if manifest == "" || got != manifest {
				t.Errorf("%s.%s: got %q, want the manifest's %q", op.Name, argument.Name, got, manifest)
			}
			if argument.Shape.Kind != ObjectsShape {
				continue
			}
			// The fields of each entry are described by the manifest too, once
			// per type an entry is.
			items := properties[argument.Name].(map[string]any)["items"].(map[string]any)
			for name, field := range items["properties"].(map[string]any) {
				got := field.(map[string]any)["description"]
				want := EntryDescription(argument.Shape.Entry, name)
				if want == "" || got != want {
					t.Errorf("%s.%s.%s: got %q, want the manifest's %q", op.Name, argument.Name, name, got, want)
				}
				fields++
			}
		}
	}
	if fields != 3*3+4 {
		t.Errorf("compared %d entry fields", fields)
	}
}

func TestEverySchemaIsFresh(t *testing.T) {
	first, _ := Schema(operation(t, "search"))
	first["query"].(map[string]any)["description"] = "changed"
	first["tags"].(map[string]any)["items"].(map[string]any)["required"] = []string{}
	second, _ := Schema(operation(t, "search"))
	if second["query"].(map[string]any)["description"] == "changed" {
		t.Fatal("a schema shares its descriptions")
	}
	if len(second["tags"].(map[string]any)["items"].(map[string]any)["required"].([]string)) != 2 {
		t.Fatal("a schema shares its nested fields")
	}
}

func decoded(t *testing.T, text string) map[string]any {
	t.Helper()
	args, err := Decode([]byte(text))
	if err != nil {
		t.Fatalf("Decode(%s): %v", text, err)
	}
	return args
}

func TestDecodeTakesAnObjectAndNothingElse(t *testing.T) {
	if got := decoded(t, `{"query": "q"}`); got["query"] != "q" {
		t.Fatalf("got %v", got)
	}
	cases := map[string]string{
		`"q"`:      "arguments must be an object, not string",
		`[1]`:      "arguments must be an object, not array",
		`null`:     "arguments must be an object, not null",
		`42`:       "arguments must be an object, not number",
		`true`:     "arguments must be an object, not boolean",
		`{"a":`:    "arguments are not valid JSON: ",
		``:         "arguments are not valid JSON: ",
		`{} extra`: "arguments are not valid JSON: ",
	}
	for text, want := range cases {
		_, err := Decode([]byte(text))
		if err == nil || !strings.HasPrefix(errDetail(t, err), want) {
			t.Errorf("%s: got %v, want %q", text, err, want)
		}
	}
}

func errDetail(t *testing.T, err error) string {
	t.Helper()
	var f *fault.Error
	if !errors.As(err, &f) {
		t.Fatalf("untyped error %v", err)
	}
	return f.Detail
}

func TestArgumentsAreCheckedLikeAModelWillSendThem(t *testing.T) {
	cases := []struct {
		op, args, want string
	}{
		{"search", `{"query": "q", "source": "user"}`, `unknown argument(s): "source"`},
		{"search", `{"query": "q", "session_id": "s", "domain": "d", "timeout": 3}`, `unknown argument(s): "domain", "session_id", "timeout"`},
		{"search", `{"query": "q", "evil\nkey": 1}`, `unknown argument(s): "evil\nkey"`},
		// Sorted by name before quoting: quoted, "a!" sorts before "a".
		{"search", `{"query": "q", "a!": 1, "a": 1}`, `unknown argument(s): "a", "a!"`},
		{"search", `{}`, "missing required argument(s): query"},
		{"search", `{"query": null}`, "missing required argument(s): query"},
		{"create_memory", `{"title": "t"}`, "missing required argument(s): query, content"},
		{"search", `{"query": 3}`, "query must be a string, not number"},
		{"search", `{"query": ["q"]}`, "query must be a string, not array"},
		{"search", `{"query": "q", "tags": "go"}`, "tags must be a list, not string"},
		{"search", `{"query": "q", "tags": {"type": "a"}}`, "tags must be a list, not object"},
		{"search", `{"query": "q", "tags": ["go"]}`, "tags must hold objects, not string"},
		{"search", `{"query": "q", "tags": [null]}`, "tags must hold objects, not null"},
		{"search", `{"query": "q", "tags": [{"type": "language"}]}`, "tags is missing value"},
		{"search", `{"query": "q", "tags": [{}]}`, "tags is missing type, value"},
		{"search", `{"query": "q", "tags": [{"type": 1, "value": "go"}]}`, "tags.type must be a string, not number"},
		{"enrich_memory", `{"memory_idx": "new", "title": "t", "content": "c", "sources": ["a", 2]}`, "sources[1] must be a string, not number"},
		{"share_feedback", `{"feedback": [{"idx": "i", "relevant": "yes", "correct": true}]}`, "feedback.relevant must be true or false, not string"},
		{"share_feedback", `{"feedback": [{"idx": "i", "relevant": true}]}`, "feedback is missing correct"},
		{"revert_memory", `{"operationId": "x"}`, `unknown argument(s): "operationId"`},
	}
	for _, c := range cases {
		_, err := Arguments(operation(t, c.op), decoded(t, c.args))
		if err == nil {
			t.Errorf("%s %s: accepted", c.op, c.args)
			continue
		}
		rejected(t, err, c.want)
	}
}

func TestArgumentsKeepOnlyWhatWasDeclared(t *testing.T) {
	got, err := Arguments(operation(t, "search"), decoded(t,
		`{"query": "q", "tags": [{"type": "language", "value": "go", "version": null, "extra": 1}]}`))
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]any{
		"query": "q",
		"tags":  []map[string]any{{"type": "language", "value": "go"}},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %#v", got)
	}

	got, err = Arguments(operation(t, "enrich_memory"), decoded(t,
		`{"memory_idx": "new", "title": "t", "content": "c", "tags": null, "sources": ["a"]}`))
	if err != nil {
		t.Fatal(err)
	}
	want = map[string]any{"memory_idx": "new", "title": "t", "content": "c", "sources": []string{"a"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %#v", got)
	}

	got, err = Arguments(operation(t, "share_feedback"), decoded(t,
		`{"feedback": [{"idx": "i", "relevant": true, "correct": false, "comment": "why"}]}`))
	if err != nil {
		t.Fatal(err)
	}
	want = map[string]any{"feedback": []map[string]any{{"idx": "i", "relevant": true, "correct": false, "comment": "why"}}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %#v", got)
	}
}

func TestJoinedDropsEmptyParts(t *testing.T) {
	if got := Joined("a", "", "b", ""); got != "a\n\nb" {
		t.Fatalf("got %q", got)
	}
	if got := Joined("", ""); got != "" {
		t.Fatalf("got %q", got)
	}
}

func TestDecodedNumbersAreStillNumbers(t *testing.T) {
	var raw map[string]any
	if err := json.Unmarshal([]byte(`{"n": 1}`), &raw); err != nil {
		t.Fatal(err)
	}
	if _, err := Arguments(operation(t, "get_memory"), map[string]any{"idx": raw["n"]}); err == nil {
		t.Fatal("a number was accepted as an idx")
	}
}
