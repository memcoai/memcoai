package memcoai

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/protobuf/proto"

	adminv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/admin/v1"
	"github.com/memcoai/memcoai/go/internal/testserver"
)

func text(s string) *string { return &s }

var canned = Network{
	ID: "network-a", Name: "Acme", ParentID: "network-root", Domain: "coding", Region: "global",
	Scope: "customer", Owner: "acme", Description: "Acme's support knowledge",
}

var cannedMember = Member{UserID: "user-a", Email: "ada@example.com", Name: "Ada"}

var cannedKey = ExternalUserKey{
	ID: "apikey-a", Name: "ci", ValuePrefix: "mk_live_ab", Roles: []string{"reader"},
	Scopes: []string{"mcp:read"}, ValidUntil: time.Unix(1767225600, 0).UTC(),
}

// cet is a zone other than UTC, so an instant sent in it must still arrive as
// the same instant.
var cet = time.FixedZone("CET", 3600)

type roundTrip struct {
	rpc  string
	call func(*Client) (any, error)
	sent proto.Message
	want any
}

func roundTrips() []roundTrip {
	none := func(err error) (any, error) { return nil, err }
	return []roundTrip{
		{"ListNetworks", func(c *Client) (any, error) {
			return c.Networks.List(ctx, ListNetworksParams{
				Name: "Acme", Scope: "customer", Owner: "acme", Domain: "coding", ParentID: "root",
				IDs: []string{"network-a", "network-b"}, Page: 2, PageSize: 10,
			})
		}, &adminv1.ListNetworksRequest{
			Name: "Acme", Scope: "customer", Owner: "acme", Domain: "coding", ParentId: "root",
			Ids: []string{"network-a", "network-b"}, Page: 2, PageSize: 10,
		}, &NetworkList{Networks: []Network{canned}, TotalCount: 1}},
		{"CreateNetwork", func(c *Client) (any, error) {
			return c.Networks.Create(ctx, CreateNetworkParams{
				Name: "Acme", ParentID: "network-root", Region: "eu", Scope: "customer", Owner: "acme", Description: "d",
			})
		}, &adminv1.CreateNetworkRequest{
			Name: "Acme", ParentId: "network-root", Region: "eu", Scope: "customer", Owner: "acme", Description: "d",
		}, &Network{
			ID: "network-1", Name: "Acme", ParentID: "network-root", Domain: "coding", Region: "eu",
			Scope: "customer", Owner: "acme", Description: "d",
		}},
		{"UpdateNetwork", func(c *Client) (any, error) {
			return c.Networks.Update(ctx, "network-a", UpdateNetworkParams{Name: text("Acme Corp"), Description: text("")})
		}, &adminv1.UpdateNetworkRequest{Id: "network-a", Name: text("Acme Corp"), Description: text("")}, &Network{
			ID: "network-a", Name: "Acme Corp", ParentID: "network-root", Domain: "coding", Region: "global",
			Scope: "customer", Owner: "acme",
		}},
		{"DeleteNetwork", func(c *Client) (any, error) {
			return c.Networks.Delete(ctx, "network-a")
		}, &adminv1.DeleteNetworkRequest{Id: "network-a"}, &DeletedNetwork{
			ID: "network-a", Removed: map[string]int64{"network_members": 1, "memories": 3},
		}},
		{"ListNetworkMembers", func(c *Client) (any, error) {
			return c.Networks.ListMembers(ctx, "network-a", ListMembersParams{Search: "ada", Page: 1, PageSize: 5})
		}, &adminv1.ListNetworkMembersRequest{Id: "network-a", Search: "ada", Page: 1, PageSize: 5},
			&MemberList{Members: []Member{cannedMember}, TotalCount: 1}},
		{"AddNetworkMember", func(c *Client) (any, error) {
			return c.Networks.AddMember(ctx, "network-a", "user-a", AddMemberParams{Force: true})
		}, &adminv1.AddNetworkMemberRequest{Id: "network-a", UserId: "user-a", Force: true},
			&MemberPlacement{NetworkID: "network-a", UserID: "user-a"}},
		{"RemoveNetworkMember", func(c *Client) (any, error) {
			return none(c.Networks.RemoveMember(ctx, "network-a", "user-a"))
		}, &adminv1.RemoveNetworkMemberRequest{Id: "network-a", UserId: "user-a"}, nil},
		{"ListGroups", func(c *Client) (any, error) {
			return c.Networks.ListGroups(ctx, ListGroupsParams{
				Name: "Support", NetworkID: "network-a", IDs: []string{"group-a"}, Page: 1, PageSize: 5,
			})
		}, &adminv1.ListGroupsRequest{Name: "Support", NetworkId: "network-a", Ids: []string{"group-a"}, Page: 1, PageSize: 5},
			&GroupList{Groups: []Group{{ID: "group-a", Name: "Support", MemoryNetworkID: "network-a", MemberCount: 2}}, TotalCount: 1}},
		{"ListGroupMembers", func(c *Client) (any, error) {
			return c.Networks.ListGroupMembers(ctx, "group-a")
		}, &adminv1.ListGroupMembersRequest{Id: "group-a"}, []Member{cannedMember}},
		{"AddNetworkGroup", func(c *Client) (any, error) {
			return none(c.Networks.AddGroup(ctx, "network-a", "group-a"))
		}, &adminv1.AddNetworkGroupRequest{Id: "network-a", GroupId: "group-a"}, nil},
		{"RemoveNetworkGroup", func(c *Client) (any, error) {
			return none(c.Networks.RemoveGroup(ctx, "network-a", "group-a"))
		}, &adminv1.RemoveNetworkGroupRequest{Id: "network-a", GroupId: "group-a"}, nil},
		{"ListExternalUsers", func(c *Client) (any, error) {
			return c.Users.List(ctx, ListUsersParams{Search: "acme", Page: 1, PageSize: 5})
		}, &adminv1.ListExternalUsersRequest{Search: "acme", Page: 1, PageSize: 5}, &ExternalUserList{
			ExternalUsers: []ExternalUser{{
				ID: "xuser-a", ExternalID: "customer-42", Name: "Ada", Email: "ada@example.com",
				Roles: []string{"reader"}, Active: true,
			}},
			TotalCount: 1,
		}},
		{"GetExternalUser", func(c *Client) (any, error) {
			return c.Users.Get(ctx, "customer-7")
		}, &adminv1.GetExternalUserRequest{ExternalId: "customer-7"}, &ExternalUser{
			ID: "xuser-a", ExternalID: "customer-7", Name: "Ada", Email: "ada@example.com",
			Roles: []string{"reader"}, Active: true,
		}},
		{"CreateExternalUser", func(c *Client) (any, error) {
			return c.Users.Create(ctx, "customer-7", CreateUserParams{
				Roles: []string{"reader", "creator"}, Name: "Grace", Email: "grace@example.com",
			})
		}, &adminv1.CreateExternalUserRequest{
			ExternalId: "customer-7", Roles: []string{"reader", "creator"}, Name: "Grace", Email: "grace@example.com",
		}, &ExternalUser{
			ID: "xuser-1", ExternalID: "customer-7", Name: "Grace", Email: "grace@example.com",
			Roles: []string{"reader", "creator"}, Active: true,
		}},
		{"UpdateExternalUser", func(c *Client) (any, error) {
			return c.Users.Update(ctx, "customer-7", UpdateUserParams{Email: text(""), Roles: []string{"creator"}})
		}, &adminv1.UpdateExternalUserRequest{ExternalId: "customer-7", Email: text(""), Roles: []string{"creator"}}, &ExternalUser{
			ID: "xuser-a", ExternalID: "customer-7", Name: "Ada", Roles: []string{"creator"}, Active: true,
		}},
		{"DeleteExternalUser", func(c *Client) (any, error) {
			return none(c.Users.Delete(ctx, "customer-7"))
		}, &adminv1.DeleteExternalUserRequest{ExternalId: "customer-7"}, nil},
		{"ListExternalUserKeys", func(c *Client) (any, error) {
			return c.Users.ListKeys(ctx, "customer-7")
		}, &adminv1.ListExternalUserKeysRequest{ExternalId: "customer-7"}, []ExternalUserKey{cannedKey}},
		{"CreateExternalUserKey", func(c *Client) (any, error) {
			return c.Users.CreateKey(ctx, "customer-7", CreateKeyParams{
				Preset: "mcp_ro", Name: "agent", ValidUntil: time.Date(2027, 1, 1, 1, 0, 0, 0, cet),
			})
		}, &adminv1.CreateExternalUserKeyRequest{
			ExternalId: "customer-7", Preset: "mcp_ro", Name: "agent", ValidUntil: time.Date(2027, 1, 1, 0, 0, 0, 0, time.UTC).Unix(),
		}, &CreatedKey{
			Key: ExternalUserKey{
				ID: "apikey-new", Name: "agent", ValuePrefix: "mk_live_ne", Roles: []string{"reader"},
				Scopes: []string{"mcp:read"}, ValidUntil: time.Date(2027, 1, 1, 0, 0, 0, 0, time.UTC),
			},
			value: text("mk_live_new-key-value"),
		}},
		{"DeleteExternalUserKey", func(c *Client) (any, error) {
			return none(c.Users.DeleteKey(ctx, "customer-7", "apikey-a"))
		}, &adminv1.DeleteExternalUserKeyRequest{ExternalId: "customer-7", KeyId: "apikey-a"}, nil},
	}
}

