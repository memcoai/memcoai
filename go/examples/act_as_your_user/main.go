// Act_as_your_user searches and writes as one of your users, in a session that
// carries their key.
//
// Opening a session with memcoai.ExternalID mints a short-lived key acting as
// that user, and every call through the session carries it, so what the
// session finds and writes is that user's, bounded by the network they are in.
// The key renews itself while the session is open, and is ended when the
// session closes.
//
// Run it with:
//
//	export MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=...
//	go run ./act_as_your_user
package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"log"
	"os"
	"os/signal"
	"time"

	"github.com/memcoai/memcoai/go/memcoai"
)

const (
	domain   = "coding"
	question = "how should a Go program act for one of its own users"
	// How often to try reverting while the write is ingested.
	revertAttempts = 60
)

// revertPause is the wait between revert attempts.
var revertPause = time.Second

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
	suffix := make([]byte, 4)
	_, _ = rand.Read(suffix)
	tag := "goex-" + hex.EncodeToString(suffix) // keeps this run's names apart from any other's

	// The user to act as, placed in a customer network of their own; see
	// map_your_users. Both are removed again at the end, even after an
	// interrupt between calls.
	roots, err := client.Networks.List(ctx, memcoai.ListNetworksParams{ParentID: "root", Domain: domain})
	if err != nil {
		return err
	}
	if len(roots.Networks) == 0 {
		return fmt.Errorf("the organisation has no root network in %s", domain)
	}
	network, err := client.Networks.Create(ctx, memcoai.CreateNetworkParams{
		Name: tag + " Acme Corp", ParentID: roots.Networks[0].ID, Scope: "customer",
	})
	if err != nil {
		return err
	}
	defer func() {
		ctx, cancel := cleanup(ctx)
		defer cancel()
		_, deleted := client.Networks.Delete(ctx, network.ID)
		err = errors.Join(err, deleted)
		out.Print("removed again")
	}()
	user, err := client.Users.Create(ctx, tag+"-alice", memcoai.CreateUserParams{Roles: []string{"creator"}, Name: "Alice Andersson"})
	if err != nil {
		return err
	}
	defer func() {
		ctx, cancel := cleanup(ctx)
		defer cancel()
		err = errors.Join(err, client.Users.Delete(ctx, user.ExternalID))
	}()
	if _, err := client.Networks.AddMember(ctx, network.ID, user.ID, memcoai.AddMemberParams{}); err != nil {
		return err
	}

	session, err := client.Memory.StartSession(ctx, domain, memcoai.ExternalID(user.ExternalID))
	if err != nil {
		return err
	}
	// Closing ends the key. A session left unclosed has its key ended once it
	// is garbage, but closing ends it now.
	defer session.Close(ctx)

	result, err := session.Search(ctx, question, memcoai.ScopedSearchParams{})
	if err != nil {
		return err
	}
	out.Printf("%s sees %d memories", user.ExternalID, len(result.Memories))

	written, err := session.CreateMemory(ctx, memcoai.ScopedCreateMemoryParams{
		Query: question + " (" + tag + ")",
		Title: "Act for a user through a session opened with their external id",
		Content: "A Go program acts for one of its own users by opening a memory session with the " +
			"ExternalID option of the Memco Go SDK. The session carries a short-lived key acting as that " +
			"user, so what it finds and writes is bounded by the network the user was placed in, and " +
			"deferring the session's Close ends the key as soon as the function returns.",
	})
	if err != nil {
		return err
	}
	out.Printf("written as %s: %s", user.ExternalID, written.OperationID)
	if written.OperationID == "" {
		return nil
	}
	reverted, err := revert(ctx, session, written.OperationID)
	if err != nil {
		return err
	}
	out.Printf("revert: %s", reverted.Outcome)
	return nil
}

// revert undoes a write through the session of the user who made it, which a
// revert needs. The write is ingested asynchronously, and until it has been,
// reverting reports NOT_FOUND rather than removing it.
func revert(ctx context.Context, session *memcoai.Session, operationID string) (*memcoai.RevertResult, error) {
	for attempt := 1; ; attempt++ {
		reverted, err := session.RevertMemory(ctx, operationID)
		if err != nil || reverted.Outcome != memcoai.RevertOutcomeNotFound || attempt == revertAttempts {
			return reverted, err
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(revertPause):
		}
	}
}

// cleanup is a context for removing what the run created: one that outlives
// an interrupt, with a minute of its own.
func cleanup(ctx context.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.WithoutCancel(ctx), time.Minute)
}
