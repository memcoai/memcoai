// Package warn surfaces once-per-process notices through the SDK's logger.
package warn

import (
	"log/slog"
	"sync"
)

// DeprecationName tags a deprecation notice from the service.
const DeprecationName = "MemcoDeprecationWarning"

var (
	mu   sync.Mutex
	seen = map[[2]string]bool{}
)

// Deprecation surfaces the service's deprecation message once per message.
// The message is relayed as written; only the sunset date is added.
func Deprecation(log *slog.Logger, message, sunset string) {
	if message == "" || !first(DeprecationName, message) {
		return
	}
	text := message
	if sunset != "" {
		text += " (stops working on " + sunset + ")"
	}
	log.Warn(text, "warning", DeprecationName)
}

// Once writes a warning the first time name and message are seen together.
func Once(log *slog.Logger, name, message string) {
	if first(name, message) {
		log.Warn(message, "warning", name)
	}
}

func first(name, message string) bool {
	mu.Lock()
	defer mu.Unlock()
	key := [2]string{name, message}
	if seen[key] {
		return false
	}
	seen[key] = true
	return true
}

// Reset forgets what was surfaced.
func Reset() {
	mu.Lock()
	defer mu.Unlock()
	clear(seen)
}
