// Package provenance reads SDK_PROVENANCE.yaml, the export's record of which
// contract the generated client was built from.
package provenance

import (
	"encoding/json"
	"slices"
	"strings"
	"sync"
	"unicode"

	"github.com/memcoai/memcoai/go/internal"
	"github.com/memcoai/memcoai/go/internal/fault"
)

const resource = "SDK_PROVENANCE.yaml"

// Record is what the descriptor says.
type Record struct {
	ServerCommit string
	Protos       []Proto
}

// Proto is one contract file and its checksum.
type Proto struct {
	Path   string
	SHA256 string
}

var read = sync.OnceValues(func() (Record, error) { return Parse(string(internal.Descriptor)) })

// Read parses the embedded descriptor once and hands out copies.
func Read() (Record, error) {
	record, err := read()
	record.Protos = slices.Clone(record.Protos)
	return record, err
}

// Parse reads a descriptor with the strict subset of YAML the export writes.
// Anything it does not model is refused rather than guessed at.
func Parse(text string) (Record, error) {
	lines := significant(text)
	var record Record
	for index, line := range lines {
		if line.indent != 0 {
			continue
		}
		if value, ok := strings.CutPrefix(line.content, "server_commit:"); ok {
			commit, err := scalar(value, "server_commit")
			if err != nil {
				return Record{}, err
			}
			record.ServerCommit = commit
			continue
		}
		if line.content == "protos:" {
			protos, err := readProtos(lines, index+1)
			if err != nil {
				return Record{}, err
			}
			record.Protos = append(record.Protos, protos...)
		}
	}
	if record.ServerCommit == "" {
		return Record{}, fault.Config(resource + " is missing server_commit")
	}
	if len(record.Protos) == 0 {
		return Record{}, fault.Config(resource + " lists no protos")
	}
	return record, nil
}

type line struct {
	indent  int
	content string
}

// significant drops blank and comment-only lines and measures indentation.
func significant(text string) []line {
	var lines []line
	for raw := range strings.SplitSeq(strings.ReplaceAll(text, "\r\n", "\n"), "\n") {
		stripped := strings.TrimRightFunc(stripComment(raw), unicode.IsSpace)
		content := strings.TrimSpace(stripped)
		if content == "" {
			continue
		}
		lines = append(lines, line{indent: len(stripped) - len(strings.TrimLeftFunc(stripped, unicode.IsSpace)), content: content})
	}
	return lines
}

// stripComment cuts a # comment that starts a line or follows whitespace,
// outside quotes.
func stripComment(raw string) string {
	var quote rune
	previous := ' '
	for index, char := range raw {
		switch {
		case quote != 0:
			if char == quote {
				quote = 0
			}
		case char == '"' || char == '\'':
			quote = char
		case char == '#' && (index == 0 || unicode.IsSpace(previous)):
			return raw[:index]
		}
		previous = char
	}
	return raw
}

func scalar(raw, where string) (string, error) {
	value := strings.TrimSpace(stripComment(raw))
	if value == "|" || value == ">" || slices.Contains([]string{"|-", ">-", "|+", ">+"}, prefix(value, 2)) {
		return "", fault.Config(resource + " uses an unsupported block scalar for " + where)
	}
	if len(value) >= 2 && value[0] == value[len(value)-1] && (value[0] == '"' || value[0] == '\'') {
		return value[1 : len(value)-1], nil
	}
	return value, nil
}

func prefix(s string, n int) string {
	if len(s) < n {
		return s
	}
	return s[:n]
}

func readProtos(lines []line, start int) ([]Proto, error) {
	base := lines[start-1].indent
	var body []line
	for _, l := range lines[start:] {
		if l.indent < base || (l.indent == base && !strings.HasPrefix(l.content, "-")) {
			break
		}
		body = append(body, l)
	}

	var (
		protos  []Proto
		current []line
		// Tracked apart from current: a dash with nothing under it still
		// starts an entry, which must be refused rather than dropped.
		started bool
	)
	flush := func() error {
		if !started {
			return nil
		}
		started = false
		fields, err := entryFields(current)
		current = nil
		if err != nil {
			return err
		}
		if len(fields) == 0 {
			return fault.Config(resource + " has an empty protos entry")
		}
		path, hasPath := fields["path"]
		sum, hasSum := fields["sha256"]
		if !hasPath || !hasSum {
			encoded, _ := json.Marshal(fields)
			return fault.Config(resource + " has a protos entry missing path or sha256: " + string(encoded))
		}
		protos = append(protos, Proto{Path: path, SHA256: sum})
		return nil
	}
	for _, l := range body {
		// Only a dash at the sequence's own indentation starts an entry; a
		// deeper one belongs to a nested list inside the current entry.
		rest, isEntry := strings.CutPrefix(l.content, "-")
		if !isEntry || l.indent != body[0].indent {
			current = append(current, l)
			continue
		}
		if err := flush(); err != nil {
			return nil, err
		}
		started = true
		if strings.TrimSpace(rest) != "" {
			// Seeded at the column the field starts in, so its siblings
			// measure against that rather than the dash.
			offset := len(rest) - len(strings.TrimLeftFunc(rest, unicode.IsSpace))
			current = append(current, line{indent: l.indent + 1 + offset, content: strings.TrimSpace(rest)})
		}
	}
	if err := flush(); err != nil {
		return nil, err
	}
	return protos, nil
}

// entryFields reads path and sha256 at the entry's own indentation only, so
// a nested block cannot answer for them.
func entryFields(body []line) (map[string]string, error) {
	fields := map[string]string{}
	if len(body) == 0 {
		return fields, nil
	}
	base := body[0].indent
	for _, l := range body {
		if l.indent != base {
			continue
		}
		key, value, ok := strings.Cut(l.content, ":")
		key = strings.TrimSpace(key)
		if !ok || (key != "path" && key != "sha256") {
			continue
		}
		if _, twice := fields[key]; twice {
			return nil, fault.Config(resource + " has a protos entry naming " + key + " twice")
		}
		parsed, err := scalar(value, key)
		if err != nil {
			return nil, err
		}
		fields[key] = parsed
	}
	return fields, nil
}