func TestTheRoundTripCoversEveryAdministrationMethod(t *testing.T) {
	var covered []string
	for _, c := range roundTrips() {
		covered = append(covered, c.rpc)
	}
	for _, method := range adminv1.AdminService_ServiceDesc.Methods {
		name := method.MethodName
		// Minted and ended by sessions, never called directly.
		if name != "ImpersonateExternalUser" && name != "EndImpersonation" && !slices.Contains(covered, name) {
			t.Errorf("%s has no round trip", name)
		}
	}
}

func TestEachMethodSendsItsRequestAndReturnsItsResult(t *testing.T) {
	for _, c := range roundTrips() {
		t.Run(c.rpc, func(t *testing.T) {
			f := credentialed(t)
			got, err := c.call(f.client)
			if err != nil {
				t.Fatal(err)
			}
			if calls := f.server.Admin.Calls(); !reflect.DeepEqual(calls, []string{c.rpc}) {
				t.Fatalf("calls %v", calls)
			}
			if sent := f.server.Admin.Request(c.rpc); !proto.Equal(sent, c.sent) {
				t.Fatalf("sent %v, want %v", sent, c.sent)
			}
			if c.want != nil && !reflect.DeepEqual(got, c.want) {
				t.Fatalf("got %#v, want %#v", got, c.want)
			}
			if got := bearers(f.server.Admin.Metadata()); !reflect.DeepEqual(got, []string{"Bearer client-token-1"}) {
				t.Fatalf("authorization %q", got)
			}
		})
	}
}

