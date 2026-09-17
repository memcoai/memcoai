package memory

import (
	"encoding/json"
	"fmt"
	"slices"
	"strings"

	"github.com/memcoai/memcoai/go/internal/fault"
)

// Scalar is a JSON Schema scalar type.
type Scalar string

const (
	String  Scalar = "string"
	Boolean Scalar = "boolean"
)

var scalarNames = map[Scalar]string{String: "a string", Boolean: "true or false"}

// ShapeKind says whether an argument is a scalar, a list of scalars or a list
// of objects.
type ShapeKind int

const (
	ScalarShape ShapeKind = iota
	ListShape
	ObjectsShape
)

// Field is one field of an object a model may send.
type Field struct {
	Name     string
	Type     Scalar
	Required bool
}

// Shape is the declared shape of one argument.
type Shape struct {
	Kind   ShapeKind
	Type   Scalar
	Fields []Field
	Entry  string // the type an object is, whose fields EntryCopies describes
}

// Argument is one argument a model may send to an operation.
type Argument struct {
	Name     string
	Shape    Shape
	Required bool
}

// Operation is one operation offered to a model. The session, the domain and
// the source are the caller's, so no operation takes them.
type Operation struct {
	Name      string
	Arguments []Argument
}

var tagFields = []Field{{"type", String, true}, {"value", String, true}, {"version", String, false}}

var feedbackFields = []Field{
	{"idx", String, true}, {"relevant", Boolean, true}, {"correct", Boolean, true}, {"comment", String, false},
}

var (
	textShape     = Shape{Kind: ScalarShape, Type: String}
	textsShape    = Shape{Kind: ListShape, Type: String}
	tagsShape     = Shape{Kind: ObjectsShape, Fields: tagFields, Entry: "Tag"}
	feedbackShape = Shape{Kind: ObjectsShape, Fields: feedbackFields, Entry: "FeedbackRating"}
)

// Operations are the offered operations, in the order a task uses them.
// Written out rather than derived: whatever is here reaches an untrusted model.
var Operations = []Operation{
	{"search", []Argument{{"query", textShape, true}, {"tags", tagsShape, false}}},
	{"get_memory", []Argument{{"idx", textShape, true}}},
	{"create_memory", []Argument{
		{"query", textShape, true}, {"title", textShape, true}, {"content", textShape, true}, {"tags", tagsShape, false},
	}},
	{"enrich_memory", []Argument{
		{"memory_idx", textShape, true}, {"title", textShape, true}, {"content", textShape, true},
		{"tags", tagsShape, false}, {"sources", textsShape, false},
	}},
	{"share_feedback", []Argument{{"feedback", feedbackShape, true}}},
	{"revert_memory", []Argument{{"operation_id", textShape, true}}},
}

func toolCopy(name string) ToolCopy {
	for _, entry := range ToolCopies {
		if entry.Name == name {
			return entry
		}
	}
	return ToolCopy{}
}

// Description is a tool's service-authored description.
func Description(name string) string { return toolCopy(name).Description }

func describe(op Operation, argument string) string {
	for _, parameter := range toolCopy(op.Name).Parameters {
		if parameter.Name == argument {
			return parameter.Description
		}
	}
	return ""
}

// Schema describes an operation's arguments as JSON Schema properties, built
// afresh on every call so a caller can change one without reaching another.
func Schema(op Operation) (properties map[string]any, required []string) {
	properties = map[string]any{}
	required = []string{}
	for _, argument := range op.Arguments {
		properties[argument.Name] = argumentSchema(argument.Shape, describe(op, argument.Name))
		if argument.Required {
			required = append(required, argument.Name)
		}
	}
	return properties, required
}

func argumentSchema(shape Shape, description string) map[string]any {
	switch shape.Kind {
	case ListShape:
		return map[string]any{"type": "array", "items": map[string]any{"type": string(shape.Type)}, "description": description}
	case ObjectsShape:
		return map[string]any{"type": "array", "items": objectSchema(shape), "description": description}
	default:
		return map[string]any{"type": string(shape.Type), "description": description}
	}
}

// objectSchema closes the object's shape. A key a model adds anyway is
// ignored rather than refused: a refused call costs a turn.
func objectSchema(shape Shape) map[string]any {
	properties := map[string]any{}
	required := []string{}
	for _, field := range shape.Fields {
		properties[field.Name] = map[string]any{"type": string(field.Type), "description": EntryDescription(shape.Entry, field.Name)}
		if field.Required {
			required = append(required, field.Name)
		}
	}
	return map[string]any{"type": "object", "properties": properties, "required": required, "additionalProperties": false}
}

