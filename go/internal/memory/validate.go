package memory

import (
	"fmt"
	"log/slog"
	"strings"
	"unicode/utf8"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/fault"
)

// NewMemory opens a new memory where an idx is expected.
const NewMemory = "new"

// CountCharacters counts code points, as the service does.
func CountCharacters(s string) int { return utf8.RuneCountInString(s) }

func present(value, field string) error {
	if strings.TrimSpace(value) == "" {
		return fault.Invalid(field + " must not be empty")
	}
	return nil
}

// checkScope needs a domain or a session; one that was given but is blank is
// refused rather than read as absent.
func checkScope(domain, sessionID string) error {
	if domain == "" && sessionID == "" {
		return fault.Invalid("pass a domain or a session_id: a request needs one of them to name a domain")
	}
	if domain != "" {
		if err := present(domain, "domain"); err != nil {
			return err
		}
	}
	if sessionID != "" {
		return present(sessionID, "session_id")
	}
	return nil
}

// A cap below one was not reported (the wire says zero; a negative one is
// malformed), and checks nothing.

func within(value, field string, limit int32) error {
	if limit < 1 {
		return nil
	}
	if n := CountCharacters(value); n > int(limit) {
		return fault.Invalid(fmt.Sprintf("%s is %d characters, which exceeds the limit of %d", field, n, limit))
	}
	return nil
}

func count(n int, field string, limit int32) error {
	if limit > 0 && n > int(limit) {
		return fault.Invalid(fmt.Sprintf("%s has %d entries, which exceeds the limit of %d", field, n, limit))
	}
	return nil
}

func together(title, content, field string, limit int32) error {
	if limit < 1 {
		return nil
	}
	if n := CountCharacters(title) + CountCharacters(content); n > int(limit) {
		return fault.Invalid(fmt.Sprintf("%s are %d characters together, which exceeds the combined limit of %d", field, n, limit))
	}
	return nil
}

// trimmed keeps the first entries a trimming cap allows. The caller's data is
// dropped invisibly, so the record is the only trace of it.
func trimmed[T any](values []T, limit int32, field string, log *slog.Logger) []T {
	if limit < 1 || len(values) <= int(limit) {
		return values
	}
	log.Debug("trimmed", "field", field, "from", len(values), "to", limit)
	return values[:limit:limit]
}

// checkTags refuses a blank tag: one that filters would narrow a search to
// nothing.
func checkTags(tags []*memoryv1.Tag, field string) error {
	for _, tag := range tags {
		if err := present(tag.GetType(), field+" type"); err != nil {
			return err
		}
		if err := present(tag.GetValue(), field+" value"); err != nil {
			return err
		}
	}
	return nil
}
