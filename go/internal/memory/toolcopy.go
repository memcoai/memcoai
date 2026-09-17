// Package memory holds the memory service's helpers: its retry list, the caps
// it reports, request checks, wire helpers and the agent tool tables.
package memory

// ToolCopy is one tool's service-authored copy. Field order matters: the
// generated literals in toolcopy_gen.go are positional.
type ToolCopy struct {
	Name        string
	Description string
	Parameters  []ParameterCopy
}

// ParameterCopy is the copy for one parameter, or one nested request field.
type ParameterCopy struct {
	Name        string
	Description string
}

// EntryCopy is the copy for the fields of one type of list entry, such as a
// Tag. Field order matters, as for ToolCopy.
type EntryCopy struct {
	Type   string
	Fields []ParameterCopy
}

// EntryDescription is the copy for one field of an entry type, or "".
func EntryDescription(typ, field string) string {
	for _, entry := range EntryCopies {
		if entry.Type != typ {
			continue
		}
		for _, f := range entry.Fields {
			if f.Name == field {
				return f.Description
			}
		}
	}
	return ""
}
