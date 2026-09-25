//go:build systemtest

package systemtest

import (
	"errors"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/memcoai/memcoai/go/memcoai"
)

// A key created without an expiry is given three months, which the calendar
// makes anything from 89 to 92 days; the margin either side absorbs the clock
// and the service's rounding.
const (
	keyLifetimeFloor   = 88 * 24 * time.Hour
	keyLifetimeCeiling = 93 * 24 * time.Hour
)

// is fails t unless err is, or wraps, a T, and returns it.
func is[T error](t *testing.T, err error) T {
	t.Helper()
	var target T
	if !errors.As(err, &target) {
		t.Fatalf("got %T %v, want %T", err, err, target)
	}
	return target
}

// expect fails t unless err is, or wraps, a T.
func expect[T error](t *testing.T, err error) {
	t.Helper()
	_ = is[T](t, err)
}

func TestACustomerNetworkIsCreatedChangedFoundAndDeleted(t *testing.T) {
	admin := administrator(t)
	root := rootNetwork(t, admin)
	ctx := t.Context()

	roots, err := admin.Networks.List(ctx, memcoai.ListNetworksParams{ParentID: "root", Domain: root.Domain})
	if err != nil {
		t.Fatal(err)
	}
	if !slices.ContainsFunc(roots.Networks, func(n memcoai.Network) bool { return n.ID == root.ID }) {
		t.Fatalf("%s is not among the roots of %s", root.ID, root.Domain)
	}
	// Pages count from 1: page 1 is what no page is, where counting from 0
	// would return the second page instead.
	first, err := admin.Networks.List(ctx, memcoai.ListNetworksParams{ParentID: "root", PageSize: 1})
	if err != nil {
		t.Fatal(err)
	}
	paged, err := admin.Networks.List(ctx, memcoai.ListNetworksParams{ParentID: "root", Page: 1, PageSize: 1})
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(first.Networks, paged.Networks) {
		t.Fatalf("page 1 is %v, no page is %v", paged.Networks, first.Networks)
	}

	network := customerNetwork(t, admin, root)
	t.Logf("created network %s under %s in %s", network.ID, root.ID, network.Domain)
	if network.ParentID != root.ID || network.Domain != root.Domain || network.Scope != "customer" {
		t.Fatalf("created %+v", network)
	}

	description := "A network the Go SDK system test has changed."
	changed, err := admin.Networks.Update(ctx, network.ID, memcoai.UpdateNetworkParams{Description: &description})
	if err != nil {
		t.Fatal(err)
	}
	if changed.Description != description || changed.Name != network.Name {
		t.Fatalf("changed %+v", changed)
	}
	found, err := admin.Networks.List(ctx, memcoai.ListNetworksParams{IDs: []string{network.ID}})
	if err != nil {
		t.Fatal(err)
	}
	if len(found.Networks) != 1 || found.Networks[0].Description != description {
		t.Fatalf("found %+v", found.Networks)
	}
	_, err = admin.Networks.Update(ctx, "", memcoai.UpdateNetworkParams{Description: &description})
	expect[*memcoai.InvalidRequestError](t, err)

	deleted, err := admin.Networks.Delete(ctx, network.ID)
	if err != nil {
		t.Fatal(err)
	}
	if deleted.ID != network.ID {
		t.Fatalf("deleted %+v", deleted)
	}
	if found, err = admin.Networks.List(ctx, memcoai.ListNetworksParams{IDs: []string{network.ID}}); err != nil || len(found.Networks) != 0 {
		t.Fatalf("still listed: %v, %v", found, err)
	}
}

// Groups belong to enterprise organisations only, and the service refuses the
// whole surface to any other. The organisation is shared with other suites
// and this one cannot create a group, so in an enterprise organisation it
// touches none and skips.
func TestGroupsAreRefusedOutsideAnEnterprise(t *testing.T) {
	admin := administrator(t)
	network := customerNetwork(t, admin, rootNetwork(t, admin))
	_, err := admin.Networks.ListGroups(t.Context(), memcoai.ListGroupsParams{})
	var denied *memcoai.PermissionError
	if !errors.As(err, &denied) {
		if err != nil {
			t.Fatal(err)
		}
		t.Skip("groups are available here, and this suite changes only what it creates")
	}
	t.Log("groups are refused: this is not an enterprise organisation")
	expect[*memcoai.PermissionError](t, admin.Networks.AddGroup(t.Context(), network.ID, "group-that-is-never-reached"))
}

func TestAnExternalUserIsCreatedFoundChangedAndDeleted(t *testing.T) {
	admin := administrator(t)
	ctx := t.Context()
	user, err := externalUser(t, admin)
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("created external user %s as %s", user.ExternalID, user.ID)
	// A creator reads what it can write, and the service says so.
	if roles := slices.Sorted(slices.Values(user.Roles)); !reflect.DeepEqual(roles, []string{"creator", "reader"}) {
		t.Fatalf("roles %v", user.Roles)
	}

	_, err = admin.Users.Create(ctx, user.ExternalID, memcoai.CreateUserParams{Roles: []string{"creator"}})
	expect[*memcoai.AlreadyExistsError](t, err)
	// An external user never holds admin.
	_, err = externalUser(t, admin, "admin")
	expect[*memcoai.InvalidRequestError](t, err)

	fetched, err := admin.Users.Get(ctx, user.ExternalID)
	if err != nil {
		t.Fatal(err)
	}
	if fetched.ID != user.ID {
		t.Fatalf("fetched %+v", fetched)
	}
	listed, err := admin.Users.List(ctx, memcoai.ListUsersParams{Search: user.ExternalID})
	if err != nil {
		t.Fatal(err)
	}
	if len(listed.ExternalUsers) != 1 || listed.ExternalUsers[0].ID != user.ID {
		t.Fatalf("listed %+v", listed.ExternalUsers)
	}

	name := "Go SDK system test"
	renamed, err := admin.Users.Update(ctx, user.ExternalID, memcoai.UpdateUserParams{Name: &name})
	if err != nil {
		t.Fatal(err)
	}
	if renamed.Name != name || !reflect.DeepEqual(slices.Sorted(slices.Values(renamed.Roles)), []string{"creator", "reader"}) {
		t.Fatalf("renamed %+v: roles left as nil were changed, or the name was not", renamed)
	}

	if err := admin.Users.Delete(ctx, user.ExternalID); err != nil {
		t.Fatal(err)
	}
	_, err = admin.Users.Get(ctx, user.ExternalID)
	expect[*memcoai.NotFoundError](t, err)
}

