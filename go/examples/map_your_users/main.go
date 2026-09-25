// Map_your_users maps a customer's organisation into Memco: a company network,
// a network under it for each client project, and its engineers.
//
// A consulting company becomes a customer network of its own, and each of its
// client projects a customer network under it. Its engineers become external
// users, with no sign-in of their own: your API client acts for them. Each is
// placed in exactly one network per domain:
//
//   - placed in a project, an engineer sees that project's knowledge and the
//     company's, and never another project's;
//   - placed in the company network, they see only what the company shares.
//
// Moving an engineer to another project is refused unless the move is asked
// for. Everything created here is removed again at the end; a real
// integration keeps it.
//
// Run it with:
//
//	export MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=...
//	go run ./map_your_users
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
	"strings"
	"time"

	"github.com/memcoai/memcoai/go/memcoai"
)

const (
	domain  = "coding"
	company = "Acme Consulting"
)

var projects = []struct{ key, title string }{
	{"billing", "Billing platform"},
	{"mobile", "Mobile app"},
}

// Each engineer by your own id for them, which Memco keeps as their external
// id so you never need to store one of Memco's, and where they work.
var engineers = []struct{ handle, name, where string }{
	{"maria", "Maria Lind", "company"}, // the practice lead, across every project
	{"alice", "Alice Andersson", "billing"},
	{"bob", "Bob Berg", "billing"},
	{"carol", "Carol Chen", "mobile"},
	{"dave", "Dave Dahl", "mobile"},
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	err := run(ctx, memcoai.Options{}, log.New(os.Stdout, "", 0))
	stop()
	if err != nil {
		log.Fatal(err)
	}
}

func run(ctx context.Context, opts memcoai.Options, out *log.Logger) (err error) {
	// Reads MEMCO_CLIENT_ID and MEMCO_CLIENT_SECRET, exchanges them for a
	// token, and renews that token for as long as the client is open.
	client, err := memcoai.NewClient(opts)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, client.Close(ctx)) }()
	if err := client.Connect(ctx); err != nil {
		return err
	}

	// Keeps this run's names apart from any other run's, and from what other
	// programs create in the same organisation.
	suffix := make([]byte, 4)
	_, _ = rand.Read(suffix)
	tag := "goex-" + hex.EncodeToString(suffix)

	// The company network hangs under the domain's root network, and each
	// project under the company, taking its domain from it.
	roots, err := client.Networks.List(ctx, memcoai.ListNetworksParams{ParentID: "root", Domain: domain})
	if err != nil {
		return err
	}
	if len(roots.Networks) == 0 {
		return fmt.Errorf("the organisation has no root network in %s", domain)
	}
	root := roots.Networks[0]

	var created []memcoai.Network
	var users []*memcoai.ExternalUser
	defer func() { err = errors.Join(err, cleanUp(ctx, client, created, users, out)) }()
	networks := map[string]memcoai.Network{}
	place := func(key string, params memcoai.CreateNetworkParams) error {
		network, err := client.Networks.Create(ctx, params)
		if err != nil {
			return err
		}
		created = append(created, *network)
		networks[key] = *network
		return nil
	}
	if err := place("company", memcoai.CreateNetworkParams{
		Name: tag + " " + company, ParentID: root.ID, Scope: "customer",
		Description: "Practice that applies to every client project",
	}); err != nil {
		return err
	}
	for _, project := range projects {
		if err := place(project.key, memcoai.CreateNetworkParams{
			Name: tag + " " + company + " / " + project.title, ParentID: networks["company"].ID, Scope: "customer",
		}); err != nil {
			return err
		}
	}

	byHandle := map[string]*memcoai.ExternalUser{}
	for _, engineer := range engineers {
		externalID := tag + "-" + engineer.handle
		user, err := client.Users.Create(ctx, externalID, memcoai.CreateUserParams{Roles: []string{"creator"}, Name: engineer.name})
		var taken *memcoai.AlreadyExistsError
		switch {
		case errors.As(err, &taken):
			// A real integration's ids carry no run tag, so mapping someone a
			// second time finds them already there. Not this run's to remove.
			user, err = client.Users.Get(ctx, externalID)
		case err == nil:
			users = append(users, user)
		}
		if err != nil {
			return err
		}
		byHandle[engineer.handle] = user
		if _, err := client.Networks.AddMember(ctx, networks[engineer.where].ID, user.ID, memcoai.AddMemberParams{}); err != nil {
			return err
		}
	}

	for _, network := range created {
		members, err := client.Networks.ListMembers(ctx, network.ID, memcoai.ListMembersParams{})
		if err != nil {
			return err
		}
		var names []string
		for _, member := range members.Members {
			names = append(names, member.Name)
		}
		out.Printf("%s: %s", network.Name, strings.Join(names, ", "))
	}

	// Bob moves from billing to the mobile app. Placing him again is refused
	// while he is in another network of the same domain, naming that network.
	bob := byHandle["bob"]
	_, err = client.Networks.AddMember(ctx, networks["mobile"].ID, bob.ID, memcoai.AddMemberParams{})
	var assigned *memcoai.UserAlreadyAssignedNetworkError
	switch {
	case errors.As(err, &assigned):
		out.Printf("\nrefused: %s: %s is already in %s (%s)",
			assigned.Reason, bob.Name, assigned.CurrentNetworkName, assigned.CurrentNetworkID)
	case err != nil:
		return err
	}
	// Asking for the move takes him out of billing and into mobile.
	moved, err := client.Networks.AddMember(ctx, networks["mobile"].ID, bob.ID, memcoai.AddMemberParams{Force: true})
	if err != nil {
		return err
	}
	for _, network := range created {
		if network.ID == moved.MovedFrom {
			out.Printf("moved %s out of %s", bob.Name, network.Name)
		}
	}

	// A key lets one engineer's own agent reach Memco directly, over MCP. Its
	// value is returned here and never again: hand it over now.
	alice := byHandle["alice"]
	key, err := client.Users.CreateKey(ctx, alice.ExternalID, memcoai.CreateKeyParams{Preset: "mcp_ro"})
	if err != nil {
		return err
	}
	out.Printf("\nkey %s... expires %s", key.Key.ValuePrefix, key.Key.ValidUntil.Format(time.DateOnly))
	return client.Users.DeleteKey(ctx, alice.ExternalID, key.Key.ID)
}

// cleanUp removes what the run created, even once ctx has ended, so a run
// interrupted between calls leaves nothing behind. Deleting a user revokes its
// keys, and projects go before the company network they hang under.
func cleanUp(ctx context.Context, client *memcoai.Client, networks []memcoai.Network, users []*memcoai.ExternalUser, out *log.Logger) error {
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), time.Minute)
	defer cancel()
	var failures []error
	for _, user := range users {
		failures = append(failures, client.Users.Delete(ctx, user.ExternalID))
	}
	for i := len(networks) - 1; i >= 0; i-- {
		_, err := client.Networks.Delete(ctx, networks[i].ID)
		failures = append(failures, err)
	}
	out.Print("removed again")
	return errors.Join(failures...)
}