func TestUnsetFiltersPatchesAndPagesSendNothing(t *testing.T) {
	f := credentialed(t)
	calls := []func() error{
		func() error { _, err := f.client.Networks.List(ctx, ListNetworksParams{}); return err },
		func() error { _, err := f.client.Networks.Update(ctx, "network-a", UpdateNetworkParams{}); return err },
		func() error {
			_, err := f.client.Networks.ListMembers(ctx, "network-a", ListMembersParams{})
			return err
		},
		func() error { _, err := f.client.Networks.ListGroups(ctx, ListGroupsParams{}); return err },
		func() error { _, err := f.client.Users.List(ctx, ListUsersParams{}); return err },
		func() error { _, err := f.client.Users.Update(ctx, "customer-7", UpdateUserParams{}); return err },
		func() error {
			_, err := f.client.Users.CreateKey(ctx, "customer-7", CreateKeyParams{Preset: "mcp_ro"})
			return err
		},
	}
	for _, call := range calls {
		if err := call(); err != nil {
			t.Fatal(err)
		}
	}
	want := []proto.Message{
		&adminv1.ListNetworksRequest{},
		&adminv1.UpdateNetworkRequest{Id: "network-a"},
		&adminv1.ListNetworkMembersRequest{Id: "network-a"},
		&adminv1.ListGroupsRequest{},
		&adminv1.ListExternalUsersRequest{},
		&adminv1.UpdateExternalUserRequest{ExternalId: "customer-7"},
		// No expiry sends zero, which takes the service's default.
		&adminv1.CreateExternalUserKeyRequest{ExternalId: "customer-7", Preset: "mcp_ro"},
	}
	for i, sent := range f.server.Admin.Received() {
		if !proto.Equal(sent, want[i]) {
			t.Errorf("sent %v, want %v", sent, want[i])
		}
	}
}

func TestAKeyThatNeverExpiresReadsAsTheZeroTime(t *testing.T) {
	f := credentialed(t)
	never := proto.Clone(testserver.CannedKey).(*adminv1.ExternalUserKey)
	never.ValidUntil = 0
	f.server.Admin.Respond("ListExternalUserKeys", &adminv1.ListExternalUserKeysResponse{Keys: []*adminv1.ExternalUserKey{never}})
	keys, err := f.client.Users.ListKeys(ctx, "customer-7")
	if err != nil {
		t.Fatal(err)
	}
	if len(keys) != 1 || !keys[0].ValidUntil.IsZero() {
		t.Fatalf("got %+v", keys)
	}
}

