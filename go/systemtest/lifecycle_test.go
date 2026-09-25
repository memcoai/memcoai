//go:build systemtest

package systemtest

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/memcoai/memcoai/go/memcoai"
)

// A write is accepted asynchronously and only becomes searchable once
// ingestion has run, so every assertion about a memory existing, or having
// stopped existing, is a poll rather than a single call.
//
// The pause between attempts starts at a second and doubles up to 15s.
// Ingestion often lands within a few seconds, and a fixed 15s pause spent most
// of every wait idle. The cap is what keeps a slow wait within the service's
// search rate limit: at a fixed 5s, one domain's ingestion poll could spend 36
// searches, while this spends at most three more than a fixed 15s would.
const (
	ingestTimeout  = 180 * time.Second
	removalTimeout = 60 * time.Second
	firstPoll      = time.Second
	pollInterval   = 15 * time.Second
)

// Every command the service publishes, in one story, against the live service.
//
// The order is not a stylistic choice. A write is accepted asynchronously, so
// CreateMemory returns an operation id rather than a memory, and the memory it
// will become is not addressable until ingestion has run; reverting before
// then reports NOT_FOUND. So the memory has to be found by searching for it
// before it can be rated, fetched, enriched or removed.
func TestTheWholeLifecycleRunsAgainstTheLiveService(t *testing.T) {
	for _, domain := range domains(t) {
		t.Run(domain, func(t *testing.T) { lifecycle(t, domain) })
	}
}

// nonceFor returns a marker unique to this run, this domain and this SDK.
func nonceFor(domain string) string {
	tail := os.Getenv("GITHUB_RUN_ID")
	if tail != "" {
		attempt := os.Getenv("GITHUB_RUN_ATTEMPT")
		if attempt == "" {
			attempt = "1"
		}
		tail += "-" + attempt
	} else {
		random := make([]byte, 4)
		_, _ = rand.Read(random)
		tail = hex.EncodeToString(random)
	}
	return "gosys-" + domain + "-" + tail
}

type note struct{ query, title, content string }

// probe is the memory this run writes: true, substantive, and free of
// identifiers of the run.
//
// The run marker lives in the query and nowhere else. The query becomes the
// memory's intent, which comes back on every search result, so the test can
// still recognise its own memory while the insight the service evaluates is
// pure prose. The service's quality gate refuses content dominated by
// identifiers, and anything it reads as a report of work rather than durable
// knowledge; a refused write is accepted and then never becomes searchable,
// which surfaces here only as a search that never finds it. So the body says
// nothing about itself: every sentence is a fact about the SDK.
//
// The subject is specific to the Go SDK, so it is never read as the same
// knowledge the Python and Node.js suites write.
func probe(nonce string) note {
	return note{
		query: "Why does the Memco Go SDK name the dns scheme in the target it dials? " +
			"(system test " + nonce + ")",
		title: "Go SDK dial target",
		content: "grpc-go's NewClient parses its target as a URL and honours whichever " +
			"resolver scheme it names, so a host called unix with port 443 would be " +
			"dialled as a Unix socket rather than looked up in DNS. The Memco Go SDK " +
			"therefore always wraps the host and port in a dns:/// URL, which also " +
			"carries a bracketed IPv6 address with a zone through unchanged.\n\n" +
			"The same client turns off service configs published in DNS and installs " +
			"its own default instead, so the retry policy the SDK declares, covering " +
			"reads only and only when the service is unavailable, is the one the " +
			"channel applies.",
	}
}

// addition is the insight the enrich step adds to the memory above: a
// different fact about the same subject, so the service adds it rather than
// endorsing it as a duplicate.
var addition = note{
	title: "Go SDK exhausted retries",
	content: "When grpc-go gives up retrying a call, it returns an error of its own " +
		"that wraps the last status and says the retries were exhausted. Reading " +
		"the status straight off that error reports the wrapper's text rather than " +
		"the service's message, so the Memco Go SDK looks for the status anywhere " +
		"in the error chain instead, which keeps the service's own explanation and " +
		"error details on the error a caller receives.",
}

