package main

import (
	"context"
	"errors"
	"reflect"
	"testing"

	"github.com/memcoai/memcoai/go/examples/internal/exampletest"
	adminv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/admin/v1"
)

func TestItMapsTheCompanyRefusesAMoveMakesItAndRemovesEverything(t *testing.T) {
	r := exampletest.StartAPIClient(t)
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	r.Expect(t,
		" Acme Consulting: Ada",
		"refused: USER_ALREADY_ASSIGNED_NETWORK: Bob Berg is already in goex-", " Acme Consulting / Billing platform (network-2)",
		"moved Bob Berg out of goex-", " Acme Consulting / Billing platform",
		"key mk_live_ne... expires 2026-01-01",
		"removed again",
	)
	var usersDeleted int
	var networksDeleted []string
	for _, request := range r.Server.Admin.Received() {
		switch deleted := request.(type) {
		case *adminv1.DeleteExternalUserRequest:
			usersDeleted++
		case *adminv1.DeleteNetworkRequest:
			networksDeleted = append(networksDeleted, deleted.GetId())
		}
	}
	// Every user, then the projects before the company they hang under.
	if usersDeleted != len(engineers) || !reflect.DeepEqual(networksDeleted, []string{"network-3", "network-2", "network-1"}) {
		t.Fatalf("deleted %d users and networks %v", usersDeleted, networksDeleted)
	}
}

func TestAnEndedContextStopsIt(t *testing.T) {
	r := exampletest.StartAPIClient(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := run(ctx, r.Options, r.Out); !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
}
