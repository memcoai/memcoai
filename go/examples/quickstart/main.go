// Quickstart connects, lists the domains the credential reaches, and runs one
// search.
//
// Run it with:
//
//	export MEMCO_API_TOKEN=...
//	go run ./quickstart
package main

import (
	"context"
	"errors"
	"log"
	"os"
	"os/signal"
	"strings"

	"github.com/memcoai/memcoai/go/memcoai"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	err := run(ctx, memcoai.Options{}, log.New(os.Stdout, "", 0))
	stop()
	if err != nil {
		log.Fatal(err)
	}
}

func run(ctx context.Context, opts memcoai.Options, out *log.Logger) (err error) {
	// Reads MEMCO_API_TOKEN and MEMCO_API_HOST, and sends nothing. Connect
	// probes the service's health endpoint and then lists domains, which proves
	// the credential and records the limits the service enforces — so a bad
	// endpoint or a bad token fails here rather than on the first real call.
	client, err := memcoai.NewClient(opts)
	if err != nil {
		return err
	}
	// Close waits for calls still in flight until ctx ends.
	defer func() { err = errors.Join(err, client.Close(ctx)) }()
	if err := client.Connect(ctx); err != nil {
		return err
	}

	listed, err := client.Memory.ListDomains(ctx)
	if err != nil {
		return err
	}
	printDomains(listed.Domains, out)
	if len(listed.Domains) == 0 {
		return nil
	}

	result, err := client.Memory.Search(ctx, "how should a client authenticate against the memory API",
		memcoai.SearchParams{Domain: listed.Domains[0].Slug})
	if err != nil {
		return err
	}
	printMemories(result.Memories, out)
	return nil
}

func printDomains(domains []memcoai.DomainEntry, out *log.Logger) {
	out.Printf("%d domain(s) available:", len(domains))
	for _, domain := range domains {
		summary, _, _ := strings.Cut(domain.Summary, "\n")
		out.Printf("  %-12s %s", domain.Slug, summary)
	}
	if len(domains) == 0 {
		out.Print("this credential reaches no domains")
	}
}

func printMemories(memories []memcoai.Memory, out *log.Logger) {
	out.Printf("\n%d memories for that query:", len(memories))
	for _, memory := range memories {
		out.Printf("  %s  served %dx", memory.Idx, memory.TimesServed)
		for _, insight := range memory.Insights {
			out.Printf("    %s  %s", insight.Updated, insight.Title)
		}
	}
}
