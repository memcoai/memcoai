package memcoai

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"

	"github.com/memcoai/memcoai/go/internal/fault"
	"github.com/memcoai/memcoai/go/internal/memory"
)

// JSONSchema is one JSON Schema fragment, as a framework takes it.
type JSONSchema = map[string]any

// ToolParameters is a JSON Schema object describing one tool's arguments. Each
// [Session.Tools] call builds it afresh, so a framework may change it in place.
type ToolParameters struct {
	// Type is always "object".
	Type string `json:"type"`
	// Properties describe each argument, keyed by its snake_case name.
	Properties map[string]JSONSchema `json:"properties"`
	// Required names the arguments a call cannot go without. Never nil.
	Required []string `json:"required"`
}

// Tool is one memory operation offered to a model, bound to a [Session].
type Tool struct {
	// Name is what a model calls the tool, such as "memco_search".
	Name string
	// Description is the service's own description of the operation.
	Description string
	// Parameters describe the arguments. The session, the domain and the
	// content's source are the caller's, so no tool offers them.
	Parameters ToolParameters

	operation memory.Operation
	session   *Session
}

// Call runs the tool with the arguments a model sent, and renders the result
// as text for the model.
//
// Parameters:
//   - ctx: bounds the call, as for the operation the tool runs.
//   - args: the model's arguments, as the JSON object it sent.
//
// It returns the rendered result. A mistake the model can correct — malformed
// or missing arguments, a request the service refused as invalid, a handle that
// resolves to nothing — also comes back as text, beginning "invalid request:"
// or "nothing found:", with a nil error.
//
// Errors: every other failure of the operation, such as
// [*AuthenticationError], [*ResourceExhaustedError] or a context that ended;
// no wording would help a model with those. [IsAgentRecoverable] tells the two
// apart.
func (t Tool) Call(ctx context.Context, args json.RawMessage) (string, error) {
	return recovered(t.call(ctx, args))
}

func (t Tool) call(ctx context.Context, args json.RawMessage) (string, error) {
	raw, err := memory.Decode(args)
	if err != nil {
		return "", public(err)
	}
	checked, err := memory.Arguments(t.operation, raw)
	if err != nil {
		return "", public(err)
	}
	result, err := t.session.run(ctx, t.operation.Name, checked)
	if err != nil {
		return "", err
	}
	return Render(result), nil
}

// recovered words the failures a model can act on; every other failure is
// returned, because no wording would help a model with it.
func recovered(text string, err error) (string, error) {
	var invalid *InvalidRequestError
	if errors.As(err, &invalid) {
		return "invalid request: " + invalid.Detail, nil
	}
	var notFound *NotFoundError
	if errors.As(err, &notFound) {
		return "nothing found: " + notFound.Detail, nil
	}
	return text, err
}

// Tools offers this session's operations to a model: search, get_memory,
// create_memory, enrich_memory, share_feedback and revert_memory, each named
// with a "memco_" prefix and limited to those the credential's role permits.
// When the session could not learn the role's permissions, every tool is
// offered.
//
// It returns a fresh toolset; it sends nothing.
func (s *Session) Tools() *Toolset {
	tools := []Tool{}
	for _, operation := range memory.Operations {
		if s.catalogKnown && !slices.ContainsFunc(s.catalog, func(tool ToolDescriptor) bool {
			return tool.Name == operation.Name && tool.Available
		}) {
			continue
		}
		properties, required := memory.Schema(operation)
		schema := make(map[string]JSONSchema, len(properties))
		for name, property := range properties {
			schema[name] = property.(map[string]any)
		}
		tools = append(tools, Tool{
			Name:        memory.ToolPrefix + operation.Name,
			Description: memory.Description(operation.Name),
			Parameters:  ToolParameters{Type: "object", Properties: schema, Required: required},
			operation:   operation,
			session:     s,
		})
	}
	return &Toolset{Tools: tools}
}

// run calls the named operation with arguments memory.Arguments has checked.
func (s *Session) run(ctx context.Context, name string, args map[string]any) (Rendered, error) {
	text := func(key string) string { value, _ := args[key].(string); return value }
	switch name {
	case "search":
		return rendered(s.Search(ctx, text("query"), ScopedSearchParams{Tags: tagsOf(args["tags"])}))
	case "get_memory":
		return rendered(s.GetMemory(ctx, text("idx")))
	case "create_memory":
		return rendered(s.CreateMemory(ctx, ScopedCreateMemoryParams{
			Query: text("query"), Title: text("title"), Content: text("content"), Tags: tagsOf(args["tags"]),
		}))
	case "enrich_memory":
		sources, _ := args["sources"].([]string)
		return rendered(s.EnrichMemory(ctx, ScopedEnrichMemoryParams{
			MemoryIdx: text("memory_idx"), Title: text("title"), Content: text("content"),
			Tags: tagsOf(args["tags"]), Sources: sources,
		}))
	case "share_feedback":
		return rendered(s.ShareFeedback(ctx, ratingsOf(args["feedback"])))
	case "revert_memory":
		return rendered(s.RevertMemory(ctx, text("operation_id")))
	}
	return nil, public(fault.Internal("no operation " + name))
}