func lifecycle(t *testing.T, domain string) {
	nonce := nonceFor(domain)
	written := probe(nonce)
	t.Logf("probe %s", nonce)
	client := connected(t)
	ctx := t.Context()

	// The test reverts these itself and asserts on the outcome; this is the
	// safety net for a run that failed somewhere in between. A second revert
	// reports NOT_FOUND, which is not an error, so reverting everything again
	// costs an RPC and nothing else.
	var outstanding []string
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
		defer cancel()
		for _, operationID := range slices.Backward(outstanding) {
			if _, err := client.Memory.RevertMemory(ctx, operationID); err != nil {
				t.Logf("cleanup: reverting %s failed: %v", operationID, err)
			}
		}
	})

	// ListDomains ran to produce domain, and again in Connect, so it is not
	// called a third time here.
	session, err := client.Memory.StartSession(ctx, domain)
	if err != nil {
		t.Fatal(err)
	}
	if session.ID == "" {
		t.Fatal("the session has no id")
	}

	write, err := client.Memory.CreateMemory(ctx, memcoai.CreateMemoryParams{
		Query:     written.query,
		Title:     written.title,
		Content:   written.content,
		SessionID: session.ID,
		Source:    memcoai.DataSourceAgent,
	})
	if err != nil {
		t.Fatal(err)
	}
	if write.OperationID == "" {
		t.Fatal("the write was accepted without an operation id, so it cannot be reverted; " +
			"refusing to leave a memory behind in a live domain")
	}
	outstanding = append(outstanding, write.OperationID)
	t.Logf("created, operation %s", write.OperationID)

	memory, insight := searchUntilFound(t, client, session.ID, written.query, nonce, written.title)
	t.Logf("found as %s, insight %s", memory.Idx, insight.Idx)

	feedback, err := client.Memory.ShareFeedback(ctx, session.ID, []memcoai.FeedbackRating{
		{Idx: insight.Idx, Relevant: true, Correct: true},
	})
	if err != nil {
		t.Fatal(err)
	}
	if !slices.ContainsFunc(feedback.Entries, func(e memcoai.FeedbackEntry) bool { return e.Idx == insight.Idx }) {
		t.Fatalf("no feedback entry for %s in %+v", insight.Idx, feedback.Entries)
	}

	fetched, err := client.Memory.GetMemory(ctx, memory.Idx)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := insightTitled(*fetched, written.title); fetched.Idx != memory.Idx || !ok {
		t.Fatalf("fetched %+v", fetched)
	}

	// An enrichment is a second write against the same memory, with an
	// operation id of its own.
	enrichment, err := client.Memory.EnrichMemory(ctx, memcoai.EnrichMemoryParams{
		MemoryIdx: memory.Idx,
		SessionID: session.ID,
		Title:     addition.title,
		Content:   addition.content,
	})
	if err != nil {
		t.Fatal(err)
	}
	if enrichment.OperationID == "" {
		t.Fatal("the enrichment was accepted without an operation id")
	}
	outstanding = append(outstanding, enrichment.OperationID)
	added := searchUntilTitled(t, client, written.query, domain, nonce, addition.title)
	t.Logf("enriched, insight %s", added.Idx)

	undoAddition, err := client.Memory.RevertMemory(ctx, enrichment.OperationID)
	if err != nil {
		t.Fatal(err)
	}
	if undoAddition.Outcome != memcoai.RevertOutcomeAdditionRemoved {
		t.Fatalf("reverting the enrichment reported %s, not ADDITION_REMOVED", undoAddition.Outcome)
	}
	// The outcome is what the service reported; this is what it did. The
	// memory the addition joined is still there, with its original insight.
	searchUntilTitled(t, client, written.query, domain, nonce, written.title)

	// Removing the original insight takes its memory with it: it is the last
	// one the memory holds.
	undoMemory, err := client.Memory.RevertMemory(ctx, write.OperationID)
	if err != nil {
		t.Fatal(err)
	}
	if undoMemory.Outcome != memcoai.RevertOutcomeMemoryRemoved {
		t.Fatalf("reverting the write reported %s, not MEMORY_REMOVED. MERGED or ADDITION_REMOVED "+
			"means the probe was folded into an existing memory. The insight carries no run "+
			"marker, so two runs of this SDK overlapping would write identical content and "+
			"collide; the CI job holds a per-language concurrency group to keep one in flight "+
			"at a time.", undoMemory.Outcome)
	}
	t.Log("reverted")

	searchUntilAbsent(t, client, written.query, domain, nonce)
	getUntilGone(t, client, memory.Idx)
	t.Log("gone")
}

func insightTitled(memory memcoai.Memory, title string) (memcoai.Insight, bool) {
	for _, insight := range memory.Insights {
		if insight.Title == title {
			return insight, true
		}
	}
	return memcoai.Insight{}, false
}

// isOurs reports whether this run wrote memory. The marker is in the intent
// because it cannot be in the insight: the query passed to CreateMemory
// becomes the memory's intent, and intents come back on every search result.
func isOurs(memory memcoai.Memory, nonce string) bool {
	return slices.ContainsFunc(memory.Intents, func(intent string) bool { return strings.Contains(intent, nonce) })
}

