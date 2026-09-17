package memory

import (
	"encoding/json"
	"go/format"
	"maps"
	"os"
	"reflect"
	"regexp"
	"slices"
	"strings"
	"testing"
)

type manifest struct {
	Tools []struct {
		Name        string            `json:"name"`
		Description string            `json:"description"`
		Parameters  map[string]string `json:"parameters"`
	} `json:"tools"`
}

func published(t *testing.T) manifest {
	t.Helper()
	data, err := os.ReadFile("../client/tools/tools.json")
	if err != nil {
		t.Fatal(err)
	}
	var m manifest
	if err := json.Unmarshal(data, &m); err != nil {
		t.Fatal(err)
	}
	return m
}

var marker = regexp.MustCompile(`\$\{tool:([a-z_]+)\}`)

func spelled(text string) string {
	return marker.ReplaceAllStringFunc(text, func(found string) string {
		name := marker.FindStringSubmatch(found)[1]
		if slices.Contains(OfferedTools, name) {
			return ToolPrefix + name
		}
		return name
	})
}

func TestEveryManifestToolIsCarriedWithItsCopy(t *testing.T) {
	m := published(t)
	var names []string
	for _, tool := range m.Tools {
		names = append(names, tool.Name)
	}
	var carried []string
	for _, entry := range ToolCopies {
		carried = append(carried, entry.Name)
	}
	if !reflect.DeepEqual(names, carried) {
		t.Fatalf("manifest %v, carried %v", names, carried)
	}
	for i, tool := range m.Tools {
		if ToolCopies[i].Description != spelled(tool.Description) {
			t.Errorf("%s: the description differs from the manifest's", tool.Name)
		}
	}
}

func TestParameterCopyIsCarriedFromBothSides(t *testing.T) {
	m := published(t)
	var compared []string
	for i, tool := range m.Tools {
		for key, text := range tool.Parameters {
			if strings.Contains(key, "[") {
				continue
			}
			name := key
			if key == "op_id" {
				name = "operation_id"
			}
			found := false
			for _, p := range ToolCopies[i].Parameters {
				if p.Name == name {
					found = true
					if p.Description != spelled(text) {
						t.Errorf("%s.%s differs from the manifest", tool.Name, name)
					}
				}
			}
			if !found {
				t.Errorf("%s.%s is not carried", tool.Name, name)
			}
			compared = append(compared, tool.Name+"."+name)
		}
	}
	var carried []string
	for _, entry := range ToolCopies {
		for _, p := range entry.Parameters {
			carried = append(carried, entry.Name+"."+p.Name)
		}
	}
	want := []string{
		"create_memory.content", "create_memory.domain", "create_memory.query", "create_memory.session_id",
		"create_memory.source", "create_memory.tags", "create_memory.title",
		"enrich_memory.content", "enrich_memory.memory_idx", "enrich_memory.session_id", "enrich_memory.source",
		"enrich_memory.sources", "enrich_memory.tags", "enrich_memory.title",
		"get_memory.idx",
		"import_memories.domain", "import_memories.memories", "import_memories.session_id",
		"revert_memory.operation_id",
		"search.domain", "search.query", "search.session_id", "search.tags",
		"share_feedback.feedback", "share_feedback.session_id",
		"start_session.domain",
	}
	slices.Sort(compared)
	slices.Sort(carried)
	if !reflect.DeepEqual(compared, want) {
		t.Errorf("compared %v", compared)
	}
	if !reflect.DeepEqual(carried, want) {
		t.Errorf("carried %v", carried)
	}
}

// entryField is the type and field a manifest key describes, when it names a
// field of the entries a tags or feedback list takes.
var entryField = regexp.MustCompile(`(?:^|\.)(tags|feedback)\[\]\.(\w+)$`)

func TestEachEntryFieldIsCarriedOncePerType(t *testing.T) {
	types := map[string]string{"tags": "Tag", "feedback": "FeedbackRating"}
	compared := map[string]bool{}
	for _, tool := range published(t).Tools {
		for key, text := range tool.Parameters {
			found := entryField.FindStringSubmatch(key)
			if found == nil {
				continue
			}
			name := types[found[1]] + "." + found[2]
			if got := EntryDescription(types[found[1]], found[2]); got != spelled(text) {
				t.Errorf("%s.%s: carried as %q", tool.Name, key, got)
			}
			compared[name] = true
		}
	}
	var carried []string
	for _, entry := range EntryCopies {
		for _, field := range entry.Fields {
			carried = append(carried, entry.Type+"."+field.Name)
		}
	}
	want := []string{
		"FeedbackRating.comment", "FeedbackRating.correct", "FeedbackRating.idx", "FeedbackRating.relevant",
		"Tag.type", "Tag.value", "Tag.version",
	}
	slices.Sort(carried)
	if names := slices.Sorted(maps.Keys(compared)); !reflect.DeepEqual(names, want) || !reflect.DeepEqual(carried, want) {
		t.Fatalf("compared %v, carried %v", names, carried)
	}
}

func TestTheNestedCopyIsCarriedByItsPath(t *testing.T) {
	m := published(t)
	var paths []string
	for _, tool := range m.Tools {
		for key, text := range tool.Parameters {
			if !strings.Contains(key, "[") || entryField.MatchString(key) {
				continue
			}
			paths = append(paths, key)
			found := slices.ContainsFunc(NestedCopy, func(p ParameterCopy) bool {
				return p.Name == key && p.Description == spelled(text)
			})
			if !found {
				t.Errorf("%s is not carried", key)
			}
		}
	}
	var carried []string
	for _, p := range NestedCopy {
		carried = append(carried, p.Name)
	}
	want := []string{
		"memories[].insights", "memories[].insights[].content", "memories[].insights[].title",
		"memories[].queries", "memories[].tags",
	}
	slices.Sort(paths)
	slices.Sort(carried)
	if !reflect.DeepEqual(paths, want) || !reflect.DeepEqual(carried, want) {
		t.Fatalf("paths %v, carried %v", paths, carried)
	}
}

func TestNoMarkerSurvivesAndEveryReferenceIsATool(t *testing.T) {
	answered := append(slices.Clone(AnsweredTools), "import_memories")
	reference := regexp.MustCompile(`\b(memco_[a-z_]+|list_domains|start_session|import_memories)\b`)
	for _, entry := range ToolCopies {
		texts := []string{entry.Description}
		for _, p := range entry.Parameters {
			texts = append(texts, p.Description)
		}
		for _, text := range texts {
			if strings.Contains(text, "${") {
				t.Errorf("%s carries a marker", entry.Name)
			}
			for _, name := range reference.FindAllString(text, -1) {
				offered := slices.Contains(OfferedTools, strings.TrimPrefix(name, ToolPrefix)) && strings.HasPrefix(name, ToolPrefix)
				if !offered && !slices.Contains(answered, name) {
					t.Errorf("%s names %s, which is neither offered nor answered", entry.Name, name)
				}
			}
		}
	}
}

func TestTheGeneratedFileIsMarkedAndFormatted(t *testing.T) {
	source, err := os.ReadFile("toolcopy_gen.go")
	if err != nil {
		t.Fatal(err)
	}
	if !regexp.MustCompile(`^// Code generated .* DO NOT EDIT\.\n`).Match(source) {
		t.Error("the file does not announce itself as generated")
	}
	formatted, err := format.Source(source)
	if err != nil {
		t.Fatal(err)
	}
	if string(formatted) != string(source) {
		t.Error("toolcopy_gen.go is not gofmt's fixed point")
	}
}
