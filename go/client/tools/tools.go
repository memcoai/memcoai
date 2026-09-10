// Package tools publishes the agent tool definitions for the memory service: the
// model-facing description of each operation, its effect class, and a
// description of every parameter. It is generated from the same copy the hosted
// MCP server publishes, so an agent built on this SDK and one connected over MCP
// are told the same things.
//
// The manifest ships spelling-neutral — a tool referenced inside the copy is a
// ${tool:...} marker — because the generated clients disagree on casing. This
// package resolves those markers to the method names this SDK exposes, so the
// text a model reads names methods it can actually call.
package tools

import (
	"encoding/json"
	"fmt"
	"maps"
	"strings"

	_ "embed"
)

//go:embed tools.json
var manifest []byte

// Tool is one operation published as an agent tool. Parameters map a request
// field to its description; the field's type comes from the generated request
// message, which states it precisely.
type Tool struct {
	Name        string
	RPC         string
	Title       string
	Description string
	Annotations Annotations
	Parameters  map[string]string
}

// Annotations is the tool's effect class, in the spelling an agent client reads.
type Annotations struct {
	ReadOnlyHint    bool
	DestructiveHint bool
	IdempotentHint  bool
	OpenWorldHint   bool
}

// Instructions is the service-level guidance accompanying the tool set, meant
// for an agent's system prompt. It names tools the way this SDK does.
func Instructions() string { return loaded.instructions }

// Tools returns every published tool definition.
func Tools() []Tool {
	published := make([]Tool, 0, len(loaded.tools))
	for _, tool := range loaded.tools {
		published = append(published, tool.clone())
	}
	return published
}

// clone returns a Tool sharing nothing with the parsed manifest. Copying the
// struct alone would hand every caller the same Parameters map, so one editing
// its own copy would reach the next caller's — and race any concurrent reader.
func (t Tool) clone() Tool {
	if t.Parameters == nil {
		return t
	}
	parameters := make(map[string]string, len(t.Parameters))
	maps.Copy(parameters, t.Parameters)
	t.Parameters = parameters
	return t
}

// ToolsByName returns the named tools in the order given, skipping any name that
// is not published. Names are the manifest's own — snake_case, stable across
// SDKs — so a caller selecting a subset is unaffected by casing conventions.
func ToolsByName(names ...string) []Tool {
	selected := make([]Tool, 0, len(names))
	for _, name := range names {
		for _, tool := range loaded.tools {
			if tool.Name == name {
				selected = append(selected, tool.clone())
			}
		}
	}
	return selected
}

// MethodName returns the Go method this SDK generates for a manifest tool name.
// The transform is duplicated rather than imported so this package stays
// dependency-free, which is what lets a consumer take the tool metadata without
// taking the rest of the client.
func MethodName(name string) string {
	parts := strings.Split(name, "_")
	for i, part := range parts {
		if part == "" {
			continue
		}
		parts[i] = strings.ToUpper(part[:1]) + part[1:]
	}
	return strings.Join(parts, "")
}

type document struct {
	Instructions string `json:"instructions"`
	Tools        []struct {
		Name        string            `json:"name"`
		RPC         string            `json:"rpc"`
		Title       string            `json:"title"`
		Description string            `json:"description"`
		Annotations Annotations       `json:"annotations"`
		Parameters  map[string]string `json:"parameters"`
	} `json:"tools"`
}

var loaded = load()

type resolved struct {
	instructions string
	tools        []Tool
}

// load parses the embedded manifest and resolves its markers once. A failure is
// a broken build rather than a runtime condition — the manifest is generated and
// compiled in — so it panics rather than making every caller handle an error
// that cannot occur in a package that built.
func load() resolved {
	var doc document
	if err := json.Unmarshal(manifest, &doc); err != nil {
		panic(fmt.Sprintf("tools: parse embedded manifest: %v", err))
	}
	out := resolved{instructions: render(doc.Instructions)}
	for _, tool := range doc.Tools {
		parameters := make(map[string]string, len(tool.Parameters))
		for name, description := range tool.Parameters {
			parameters[name] = render(description)
		}
		out.tools = append(out.tools, Tool{
			Name:        tool.Name,
			RPC:         tool.RPC,
			Title:       tool.Title,
			Description: render(tool.Description),
			Annotations: tool.Annotations,
			Parameters:  parameters,
		})
	}
	return out
}

const (
	tokenPrefix = "${tool:"
	tokenSuffix = "}"
)

// render spells every ${tool:...} marker as this SDK's method name. An
// unterminated marker is left alone: it is not a reference, and truncating the
// text around it would lose copy.
func render(text string) string {
	var spelled strings.Builder
	rest := text
	for {
		before, after, found := strings.Cut(rest, tokenPrefix)
		spelled.WriteString(before)
		if !found {
			return spelled.String()
		}
		name, tail, terminated := strings.Cut(after, tokenSuffix)
		if !terminated {
			spelled.WriteString(tokenPrefix)
			spelled.WriteString(after)
			return spelled.String()
		}
		spelled.WriteString(MethodName(name))
		rest = tail
	}
}