// rendered keeps a failed call's typed nil out of the Rendered interface.
func rendered[R Rendered](result R, err error) (Rendered, error) {
	if err != nil {
		return nil, err
	}
	return result, nil
}

func objects(value any) []map[string]any {
	list, _ := value.([]map[string]any)
	return list
}

func tagsOf(value any) []Tag {
	var tags []Tag
	for _, object := range objects(value) {
		typ, _ := object["type"].(string)
		val, _ := object["value"].(string)
		version, _ := object["version"].(string)
		tags = append(tags, Tag{Type: typ, Value: val, Version: version})
	}
	return tags
}

func ratingsOf(value any) []FeedbackRating {
	var ratings []FeedbackRating
	for _, object := range objects(value) {
		idx, _ := object["idx"].(string)
		relevant, _ := object["relevant"].(bool)
		correct, _ := object["correct"].(bool)
		comment, _ := object["comment"].(string)
		ratings = append(ratings, FeedbackRating{Idx: idx, Relevant: relevant, Correct: correct, Comment: comment})
	}
	return ratings
}

// Toolset is every operation a [Session] offers to a model.
type Toolset struct {
	// Tools are the offered tools, in the order a task uses them.
	Tools []Tool
}

// Call runs the tool a model named, as [Tool.Call] does.
//
// Parameters:
//   - ctx: bounds the call.
//   - name: the tool name the model used.
//   - args: the model's arguments, as the JSON object it sent. OpenAI's
//     function arguments string can be passed as is.
//
// It returns the rendered result, or text telling the model what it got
// wrong, including a name that is not a tool.
//
// Errors: as [Tool.Call].
func (s *Toolset) Call(ctx context.Context, name string, args json.RawMessage) (string, error) {
	var names []string
	for _, tool := range s.Tools {
		if tool.Name == name {
			return tool.Call(ctx, args)
		}
		names = append(names, tool.Name)
	}
	slices.Sort(names)
	return recovered("", public(fault.Invalid(fmt.Sprintf("no tool named %q; there is %s", name, strings.Join(names, ", ")))))
}

// AnthropicTool is a tool definition in the Claude Messages API's shape.
type AnthropicTool struct {
	// Name is the tool's name.
	Name string `json:"name"`
	// Description is the tool's description.
	Description string `json:"description"`
	// InputSchema describes the tool's arguments.
	InputSchema ToolParameters `json:"input_schema"`
}

// OpenAITool is a tool definition in the OpenAI Chat Completions API's shape.
type OpenAITool struct {
	// Type is always "function".
	Type string `json:"type"`
	// Function describes the tool.
	Function OpenAIFunction `json:"function"`
}

// OpenAIFunction is the function an [OpenAITool] describes.
type OpenAIFunction struct {
	// Name is the tool's name.
	Name string `json:"name"`
	// Description is the tool's description.
	Description string `json:"description"`
	// Parameters describes the tool's arguments.
	Parameters ToolParameters `json:"parameters"`
}

// ToAnthropic returns the tools as Claude Messages API tool definitions, ready
// to marshal into a request's tools. Run the model's tool_use blocks with
// [Toolset.Call].
//
// It returns one definition per tool, sharing each tool's Parameters.
func (s *Toolset) ToAnthropic() []AnthropicTool {
	tools := make([]AnthropicTool, 0, len(s.Tools))
	for _, tool := range s.Tools {
		tools = append(tools, AnthropicTool{Name: tool.Name, Description: tool.Description, InputSchema: tool.Parameters})
	}
	return tools
}

// ToOpenAI returns the tools as OpenAI Chat Completions function tools. Run
// the model's tool calls with [Toolset.Call].
//
// It returns one definition per tool, sharing each tool's Parameters.
func (s *Toolset) ToOpenAI() []OpenAITool {
	tools := make([]OpenAITool, 0, len(s.Tools))
	for _, tool := range s.Tools {
		tools = append(tools, OpenAITool{Type: "function", Function: OpenAIFunction{
			Name: tool.Name, Description: tool.Description, Parameters: tool.Parameters,
		}})
	}
	return tools
}

// IsAgentRecoverable reports whether a tool hands err to the model as text
// rather than returning it: a malformed request, or a handle that resolves to
// nothing.
//
// Parameters:
//   - err: an error an operation returned.
//
// It returns true for an [*InvalidRequestError] or a [*NotFoundError].
func IsAgentRecoverable(err error) bool {
	var invalid *InvalidRequestError
	var notFound *NotFoundError
	return errors.As(err, &invalid) || errors.As(err, &notFound)
}

// Rendered is a result [Render] can turn into text: a [SearchResult],
// [Memory], [WriteResult], [FeedbackResult] or [RevertResult], or a pointer to
// one.
type Rendered interface {
	render() string
}

// Render turns a result into text a model can read, including the guidance
// the service sent with it and the reliability signal on every insight.
//
// Parameters:
//   - r: a result an operation returned; it must not be nil.
//
// It returns the text.
func Render(r Rendered) string { return r.render() }

