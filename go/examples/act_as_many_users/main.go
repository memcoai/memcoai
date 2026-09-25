// Act_as_many_users acts as several of your users at once, each in a session
// of their own.
//
// One client holds one connection, and each session carries its own user's
// key, so sessions for different users run side by side from as many
// goroutines, never sharing a credential. Each key is ended as its session
// closes.
//
// Run it with:
//
//	export MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=...
//	go run ./act_as_many_users
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
	"sync"
	"time"

	"github.com/memcoai/memcoai/go/memcoai"
)

const (
	domain   = "coding"
	question = "how should a Go program act for one of its own users"
)

var handles = []string{"alice", "bob", "carol"}

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

	// Three users to act as, in one customer network; see map_your_users.
	// Removed again at the end, even after an interrupt between calls.
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
	var users []*memcoai.ExternalUser
	defer func() {
		// A minute of its own, whatever became of ctx.
		cleanup, cancel := context.WithTimeout(context.WithoutCancel(ctx), time.Minute)
		defer cancel()
		for _, user := range users {
			err = errors.Join(err, client.Users.Delete(cleanup, user.ExternalID))
		}
		_, deleted := client.Networks.Delete(cleanup, network.ID)
		err = errors.Join(err, deleted)
		out.Print("removed again")
	}()
	for _, handle := range handles {
		user, err := client.Users.Create(ctx, tag+"-"+handle, memcoai.CreateUserParams{Roles: []string{"reader"}})
		if err != nil {
			return err
		}
		users = append(users, user)
		if _, err := client.Networks.AddMember(ctx, network.ID, user.ID, memcoai.AddMemberParams{}); err != nil {
			return err
		}
	}

	// All three sessions are open at once, each under its own key.
	lines := make([]string, len(users))
	failures := make([]error, len(users))
	var wg sync.WaitGroup
	for i, user := range users {
		wg.Go(func() { lines[i], failures[i] = ask(ctx, client, user.ExternalID) })
	}
	wg.Wait()
	for _, line := range lines {
		if line != "" {
			out.Print(line)
		}
	}
	return errors.Join(failures...)
}

// ask searches as one user, in a session of their own.
func ask(ctx context.Context, client *memcoai.Client, externalID string) (string, error) {
	session, err := client.Memory.StartSession(ctx, domain, memcoai.ExternalID(externalID))
	if err != nil {
		return "", err
	}
	defer session.Close(ctx) // ends this user's key
	result, err := session.Search(ctx, question, memcoai.ScopedSearchParams{})
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("%s sees %d memories", externalID, len(result.Memories)), nil
}