func TestACreatedKeyCarriesItsValueButNeverShowsIt(t *testing.T) {
	f := credentialed(t)
	created, err := f.client.Users.CreateKey(ctx, "customer-7", CreateKeyParams{Preset: "mcp_ro"})
	if err != nil {
		t.Fatal(err)
	}
	if created.Value() != "mk_live_new-key-value" {
		t.Fatalf("control: the value is %q", created.Value())
	}
	encoded, err := json.Marshal(created)
	if err != nil {
		t.Fatal(err)
	}
	listed, err := json.Marshal([]CreatedKey{*created})
	if err != nil {
		t.Fatal(err)
	}
	var logged bytes.Buffer
	slog.New(slog.NewTextHandler(&logged, nil)).Info("x", "key", created, "value", *created)
	slog.New(slog.NewJSONHandler(&logged, nil)).Info("x", "key", created)
	renderings := []string{string(encoded), string(listed), logged.String(), fmt.Sprint([]CreatedKey{*created})}
	for _, verb := range []string{"%v", "%+v", "%#v", "%s", "%q", "%x"} {
		renderings = append(renderings, fmt.Sprintf(verb, created), fmt.Sprintf(verb, *created))
	}
	for _, rendered := range renderings {
		if strings.Contains(rendered, "new-key-value") || strings.Contains(rendered, "6e65772d6b6579") {
			t.Errorf("the value is visible in %q", rendered)
		}
	}
	if !strings.Contains(renderings[0], "mk_live_ne") {
		t.Fatalf("control: the prefix was not rendered: %s", renderings[0])
	}
}

func TestAValueArrivingWithoutItsKeyIsAnInternalError(t *testing.T) {
	f := credentialed(t)
	f.server.Admin.Respond("CreateExternalUserKey", &adminv1.CreateExternalUserKeyResponse{Value: "mk_live_orphan-value"})
	_, err := f.client.Users.CreateKey(ctx, "customer-7", CreateKeyParams{Preset: "mcp_ro"})
	got := as[*InternalError](t, err)
	if got.Detail != "the service returned a key value but no key; list the user's keys to see whether one was created" {
		t.Fatalf("got %q", got.Detail)
	}
	if strings.Contains(fmt.Sprintf("%v %+v", err, err), "orphan") {
		t.Fatal("the value reached the error")
	}
}

func TestABadArgumentIsRefusedBeforeAnythingIsSent(t *testing.T) {
	f := credentialed(t)
	cases := map[string]func() error{
		"network_id must not be empty": func() error {
			_, err := f.client.Networks.Update(ctx, " ", UpdateNetworkParams{Name: text("x")})
			return err
		},
		"user_id must not be empty": func() error {
			_, err := f.client.Networks.AddMember(ctx, "network-a", "", AddMemberParams{})
			return err
		},
		"group_id must not be empty":    func() error { return f.client.Networks.AddGroup(ctx, "network-a", "\t") },
		"key_id must not be empty":      func() error { return f.client.Users.DeleteKey(ctx, "customer-7", "") },
		"external_id must not be empty": func() error { _, err := f.client.Users.Get(ctx, "  "); return err },
		"roles must name at least one role": func() error {
			_, err := f.client.Users.Create(ctx, "customer-7", CreateUserParams{})
			return err
		},
	}
	for want, call := range cases {
		invalid(t, call(), want)
	}
	_, err := f.client.Networks.Create(ctx, CreateNetworkParams{Name: "bad \xff"})
	if got := as[*InvalidRequestError](t, err); !strings.HasPrefix(got.Detail, "a field value cannot be sent: ") || !strings.Contains(got.Detail, "UTF-8") {
		t.Errorf("got %q", got.Detail)
	}
	// Given, but naming no role: on the wire it would read as not given.
	_, err = f.client.Users.Update(ctx, "customer-7", UpdateUserParams{Roles: []string{}})
	invalid(t, err, "roles must name at least one role")
	if calls := f.server.Admin.Calls(); len(calls) != 0 {
		t.Fatalf("sent %v", calls)
	}
}