// resolved returns memory with its insights, fetching them if the search
// withheld them. Within one session a memory a previous search already
// delivered comes back as a bare reference, so without this a memory
// delivered once, before its insight was attached, would never match again.
func resolved(ctx context.Context, client *memcoai.Client, memory memcoai.Memory) (memcoai.Memory, error) {
	if len(memory.Insights) > 0 || memory.Reference == "" {
		return memory, nil
	}
	fetched, err := client.Memory.GetMemory(ctx, memory.Idx)
	if err != nil {
		return memcoai.Memory{}, err
	}
	return *fetched, nil
}

// searchUntilFound polls the search until this run's memory comes back. Every
// returned memory is examined: the probe is brand new and competing with
// whatever else the domain holds, so its rank is not something to assume.
func searchUntilFound(t *testing.T, client *memcoai.Client, sessionID, query, nonce, title string) (memcoai.Memory, memcoai.Insight) {
	t.Helper()
	var memory memcoai.Memory
	var insight memcoai.Insight
	seen := 0
	poll(t, ingestTimeout, "waiting for ingestion", func(ctx context.Context) (bool, error) {
		result, err := client.Memory.Search(ctx, query, memcoai.SearchParams{SessionID: sessionID})
		if err != nil {
			return false, err
		}
		seen = len(result.Memories)
		for _, candidate := range result.Memories {
			resolvedMemory, err := resolved(ctx, client, candidate)
			if err != nil {
				return false, err
			}
			if !isOurs(resolvedMemory, nonce) {
				continue
			}
			if found, ok := insightTitled(resolvedMemory, title); ok {
				memory, insight = resolvedMemory, found
				return true, nil
			}
		}
		return false, nil
	}, func() string {
		return fmt.Sprintf("the memory never became searchable within %s (last search returned %d memories, "+
			"none whose intent names %s). Three things can cause this. The write may have been rejected "+
			"downstream by the quality gate, in which case it never becomes searchable at all. Ingestion "+
			"may simply be slower than the budget. Or the service may not carry a newly created memory's "+
			"own query in its intents, which is the assumption this suite rests on to recognise its own "+
			"memory without an identifier in the insight.", ingestTimeout, seen, nonce)
	})
	return memory, insight
}

// searchUntilTitled polls a session-less search until this run's memory
// carries an insight with this title.
//
// Not GetMemory on the idx the earlier search returned: an idx is bound to the
// search that issued it and resolves to that snapshot, so an insight added
// afterwards never appears through it. And no session, because within one a
// memory already returned comes back as a bare reference with no insights.
func searchUntilTitled(t *testing.T, client *memcoai.Client, query, domain, nonce, title string) memcoai.Insight {
	t.Helper()
	var insight memcoai.Insight
	poll(t, ingestTimeout, "waiting for the enrichment to be ingested", func(ctx context.Context) (bool, error) {
		result, err := client.Memory.Search(ctx, query, memcoai.SearchParams{Domain: domain})
		if err != nil {
			return false, err
		}
		for _, candidate := range result.Memories {
			if !isOurs(candidate, nonce) {
				continue
			}
			if found, ok := insightTitled(candidate, title); ok {
				insight = found
				return true, nil
			}
		}
		return false, nil
	}, func() string {
		return fmt.Sprintf("no insight titled %q appeared within %s. Either ingestion is slower than the "+
			"budget, or the service endorsed the addition as a duplicate of an insight the memory "+
			"already held instead of adding it as a new one.", title, ingestTimeout)
	})
	return insight
}

// searchUntilAbsent polls a session-less search until this run's memory is no
// longer returned.
func searchUntilAbsent(t *testing.T, client *memcoai.Client, query, domain, nonce string) {
	t.Helper()
	poll(t, removalTimeout, "waiting for the removal to take effect", func(ctx context.Context) (bool, error) {
		result, err := client.Memory.Search(ctx, query, memcoai.SearchParams{Domain: domain})
		if err != nil {
			return false, err
		}
		return !slices.ContainsFunc(result.Memories, func(m memcoai.Memory) bool { return isOurs(m, nonce) }), nil
	}, func() string {
		return fmt.Sprintf("the memory was still returned by search %s after a revert reported MEMORY_REMOVED", removalTimeout)
	})
}

// getUntilGone polls GetMemory until it reports the memory is gone.
func getUntilGone(t *testing.T, client *memcoai.Client, idx string) {
	t.Helper()
	poll(t, removalTimeout, "waiting for the removal to take effect", func(ctx context.Context) (bool, error) {
		_, err := client.Memory.GetMemory(ctx, idx)
		var notFound *memcoai.NotFoundError
		if errors.As(err, &notFound) {
			return true, nil
		}
		return false, err
	}, func() string {
		return fmt.Sprintf("%s was still retrievable %s after a revert reported MEMORY_REMOVED", idx, removalTimeout)
	})
}
