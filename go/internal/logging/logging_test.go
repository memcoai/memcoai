package logging

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"os"
	"regexp"
	"strings"
	"testing"

	"github.com/memcoai/memcoai/go/internal/fault"
)

// captured points os.Stderr at a file for the rest of the test.
func captured(t *testing.T) func() string {
	t.Helper()
	file, err := os.CreateTemp(t.TempDir(), "stderr")
	if err != nil {
		t.Fatal(err)
	}
	original := os.Stderr
	os.Stderr = file
	t.Cleanup(func() {
		os.Stderr = original
		_ = file.Close()
		Configure(func(string) string { return "" })
	})
	return func() string {
		data, err := os.ReadFile(file.Name())
		if err != nil {
			t.Fatal(err)
		}
		return string(data)
	}
}

func env(value string) func(string) string {
	return func(name string) string {
		if name == Env {
			return value
		}
		return ""
	}
}

func TestTheDefaultLevelIsInfo(t *testing.T) {
	read := captured(t)
	Configure(env(""))
	Default().Debug("hidden")
	Default().Info("shown")
	out := read()
	if strings.Contains(out, "hidden") || !strings.Contains(out, "shown") {
		t.Fatalf("got %q", out)
	}
}

func TestMemcoLogChoosesTheLevel(t *testing.T) {
	cases := map[string][]string{
		"critical": {"CRITICAL"},
		"error":    {"CRITICAL", "ERROR"},
		"warning":  {"CRITICAL", "ERROR", "WARNING"},
		"info":     {"CRITICAL", "ERROR", "WARNING", "INFO"},
		"debug":    {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"},
		" DeBuG ":  {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"},
		"none":     {},
	}
	for setting, want := range cases {
		t.Run(setting, func(t *testing.T) {
			read := captured(t)
			Configure(env(setting))
			log := Default()
			ctx := context.Background()
			log.Log(ctx, LevelCritical, "c")
			log.Error("e")
			log.Warn("w")
			log.Info("i")
			log.Debug("d")
			var got []string
			for _, line := range strings.Split(strings.TrimSpace(read()), "\n") {
				if match := regexp.MustCompile(`level=(\S+)`).FindStringSubmatch(line); match != nil {
					got = append(got, match[1])
				}
			}
			if strings.Join(got, ",") != strings.Join(want, ",") {
				t.Fatalf("got %v, want %v", got, want)
			}
		})
	}
}

func TestAnUnrecognisedMemcoLogWarnsAndKeepsInfo(t *testing.T) {
	for _, setting := range []string{"loud", "warn", "fatal", "trace", "off"} {
		t.Run(setting, func(t *testing.T) {
			read := captured(t)
			Configure(env(setting))
			Default().Debug("hidden")
			Default().Info("shown")
			out := read()
			if !strings.Contains(out, "level=WARNING") || !strings.Contains(out, `MEMCO_LOG ignored: \"`+setting+`\" is not a log level`) {
				t.Errorf("no warning in %q", out)
			}
			if strings.Contains(out, "hidden") || !strings.Contains(out, "shown") {
				t.Errorf("level is not info: %q", out)
			}
		})
	}
}

func TestSetLevelRefusesWhatIsNotALevel(t *testing.T) {
	t.Cleanup(func() { Configure(env("")) })
	err := SetLevel("loud")
	var f *fault.Error
	if !errors.As(err, &f) || f.Kind != fault.KindConfig {
		t.Fatalf("got %v", err)
	}
	want := `"loud" is not a log level; use one of critical, debug, error, info, warning, or none to silence`
	if f.Detail != want {
		t.Fatalf("got %q, want %q", f.Detail, want)
	}
	if err := SetLevel("  Warning "); err != nil {
		t.Fatalf("a padded, capitalised level is refused: %v", err)
	}
}

func TestNoneSilencesEvenCritical(t *testing.T) {
	read := captured(t)
	if err := SetLevel("critical"); err != nil {
		t.Fatal(err)
	}
	Default().Log(context.Background(), LevelCritical, "loud")
	if !strings.Contains(read(), "loud") {
		t.Fatal("control: critical is not written at critical")
	}
	if err := SetLevel("none"); err != nil {
		t.Fatal(err)
	}
	Default().Log(context.Background(), LevelCritical, "quiet")
	if strings.Contains(read(), "quiet") {
		t.Fatal("none let a critical record through")
	}
}

func TestNamedSaysWhichAreaSpoke(t *testing.T) {
	var buffer bytes.Buffer
	log := Named(slog.New(slog.NewTextHandler(&buffer, nil)), "client")
	log.Info("hello")
	if !strings.Contains(buffer.String(), "logger=memco.client") {
		t.Fatalf("got %q", buffer.String())
	}
}

func TestOutputFollowsStderrReassignedLater(t *testing.T) {
	Default() // resolved before the reassignment below
	read := captured(t)
	Configure(env(""))
	Default().Info("after")
	if !strings.Contains(read(), "after") {
		t.Fatal("the record went to the stderr the logger was built with")
	}
}

func TestAFailingStderrNeverPanics(t *testing.T) {
	read := captured(t)
	Configure(env(""))
	Default().Info("first")
	if !strings.Contains(read(), "first") {
		t.Fatal("control: a working stderr is not written")
	}
	closed, err := os.CreateTemp(t.TempDir(), "closed")
	if err != nil {
		t.Fatal(err)
	}
	_ = closed.Close()
	os.Stderr = closed
	Default().Info("lost") // must not panic
}

func TestTheDedicatedLoggerIsNotTheProcessDefault(t *testing.T) {
	if Default().Handler() == slog.Default().Handler() {
		t.Fatal("the SDK writes through slog.Default")
	}
}

func TestAnAttributeNamedLevelIsLeftAlone(t *testing.T) {
	attr := slog.String(slog.LevelKey, "not a level")
	if got := labelled(nil, attr); !got.Equal(attr) {
		t.Fatalf("got %v", got)
	}
	if got := labelled(nil, slog.Any(slog.LevelKey, slog.LevelWarn)); got.Value.String() != "WARNING" {
		t.Fatalf("control: got %v", got)
	}
}
