// Search_and_rate runs the full read loop: open a session, search under it,
// and rate what came back.
//
// Rating is the part people skip, and it is the only signal the service gets
// about whether a result actually answered the question. Searches made under
// one session are recorded as a series, which is what makes them rateable
// afterwards.
//
// Run it with:
//
//	export MEMCO_API_TOKEN=...
//	go run ./search_and_rate
package main

import (
	"context"
	"errors"
	"log"
	"os"
	"os/signal"

	"github.com/memcoai/memcoai/go/memcoai"
)

const domain = "coding"

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	err := run(ctx, memcoai.Options{}, log.New(os.Stdout, "", 0))
	stop()
	if err != nil {
		log.Fatal(err)
	}
}

func run(ctx context.Context, opts memcoai.Options, out *log.Logger) (err error) {
	client, err := memcoai.NewClient(opts)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, client.Close(ctx)) }()
	if err := client.Connect(ctx); err != nil {
		return err
	}

	// A session ties related searches together, and every call made through
	// it carries its id, so nothing later can drop the handle. Use one session
	// for every search made for the same task, and for the ratings afterwards.
	session, err := client.Memory.StartSession(ctx, domain)
	if err != nil {
		return err
	}
	out.Printf("session %s", session.ID)

	result, err := search(ctx, session, out)
	if err != nil {
		return err
	}
	if len(result.Memories) == 0 {
		out.Print("nothing matched; try a broader query")
		return nil
	}
	ratings := review(result.Memories, out)
	if len(ratings) == 0 {
		// Possible even with memories in hand: one that only references an
		// earlier result in this session carries no insights of its own, and
		// ShareFeedback refuses an empty batch before sending it.
		out.Print("nothing to rate")
		return nil
	}
	return rate(ctx, session, ratings, out)
}

// search runs the one search this example rates.
func search(ctx context.Context, session *memcoai.Session, out *log.Logger) (*memcoai.SearchResult, error) {
	result, err := session.Search(ctx, "how does gRPC health checking interact with an auth interceptor",
		memcoai.ScopedSearchParams{
			// Which tag types narrow results and which merely boost them is
			// per-domain; ListDomains describes them. A wrong filtering tag
			// returns nothing at all, so start without tags if unsure.
			Tags: []memcoai.Tag{{Type: "language", Value: "go", Version: "1.25"}},
		})
	if err != nil {
		return nil, err
	}
	if result.Notice != "" {
		out.Printf("notice: %s", result.Notice)
	}
	return result, nil
}

// review prints each insight a search returned, and rates it.
func review(memories []memcoai.Memory, out *log.Logger) []memcoai.FeedbackRating {
	var ratings []memcoai.FeedbackRating
	for _, memory := range memories {
		for _, insight := range memory.Insights {
			out.Printf("\n%s  (updated %s)", insight.Title, insight.Updated)
			out.Printf("  endorsed %d / disputed %d", insight.Endorsed, insight.Disputed)
			out.Printf("  %s", preview(insight.Content, 200))

			// Handles are opaque and must be copied exactly from a result;
			// they cannot be constructed by hand.
			ratings = append(ratings, memcoai.FeedbackRating{
				Idx:      insight.Idx,
				Relevant: true,
				Correct:  true,
				Comment:  "answered the question directly",
			})
		}
	}
	return ratings
}

// rate sends the ratings, and prints any advice they earned.
func rate(ctx context.Context, session *memcoai.Session, ratings []memcoai.FeedbackRating, out *log.Logger) error {
	recorded, err := session.ShareFeedback(ctx, ratings)
	if err != nil {
		return err
	}
	out.Printf("\nrecorded %d rating(s)", len(recorded.Entries))
	for _, entry := range recorded.Entries {
		if entry.Advice != "" {
			out.Printf("  %s: %s", entry.Idx, entry.Advice)
		}
	}
	return nil
}

// preview returns text's first limit characters, marking a cut.
func preview(text string, limit int) string {
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	return string(runes[:limit]) + "..."
}
