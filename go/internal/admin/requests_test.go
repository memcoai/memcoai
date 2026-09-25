package admin

import (
	"errors"
	"strings"
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/protobuf/proto"

	adminv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/admin/v1"
	"github.com/memcoai/memcoai/go/internal/fault"
)

func TestBlankHandlesAndMissingRolesAreRefused(t *testing.T) {
	cases := []struct {
		request proto.Message
		want    string
	}{
		{&adminv1.UpdateNetworkRequest{Id: " "}, "network_id must not be empty"},
		{&adminv1.DeleteNetworkRequest{}, "network_id must not be empty"},
		{&adminv1.ListNetworkMembersRequest{Id: "\t"}, "network_id must not be empty"},
		{&adminv1.AddNetworkMemberRequest{UserId: "u"}, "network_id must not be empty"},
		{&adminv1.AddNetworkMemberRequest{Id: "n", UserId: " "}, "user_id must not be empty"},
		{&adminv1.RemoveNetworkMemberRequest{Id: "n"}, "user_id must not be empty"},
		{&adminv1.ListGroupMembersRequest{}, "group_id must not be empty"},
		{&adminv1.AddNetworkGroupRequest{Id: "n"}, "group_id must not be empty"},
		{&adminv1.RemoveNetworkGroupRequest{GroupId: "g"}, "network_id must not be empty"},
		{&adminv1.GetExternalUserRequest{ExternalId: " "}, "external_id must not be empty"},
		{&adminv1.CreateExternalUserRequest{Roles: []string{"reader"}}, "external_id must not be empty"},
		{&adminv1.CreateExternalUserRequest{ExternalId: "x"}, "roles must name at least one role"},
		// Given, but empty: on the wire it would read as not given.
		{&adminv1.UpdateExternalUserRequest{ExternalId: "x", Roles: []string{}}, "roles must name at least one role"},
		{&adminv1.DeleteExternalUserRequest{}, "external_id must not be empty"},
		{&adminv1.ListExternalUserKeysRequest{}, "external_id must not be empty"},
		{&adminv1.CreateExternalUserKeyRequest{Preset: "mcp_ro"}, "external_id must not be empty"},
		{&adminv1.DeleteExternalUserKeyRequest{ExternalId: "x"}, "key_id must not be empty"},
		{&adminv1.ImpersonateExternalUserRequest{}, "external_id must not be empty"},
	}
	for _, c := range cases {
		err := Check(c.request)
		var f *fault.Error
		if !errors.As(err, &f) || f.Code != codes.InvalidArgument || f.Detail != c.want {
			t.Errorf("%T: got %v, want %q", c.request, err, c.want)
		}
	}
}

func TestWhatTheServiceOwnsIsLeftToIt(t *testing.T) {
	for _, request := range []proto.Message{
		&adminv1.ListNetworksRequest{},
		&adminv1.CreateNetworkRequest{},
		&adminv1.ListGroupsRequest{},
		&adminv1.ListExternalUsersRequest{},
		&adminv1.UpdateExternalUserRequest{ExternalId: "x"},
		&adminv1.CreateExternalUserRequest{ExternalId: "x", Roles: []string{"admin"}},
		&adminv1.CreateExternalUserKeyRequest{ExternalId: "x", Preset: "anything"},
		&adminv1.AddNetworkMemberRequest{Id: "n", UserId: "u"},
	} {
		if err := Check(request); err != nil {
			t.Errorf("%T: %v", request, err)
		}
	}
}

func TestTextProtobufCannotEncodeIsRefused(t *testing.T) {
	err := Check(&adminv1.CreateNetworkRequest{Name: "bad \xff"})
	var f *fault.Error
	if !errors.As(err, &f) || f.Code != codes.InvalidArgument || !strings.HasPrefix(f.Detail, "a field value cannot be sent: ") {
		t.Fatalf("got %v", err)
	}
}
