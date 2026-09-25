// Package admin checks administration requests before they are sent.
package admin

import (
	"cmp"
	"strings"

	"google.golang.org/protobuf/proto"

	adminv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/admin/v1"
	"github.com/memcoai/memcoai/go/internal/fault"
)

// Check refuses a request the service could only refuse too: a blank handle,
// no roles where some are required, or text protobuf cannot encode. Scopes,
// presets, role names and caps are the service's to judge.
func Check(request proto.Message) error {
	var err error
	switch r := request.(type) {
	case *adminv1.UpdateNetworkRequest:
		err = present(r.GetId(), "network_id")
	case *adminv1.DeleteNetworkRequest:
		err = present(r.GetId(), "network_id")
	case *adminv1.ListNetworkMembersRequest:
		err = present(r.GetId(), "network_id")
	case *adminv1.AddNetworkMemberRequest:
		err = cmp.Or(present(r.GetId(), "network_id"), present(r.GetUserId(), "user_id"))
	case *adminv1.RemoveNetworkMemberRequest:
		err = cmp.Or(present(r.GetId(), "network_id"), present(r.GetUserId(), "user_id"))
	case *adminv1.ListGroupMembersRequest:
		err = present(r.GetId(), "group_id")
	case *adminv1.AddNetworkGroupRequest:
		err = cmp.Or(present(r.GetId(), "network_id"), present(r.GetGroupId(), "group_id"))
	case *adminv1.RemoveNetworkGroupRequest:
		err = cmp.Or(present(r.GetId(), "network_id"), present(r.GetGroupId(), "group_id"))
	case *adminv1.GetExternalUserRequest:
		err = present(r.GetExternalId(), "external_id")
	case *adminv1.CreateExternalUserRequest:
		err = cmp.Or(present(r.GetExternalId(), "external_id"), roles(r.GetRoles()))
	case *adminv1.UpdateExternalUserRequest:
		err = present(r.GetExternalId(), "external_id")
		// Nil leaves the roles alone; given but empty would read the same on
		// the wire, so the service could not say why nothing changed.
		if err == nil && r.Roles != nil {
			err = roles(r.Roles)
		}
	case *adminv1.DeleteExternalUserRequest:
		err = present(r.GetExternalId(), "external_id")
	case *adminv1.ListExternalUserKeysRequest:
		err = present(r.GetExternalId(), "external_id")
	case *adminv1.CreateExternalUserKeyRequest:
		err = present(r.GetExternalId(), "external_id")
	case *adminv1.DeleteExternalUserKeyRequest:
		err = cmp.Or(present(r.GetExternalId(), "external_id"), present(r.GetKeyId(), "key_id"))
	case *adminv1.ImpersonateExternalUserRequest:
		err = present(r.GetExternalId(), "external_id")
	}
	if err != nil {
		return err
	}
	if _, err := proto.Marshal(request); err != nil {
		return fault.Invalid("a field value cannot be sent: " + err.Error())
	}
	return nil
}

func present(value, field string) error {
	if strings.TrimSpace(value) == "" {
		return fault.Invalid(field + " must not be empty")
	}
	return nil
}

func roles(given []string) error {
	if len(given) == 0 {
		return fault.Invalid("roles must name at least one role")
	}
	return nil
}