// Decode reads a model's JSON arguments.
func Decode(raw []byte) (map[string]any, error) {
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		return nil, fault.Invalid("arguments are not valid JSON: " + err.Error())
	}
	object, ok := value.(map[string]any)
	if !ok {
		return nil, fault.Invalid("arguments must be an object, not " + jsonType(value))
	}
	return object, nil
}

func jsonType(value any) string {
	switch value.(type) {
	case nil:
		return "null"
	case []any:
		return "array"
	case map[string]any:
		return "object"
	case string:
		return "string"
	case bool:
		return "boolean"
	default:
		return "number"
	}
}

// Arguments checks what a model sent against op and converts it: strings,
// booleans, []string, and []map[string]any holding only declared fields. A
// null optional argument or field is the same as leaving it out.
func Arguments(op Operation, raw map[string]any) (map[string]any, error) {
	var undeclared []string
	for name := range raw {
		if !slices.ContainsFunc(op.Arguments, func(a Argument) bool { return a.Name == name }) {
			undeclared = append(undeclared, name)
		}
	}
	if len(undeclared) > 0 {
		slices.Sort(undeclared)
		for i, name := range undeclared {
			undeclared[i] = fmt.Sprintf("%q", name)
		}
		return nil, fault.Invalid("unknown argument(s): " + strings.Join(undeclared, ", "))
	}
	var missing []string
	for _, argument := range op.Arguments {
		if argument.Required && raw[argument.Name] == nil {
			missing = append(missing, argument.Name)
		}
	}
	if len(missing) > 0 {
		return nil, fault.Invalid("missing required argument(s): " + strings.Join(missing, ", "))
	}
	ready := map[string]any{}
	for _, argument := range op.Arguments {
		value := raw[argument.Name]
		if value == nil {
			continue
		}
		converted, err := coerced(argument.Shape, value, argument.Name)
		if err != nil {
			return nil, err
		}
		ready[argument.Name] = converted
	}
	return ready, nil
}

func scalar(typ Scalar, value any, where string) (any, error) {
	ok := false
	switch typ {
	case String:
		_, ok = value.(string)
	case Boolean:
		_, ok = value.(bool)
	}
	if !ok {
		return nil, fault.Invalid(fmt.Sprintf("%s must be %s, not %s", where, scalarNames[typ], jsonType(value)))
	}
	return value, nil
}

func coerced(shape Shape, value any, argument string) (any, error) {
	if shape.Kind == ScalarShape {
		return scalar(shape.Type, value, argument)
	}
	list, ok := value.([]any)
	if !ok {
		return nil, fault.Invalid(fmt.Sprintf("%s must be a list, not %s", argument, jsonType(value)))
	}
	if shape.Kind == ListShape {
		out := make([]string, 0, len(list))
		for i, entry := range list {
			checked, err := scalar(shape.Type, entry, fmt.Sprintf("%s[%d]", argument, i))
			if err != nil {
				return nil, err
			}
			out = append(out, checked.(string))
		}
		return out, nil
	}
	out := make([]map[string]any, 0, len(list))
	for _, entry := range list {
		object, err := objectFrom(shape.Fields, entry, argument)
		if err != nil {
			return nil, err
		}
		out = append(out, object)
	}
	return out, nil
}

func objectFrom(fields []Field, value any, argument string) (map[string]any, error) {
	object, ok := value.(map[string]any)
	if !ok {
		return nil, fault.Invalid(fmt.Sprintf("%s must hold objects, not %s", argument, jsonType(value)))
	}
	var missing []string
	for _, field := range fields {
		if _, present := object[field.Name]; field.Required && !present {
			missing = append(missing, field.Name)
		}
	}
	if len(missing) > 0 {
		return nil, fault.Invalid(fmt.Sprintf("%s is missing %s", argument, strings.Join(missing, ", ")))
	}
	supplied := map[string]any{}
	for _, field := range fields {
		item, present := object[field.Name]
		if !present || (item == nil && !field.Required) {
			continue
		}
		checked, err := scalar(field.Type, item, argument+"."+field.Name)
		if err != nil {
			return nil, err
		}
		supplied[field.Name] = checked
	}
	return supplied, nil
}

// Joined joins non-empty parts with a blank line.
func Joined(parts ...string) string {
	return strings.Join(slices.DeleteFunc(slices.Clone(parts), func(part string) bool { return part == "" }), "\n\n")
}
