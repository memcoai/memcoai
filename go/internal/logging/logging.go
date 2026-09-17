// Package logging is the SDK's own slog logger: a stderr handler whose level
// MEMCO_LOG, a client option or SetLevel decides.
package logging

import (
	"fmt"
	"log/slog"
	"math"
	"os"
	"strings"

	"github.com/memcoai/memcoai/go/internal/fault"
)

// Env names the environment variable read at start-up.
const Env = "MEMCO_LOG"

// LevelCritical sits above slog's error level.
const LevelCritical = slog.Level(12)

// silent is above every level, so none is a level rather than a flag.
const silent = slog.Level(math.MaxInt32)

const defaultLevel = "info"

// No aliases: warn, fatal, trace and off name levels elsewhere, and MEMCO_LOG
// must mean the same thing to every SDK that reads it.
var levels = map[string]slog.Level{
	"critical": LevelCritical,
	"error":    slog.LevelError,
	"warning":  slog.LevelWarn,
	"info":     slog.LevelInfo,
	"debug":    slog.LevelDebug,
	"none":     silent,
}

var (
	level  slog.LevelVar
	logger = slog.New(slog.NewTextHandler(stderr{}, &slog.HandlerOptions{
		Level:       &level,
		ReplaceAttr: labelled,
	}))
)

func init() { Configure(os.Getenv) }

// stderr writes to whatever os.Stderr is at the time, so a process that
// redirects it after start-up is still followed. A failed write is dropped:
// a log record must never break the call that made it.
type stderr struct{}

func (stderr) Write(p []byte) (int, error) {
	_, _ = os.Stderr.Write(p)
	return len(p), nil
}

// labelled spells levels the way the other SDKs do.
func labelled(_ []string, attr slog.Attr) slog.Attr {
	if attr.Key != slog.LevelKey {
		return attr
	}
	level, ok := attr.Value.Any().(slog.Level)
	if !ok {
		return attr
	}
	switch level {
	case LevelCritical:
		attr.Value = slog.StringValue("CRITICAL")
	case slog.LevelWarn:
		attr.Value = slog.StringValue("WARNING")
	}
	return attr
}

// Default is the SDK's dedicated logger.
func Default() *slog.Logger { return logger }

// SetLevel sets the dedicated logger's level by name.
func SetLevel(name string) error {
	resolved, ok := levels[strings.ToLower(strings.TrimSpace(name))]
	if !ok {
		return fault.Config(fmt.Sprintf(
			"%q is not a log level; use one of critical, debug, error, info, warning, or none to silence", name))
	}
	level.Set(resolved)
	return nil
}

// Configure applies the level named by env, falling back to info. A bad value
// warns rather than fails: an environment variable is ambient.
func Configure(getenv func(string) string) {
	setting := strings.TrimSpace(getenv(Env))
	if setting == "" {
		setting = defaultLevel
	}
	if err := SetLevel(setting); err != nil {
		_ = SetLevel(defaultLevel)
		logger.Warn(Env + " ignored: " + err.Error())
	}
}

// Named tags a logger with the SDK area it speaks for.
func Named(log *slog.Logger, area string) *slog.Logger {
	return log.With("logger", "memco."+area)
}