func TestAnExternalUsersKeyIsShownOnceListedAndRevoked(t *testing.T) {
	admin := administrator(t)
	ctx := t.Context()
	user, err := externalUser(t, admin)
	if err != nil {
		t.Fatal(err)
	}
	created, err := admin.Users.CreateKey(ctx, user.ExternalID, memcoai.CreateKeyParams{Preset: "mcp_ro", Name: "go-systemtest"})
	if err != nil {
		t.Fatal(err)
	}
	// Checked without printing: the value is a live key.
	if !strings.HasPrefix(created.Value(), created.Key.ValuePrefix) {
		t.Fatal("the key's value does not start with its listed prefix")
	}
	if left := time.Until(created.Key.ValidUntil); left < keyLifetimeFloor || left > keyLifetimeCeiling {
		t.Fatalf("a key valid for %v", left)
	}

	// The audit stream is for auditors, which a creator is not.
	_, err = admin.Users.CreateKey(ctx, user.ExternalID, memcoai.CreateKeyParams{Preset: "audit"})
	expect[*memcoai.InvalidRequestError](t, err)

	held := func() bool {
		t.Helper()
		keys, err := admin.Users.ListKeys(ctx, user.ExternalID)
		if err != nil {
			t.Fatal(err)
		}
		return slices.ContainsFunc(keys, func(key memcoai.ExternalUserKey) bool { return key.ID == created.Key.ID })
	}
	if !held() {
		t.Fatal("the key is not listed")
	}
	if err := admin.Users.DeleteKey(ctx, user.ExternalID, created.Key.ID); err != nil {
		t.Fatal(err)
	}
	if held() {
		t.Fatal("the key is still listed")
	}
	expect[*memcoai.NotFoundError](t, admin.Users.DeleteKey(ctx, user.ExternalID, created.Key.ID))
}

func TestAnExternalUserIsPlacedOnlyInACustomerNetwork(t *testing.T) {
	admin := administrator(t)
	ctx := t.Context()
	root := rootNetwork(t, admin)
	network := customerNetwork(t, admin, root)
	user, err := externalUser(t, admin)
	if err != nil {
		t.Fatal(err)
	}

	_, err = admin.Networks.AddMember(ctx, root.ID, user.ID, memcoai.AddMemberParams{})
	if refused := is[*memcoai.ExternalUserNeedsCustomerNetworkError](t, err); refused.RequiredNetworkScope != "customer" {
		t.Fatalf("refused %+v", refused)
	}

	placement, err := admin.Networks.AddMember(ctx, network.ID, user.ID, memcoai.AddMemberParams{})
	if err != nil {
		t.Fatal(err)
	}
	if *placement != (memcoai.MemberPlacement{NetworkID: network.ID, UserID: user.ID}) {
		t.Fatalf("placed %+v", placement)
	}
	member := func(networkID string) bool {
		t.Helper()
		members, err := admin.Networks.ListMembers(ctx, networkID, memcoai.ListMembersParams{})
		if err != nil {
			t.Fatal(err)
		}
		return slices.ContainsFunc(members.Members, func(m memcoai.Member) bool { return m.UserID == user.ID })
	}
	if !member(network.ID) {
		t.Fatal("the user is not listed as a member")
	}

	// A second network of the same domain refuses them, naming where they are.
	second := customerNetwork(t, admin, root)
	_, err = admin.Networks.AddMember(ctx, second.ID, user.ID, memcoai.AddMemberParams{})
	moving := is[*memcoai.UserAlreadyAssignedNetworkError](t, err)
	if moving.CurrentNetworkID != network.ID || moving.CurrentNetworkName != network.Name {
		t.Fatalf("refused %+v", moving)
	}
	moved, err := admin.Networks.AddMember(ctx, second.ID, user.ID, memcoai.AddMemberParams{Force: true})
	if err != nil {
		t.Fatal(err)
	}
	if moved.MovedFrom != network.ID || member(network.ID) || !member(second.ID) {
		t.Fatalf("moved %+v", moved)
	}

	// A network holding an external user cannot stop being a customer one.
	internal := "internal"
	_, err = admin.Networks.Update(ctx, second.ID, memcoai.UpdateNetworkParams{Scope: &internal})
	expect[*memcoai.PreconditionFailedError](t, err)

	if err := admin.Networks.RemoveMember(ctx, second.ID, user.ID); err != nil {
		t.Fatal(err)
	}
	if member(second.ID) {
		t.Fatal("the user is still listed as a member")
	}
}
