package memcoai

import (
	"bufio"
	"bytes"
	"os"
	"regexp"
	"slices"
	"strings"
	"testing"

	"github.com/memcoai/memcoai/go/internal/config"
	"github.com/memcoai/memcoai/go/internal/fault"
	"github.com/memcoai/memcoai/go/internal/logging"
	"github.com/memcoai/memcoai/go/internal/memory"
	"github.com/memcoai/memcoai/go/internal/warn"
)

func TestTheLicenceMatchesTheRepositoryRoot(t *testing.T) {
	module, err := os.ReadFile("../LICENSE")
	if err != nil {
		t.Fatal(err)
	}
	root, err := os.ReadFile("../../LICENSE")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(module, root) {
		t.Fatal("go/LICENSE has drifted from the repository LICENSE; copy it across")
	}
}

// requirement reads one "key: value" line from a descriptor block.
func requirement(t *testing.T, block, key string) string {
	t.Helper()
	match := regexp.MustCompile(`(?m)^\s+` + regexp.QuoteMeta(key) + `:\s*(\S+)\s*$`).FindStringSubmatch(block)
	if match == nil {
		t.Fatalf("the descriptor declares no %s", key)
	}
	return strings.Trim(match[1], `"'`)
}

func TestGoModRequiresWhatTheGeneratedClientNeeds(t *testing.T) {
	descriptor, err := os.ReadFile("../internal/client/SDK_PROVENANCE.yaml")
	if err != nil {
		t.Fatal(err)
	}
	block := regexp.MustCompile(`(?ms)^requires:\n  go:\n((?:    .*\n)+)`).FindSubmatch(descriptor)
	if block == nil {
		t.Fatal("the descriptor has no requires.go block")
	}
	goMod, err := os.ReadFile("../go.mod")
	if err != nil {
		t.Fatal(err)
	}
	directive := regexp.MustCompile(`(?m)^go (\S+)$`).FindSubmatch(goMod)
	if directive == nil || string(directive[1]) != requirement(t, string(block[1]), "go") {
		t.Errorf("go directive %q, descriptor %q", directive, requirement(t, string(block[1]), "go"))
	}

	direct := map[string]string{}
	inside := false
	scanner := bufio.NewScanner(bytes.NewReader(goMod))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		switch {
		case line == "require (":
			inside = true
		case inside && line == ")":
			inside = false
		case inside && line != "" && !strings.HasSuffix(line, "// indirect"):
			fields := strings.Fields(line)
			direct[fields[0]] = fields[1]
		}
	}
	want := map[string]string{
		"google.golang.org/grpc":                    requirement(t, string(block[1]), "grpc"),
		"google.golang.org/protobuf":                requirement(t, string(block[1]), "protobuf"),
		"google.golang.org/genproto/googleapis/rpc": direct["google.golang.org/genproto/googleapis/rpc"],
	}
	if len(direct) != len(want) {
		t.Errorf("direct requirements %v", direct)
	}
	for module, version := range want {
		if direct[module] != version || version == "" {
			t.Errorf("%s: go.mod %q, want %q", module, direct[module], version)
		}
	}
}

func TestProvenanceNeedsNoConnection(t *testing.T) {
	first, err := ReadProvenance()
	if err != nil {
		t.Fatal(err)
	}
	recordsMemory := slices.ContainsFunc(first.Protos, func(p ProtoRecord) bool {
		return p.Path == "memcoai/memory/v1/memory.proto"
	})
	if !regexp.MustCompile(`^[0-9a-f]{40}$`).MatchString(first.ServerCommit) || !recordsMemory {
		t.Fatalf("got %+v", first)
	}
	first.Protos[0].Path = "changed"
	second, _ := ReadProvenance()
	if second.Protos[0].Path == "changed" {
		t.Fatal("a caller's edit reached the next caller")
	}
}

// The public constants restate values the internal packages act on, so the
// reference can show them; this keeps the two from drifting apart.
func TestPublicConstantsAreTheValuesTheSDKActsOn(t *testing.T) {
	pairs := [][2]any{
		{DefaultHost, config.DefaultHost},
		{DefaultPort, config.DefaultPort},
		{DefaultTimeout, config.DefaultTimeout},
		{LogEnv, logging.Env},
		{DeprecationWarningName, warn.DeprecationName},
		{NewMemory, memory.NewMemory},
		{string(SunsetClientVersion), fault.SunsetClientVersion},
		{string(SunsetAPIVersion), fault.SunsetAPIVersion},
		{string(ResourceExhaustedRateLimit), fault.ExhaustionRateLimit},
		{string(ResourceExhaustedQuota), fault.ExhaustionQuota},
		{string(ResourceExhaustedUnknown), fault.ExhaustionUnknown},
	}
	for _, pair := range pairs {
		if pair[0] != pair[1] {
			t.Errorf("public %v, internal %v", pair[0], pair[1])
		}
	}
}