func renderInstructions(i Instructions) string {
	return memory.Joined(i.Content, i.Policy, i.Adding, i.Rating, i.Next)
}

func (r SearchResult) render() string {
	noun := "memories"
	if len(r.Memories) == 1 {
		noun = "memory"
	}
	memories := make([]string, 0, len(r.Memories))
	for _, m := range r.Memories {
		memories = append(memories, m.render())
	}
	return memory.Joined(
		memory.Joined(fmt.Sprintf("%d %s", len(r.Memories), noun), r.Notice),
		strings.Join(memories, "\n\n"),
		renderInstructions(r.Instructions),
	)
}

func (m Memory) render() string {
	if m.Reference != "" {
		return fmt.Sprintf("%s  already returned in this session as %s; fetch it again with %sget_memory",
			m.Idx, m.Reference, memory.ToolPrefix)
	}
	heading := fmt.Sprintf("%s  served %dx", m.Idx, m.TimesServed)
	if m.Kind != "" {
		heading += "  (" + m.Kind + ")"
	}
	lines := []string{heading}
	if len(m.Intents) > 0 {
		lines = append(lines, "  retrieved before by: "+strings.Join(m.Intents, "; "))
	}
	for _, insight := range m.Insights {
		// endorsed and disputed are the reliability signal served is not.
		marks := []string{fmt.Sprintf("served %dx", insight.TimesServed)}
		if insight.Endorsed != 0 {
			marks = append(marks, fmt.Sprintf("endorsed %dx", insight.Endorsed))
		}
		if insight.Disputed != 0 {
			marks = append(marks, fmt.Sprintf("disputed %dx", insight.Disputed))
		}
		written := "never updated"
		if insight.Updated != "" {
			written = "updated " + insight.Updated
		}
		lines = append(lines, fmt.Sprintf("  %s  %s\n    [%s, %s]\n    %s",
			insight.Idx, insight.Title, written, strings.Join(marks, ", "), insight.Content))
	}
	return strings.Join(lines, "\n")
}

func (r WriteResult) render() string {
	head := "accepted; this write cannot be undone"
	if r.OperationID != "" {
		head = "accepted as operation " + r.OperationID
	}
	return memory.Joined(head, renderInstructions(r.Instructions))
}

func (r FeedbackResult) render() string {
	lines := make([]string, 0, len(r.Entries))
	for _, entry := range r.Entries {
		line := fmt.Sprintf("%s  relevant=%t correct=%t", entry.Idx, entry.Relevant, entry.Correct)
		if entry.Advice != "" {
			line += "  " + entry.Advice
		}
		lines = append(lines, line)
	}
	return memory.Joined(strings.Join(lines, "\n"), renderInstructions(r.Instructions))
}

func (r RevertResult) render() string {
	id := r.OperationID
	if id == "" {
		id = "null"
	}
	outcome := strings.ToLower(strings.ReplaceAll(r.Outcome.String(), "_", " "))
	return memory.Joined(id+": "+outcome, renderInstructions(r.Instructions))
}

// Briefing turns what the service says about a domain into guidance for a
// model: what the domain holds, when to search and save, the tag vocabulary,
// and why list_domains and start_session are not among its tools.
//
// Parameters:
//   - domain: the session's domain, from [MemoryOperations.ListDomains].
//   - instructions: what the service said when the session opened, as
//     [Session.Instructions].
//
// It returns the guidance, ready to be a system prompt or part of one.
func Briefing(domain DomainEntry, instructions Instructions) string {
	parts := []string{
		fmt.Sprintf("You share a persistent memory with your team, in the '%s' domain (%s). "+
			"Search it before working anything out from scratch, and save what you learn so nobody has to learn it twice.",
			domain.Slug, domain.Title),
		domain.Summary,
		labelled("When to search: ", domain.WhenToSearch),
		labelled("When to save: ", domain.WhenToSave),
		labelled("What not to save: ", domain.WhatNotToSave),
		domain.TagsDescription,
	}
	if len(domain.FilterTagTypes) > 0 {
		parts = append(parts, "These tag types narrow the results rather than boosting them, so a wrong one returns nothing: "+
			strings.Join(domain.FilterTagTypes, ", ")+".")
	}
	if len(domain.VersionTagTypes) > 0 {
		parts = append(parts, "These carry a version, and one on any other type is dropped: "+
			strings.Join(domain.VersionTagTypes, ", ")+".")
	}
	if domain.MaxTagsPerQuery > 0 {
		parts = append(parts, fmt.Sprintf("At most %d tags per call.", domain.MaxTagsPerQuery))
	}
	parts = append(parts,
		"The domain and the session are already chosen, so "+strings.Join(memory.AnsweredTools, " and ")+
			" are not among your tools: where a tool's description names one, what it would have told you is above.",
		fmt.Sprintf("Pass '%s' as memory_idx to %senrich_memory to open a new memory.", NewMemory, memory.ToolPrefix),
		instructions.Content,
	)
	return memory.Joined(parts...)
}

func labelled(label, text string) string {
	if text == "" {
		return ""
	}
	return label + text
}