func TestADuplicateIsAlreadyExists(t *testing.T) {
	f := credentialed(t)
	f.server.Admin.FailNext("CreateExternalUser", testserver.Failure{Code: codes.AlreadyExists, Details: "external_id taken"})
	_, err := f.client.Users.Create(ctx, "customer-7", CreateUserParams{Roles: []string{"reader"}})
	if got := as[*AlreadyExistsError](t, err); got.Detail != "external_id taken" || err.Error() != "ALREADY_EXISTS: external_id taken" {
		t.Fatalf("got %v", err)
	}
}

func refusedPlacement(t *testing.T, failure testserver.Failure) error {
	t.Helper()
	f := credentialed(t)
	failure.Code = codes.FailedPrecondition
	f.server.Admin.FailNext("AddNetworkMember", failure)
	_, err := f.client.Networks.AddMember(ctx, "network-b", "user-a", AddMemberParams{})
	return err
}

func TestARefusedMoveNamesTheNetworkTheUserIsIn(t *testing.T) {
	metadata := map[string]string{"current_network_id": "network-a", "current_network_name": "Acme", "added_later": "x"}
	err := refusedPlacement(t, testserver.Failure{
		Details: "network_move_required: user-a is in Acme", Reason: "USER_ALREADY_ASSIGNED_NETWORK",
		Domain: "memco.ai", Metadata: metadata,
	})
	got := as[*UserAlreadyAssignedNetworkError](t, err)
	if got.CurrentNetworkID != "network-a" || got.CurrentNetworkName != "Acme" ||
		got.Reason != "USER_ALREADY_ASSIGNED_NETWORK" || !reflect.DeepEqual(got.Metadata, metadata) {
		t.Fatalf("got %+v", got)
	}
	if precondition := as[*PreconditionFailedError](t, err); precondition.Detail != "network_move_required: user-a is in Acme" {
		t.Fatalf("got %+v", precondition)
	}
}

func TestACustomerNetworkRefusalNamesTheScopeItNeeds(t *testing.T) {
	err := refusedPlacement(t, testserver.Failure{
		Details: "external users need a customer network", Reason: "EXTERNAL_USER_NEEDS_CUSTOMER_NETWORK",
		Domain: "memco.ai", Metadata: map[string]string{"required_network_scope": "customer"},
	})
	if got := as[*ExternalUserNeedsCustomerNetworkError](t, err); got.RequiredNetworkScope != "customer" {
		t.Fatalf("got %+v", got)
	}
	expect[*PreconditionFailedError](t, err)
	var sunset *SunsetError
	if errors.As(err, &sunset) {
		t.Fatal("a placement refusal reads as a sunset")
	}
}

func TestAnyPreconditionCarriesWhatTheServiceSent(t *testing.T) {
	err := refusedPlacement(t, testserver.Failure{
		Details: "newer refusal", Reason: "NEWER_REASON", Domain: "memco.ai", Metadata: map[string]string{"k": "v"},
	})
	got := as[*PreconditionFailedError](t, err)
	if got.Reason != "NEWER_REASON" || !reflect.DeepEqual(got.Metadata, map[string]string{"k": "v"}) {
		t.Fatalf("got %+v", got)
	}
	var assigned *UserAlreadyAssignedNetworkError
	if errors.As(err, &assigned) {
		t.Fatal("an unknown reason was read as a known one")
	}
}

func TestAPlacementReasonFromAnotherDomainIsNotReadAsMemcos(t *testing.T) {
	err := refusedPlacement(t, testserver.Failure{
		Details: "taken", Reason: "USER_ALREADY_ASSIGNED_NETWORK", Domain: "example.com",
		Metadata: map[string]string{"current_network_id": "network-a"},
	})
	got := as[*PreconditionFailedError](t, err)
	var assigned *UserAlreadyAssignedNetworkError
	if errors.As(err, &assigned) || got.Reason != "" || got.Metadata != nil {
		t.Fatalf("got %+v", got)
	}
}

func TestEveryMethodReturnsTheServicesRefusalTyped(t *testing.T) {
	for _, c := range roundTrips() {
		t.Run(c.rpc, func(t *testing.T) {
			f := credentialed(t)
			f.server.Admin.Fail(&testserver.Failure{Code: codes.PermissionDenied, Details: "missing scope"})
			got, err := c.call(f.client)
			if refused := as[*PermissionError](t, err); refused.Detail != "missing scope" {
				t.Fatalf("got %+v", refused)
			}
			if value := reflect.ValueOf(got); got != nil && !value.IsNil() {
				t.Fatalf("returned %#v beside the error", got)
			}
		})
	}
}
