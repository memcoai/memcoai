package memcoai

import (
	"context"
	"maps"
	"slices"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/protobuf/proto"

	"github.com/memcoai/memcoai/go/internal/admin"
	adminv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/admin/v1"
)

// NetworkOperations manage your organization's memory networks, their members
// and their identity-provider groups, reached as [Client.Networks]. Every call
// is made under the token the client is issued for an API client's
// credentials ([Options.ClientID]), and is bounded by the scopes that API
// client was granted.
type NetworkOperations struct {
	client *Client
	rpc    adminv1.AdminServiceClient
}

// ListNetworksParams narrow [NetworkOperations.List]. Every field is optional,
// and those given must all match.
type ListNetworksParams struct {
	// Name keeps networks with this name, matched case-insensitively.
	Name string
	// Scope keeps networks of this scope: "internal", "customer", or "none"
	// for networks without one.
	Scope string
	// Owner keeps networks with this owner, matched case-insensitively.
	Owner string
	// Domain keeps networks of this memory domain.
	Domain string
	// ParentID keeps the children of this network; "root" keeps the root
	// networks, one per memory domain.
	ParentID string
	// IDs keeps only these networks. The service bounds how many one call may
	// name.
	IDs []string
	// Page is the page to return, counting from 1; zero is the first.
	Page int32
	// PageSize is how many networks a page holds; zero is the service's
	// default.
	PageSize int32
}

// CreateNetworkParams describe a new network: a root in a memory domain, or
// the child of another network, whose domain it takes.
type CreateNetworkParams struct {
	// Name is the network's name. Required.
	Name string
	// ParentID is the network to create this one under; empty makes a root.
	ParentID string
	// Domain is a root network's memory domain; empty is your organization's
	// default. A child names none.
	Domain string
	// Region is where the network's data resides; empty is your
	// organization's default, and "global" replicates everywhere.
	Region string
	// Scope is "internal" or "customer", where your organization uses scopes.
	// An external user can be placed only in a customer network.
	Scope string
	// Owner is who the network's knowledge belongs to.
	Owner string
	// Description says what the network is for.
	Description string
}

// UpdateNetworkParams change a network. A nil field is left as it is; a
// pointer to "" is sent, and clears the field where the service allows it.
type UpdateNetworkParams struct {
	// Name is the new name.
	Name *string
	// ParentID is the network to move this one under.
	ParentID *string
	// Scope is the new scope, "internal" or "customer".
	Scope *string
	// Owner is the new owner.
	Owner *string
	// Description is the new description.
	Description *string
}

// ListMembersParams narrow [NetworkOperations.ListMembers].
type ListMembersParams struct {
	// Search keeps members matching this text.
	Search string
	// Page is the page to return, counting from 1; zero is the first.
	Page int32
	// PageSize is how many members a page holds; zero is the service's
	// default.
	PageSize int32
}

// AddMemberParams qualify [NetworkOperations.AddMember].
type AddMemberParams struct {
	// Force moves a user already placed in another network of the same
	// memory domain. Off, such a user is refused, so a move is never the
	// accidental outcome.
	Force bool
}

// ListGroupsParams narrow [NetworkOperations.ListGroups]. Every field is
// optional, and those given must all match.
type ListGroupsParams struct {
	// Name keeps groups with this name, matched case-insensitively.
	Name string
	// NetworkID keeps groups assigned to this network.
	NetworkID string
	// IDs keeps only these groups. The service bounds how many one call may
	// name.
	IDs []string
	// Page is the page to return, counting from 1; zero is the first.
	Page int32
	// PageSize is how many groups a page holds; zero is the service's
	// default.
	PageSize int32
}

// List lists one page of your organization's memory networks, narrowed by the
// filters given.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - p: the filters and the page; the zero value lists the first page of
//     every network.
//
// It returns the page of networks, and how many match in all.
//
// Errors: [*PermissionError] when the credential may not manage networks;
// [*ConfigError] when the client is closed. See [Error] for the rest.
func (n *NetworkOperations) List(ctx context.Context, p ListNetworksParams) (*NetworkList, error) {
	response, err := administer(ctx, n.client, "ListNetworks", &adminv1.ListNetworksRequest{
		Name: p.Name, Scope: p.Scope, Owner: p.Owner, Domain: p.Domain, ParentId: p.ParentID,
		Ids: slices.Clone(p.IDs), Page: p.Page, PageSize: p.PageSize,
	}, n.rpc.ListNetworks)
	if err != nil {
		return nil, err
	}
	return toNetworkList(response), nil
}

// Create creates a memory network.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - p: the network to create. Name is required; ParentID places it under
//     another network, and Scope "customer" lets it take external users.
//
// It returns the network as created.
//
// Errors: [*AlreadyExistsError] when the name is already taken;
// [*InvalidRequestError] when the service refuses a field;
// [*PermissionError]; [*ConfigError] when the client is closed.
func (n *NetworkOperations) Create(ctx context.Context, p CreateNetworkParams) (*Network, error) {
	response, err := administer(ctx, n.client, "CreateNetwork", &adminv1.CreateNetworkRequest{
		Name: p.Name, ParentId: p.ParentID, Domain: p.Domain, Region: p.Region, Scope: p.Scope,
		Owner: p.Owner, Description: p.Description,
	}, n.rpc.CreateNetwork)
	if err != nil {
		return nil, err
	}
	network := toNetwork(response)
	return &network, nil
}

// Update changes a network's name, parent, scope, owner or description. Only
// the fields given change.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - networkID: the network to change.
//   - p: the changes; a nil field is left as it is, and a pointer to "" clears
//     the field.
//
// It returns the network as it now is.
//
// Errors: [*InvalidRequestError] when networkID is blank (nothing is sent);
// [*PreconditionFailedError] when the change is not allowed as things stand,
// such as making a network holding external users internal;
// [*NotFoundError]; [*ConfigError] when the client is closed.
func (n *NetworkOperations) Update(ctx context.Context, networkID string, p UpdateNetworkParams) (*Network, error) {
	response, err := administer(ctx, n.client, "UpdateNetwork", &adminv1.UpdateNetworkRequest{
		Id: networkID, Name: cloned(p.Name), ParentId: cloned(p.ParentID), Scope: cloned(p.Scope),
		Owner: cloned(p.Owner), Description: cloned(p.Description),
	}, n.rpc.UpdateNetwork)
	if err != nil {
		return nil, err
	}
	network := toNetwork(response)
	return &network, nil
}

// Delete deletes a network, and everything that hangs off it. It cannot be
// undone.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - networkID: the network to delete.
//
// It returns the network deleted, and how much the deletion took with it.
//
// Errors: [*InvalidRequestError] when networkID is blank (nothing is sent);
// [*NotFoundError]; [*ConfigError] when the client is closed.
func (n *NetworkOperations) Delete(ctx context.Context, networkID string) (*DeletedNetwork, error) {
	response, err := administer(ctx, n.client, "DeleteNetwork", &adminv1.DeleteNetworkRequest{Id: networkID}, n.rpc.DeleteNetwork)
	if err != nil {
		return nil, err
	}
	return &DeletedNetwork{ID: response.GetId(), Removed: maps.Clone(response.GetRemoved())}, nil
}

// ListMembers lists one page of the users placed in a network.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - networkID: the network whose members to list.
//   - p: a search and the page; the zero value lists the first page.
//
// It returns the page of members, and how many match in all.
//
// Errors: [*InvalidRequestError] when networkID is blank (nothing is sent);
// [*NotFoundError]; [*ConfigError] when the client is closed.
func (n *NetworkOperations) ListMembers(ctx context.Context, networkID string, p ListMembersParams) (*MemberList, error) {
	response, err := administer(ctx, n.client, "ListNetworkMembers", &adminv1.ListNetworkMembersRequest{
		Id: networkID, Search: p.Search, Page: p.Page, PageSize: p.PageSize,
	}, n.rpc.ListNetworkMembers)
	if err != nil {
		return nil, err
	}
	return &MemberList{Members: toMembers(response.GetMembers()), TotalCount: response.GetTotalCount()}, nil
}

// AddMember places a user in a network. A user holds one network per memory
// domain: one already placed in another network of the same domain is
// refused, naming that network, unless p.Force moves them. An external user
// can be placed only in a customer network.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - networkID: the network to place the user in.
//   - userID: the user to place; for an external user, [ExternalUser.ID], not
//     your own id for them.
//   - p: whether to move a user placed elsewhere.
//
// It returns where the user was placed, and the network they were moved out
// of, if any.
//
// Errors: [*InvalidRequestError] when networkID or userID is blank (nothing
// is sent); [*UserAlreadyAssignedNetworkError] when the user is in another
// network of the domain and p.Force is off;
// [*ExternalUserNeedsCustomerNetworkError] when an external user meets a
// network that is not a customer one; [*NotFoundError]; [*ConfigError] when
// the client is closed.
func (n *NetworkOperations) AddMember(ctx context.Context, networkID, userID string, p AddMemberParams) (*MemberPlacement, error) {
	response, err := administer(ctx, n.client, "AddNetworkMember", &adminv1.AddNetworkMemberRequest{
		Id: networkID, UserId: userID, Force: p.Force,
	}, n.rpc.AddNetworkMember)
	if err != nil {
		return nil, err
	}
	return &MemberPlacement{NetworkID: response.GetId(), UserID: response.GetUserId(), MovedFrom: response.GetMovedFrom()}, nil
}

// RemoveMember takes a user out of a network.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - networkID: the network to take the user out of.
//   - userID: the user to take out.
//
// Errors: [*InvalidRequestError] when networkID or userID is blank (nothing
// is sent); [*NotFoundError]; [*ConfigError] when the client is closed.
func (n *NetworkOperations) RemoveMember(ctx context.Context, networkID, userID string) error {
	_, err := administer(ctx, n.client, "RemoveNetworkMember", &adminv1.RemoveNetworkMemberRequest{
		Id: networkID, UserId: userID,
	}, n.rpc.RemoveNetworkMember)
	return err
}

// ListGroups lists one page of your organization's identity-provider groups.
// Groups are available to enterprise organizations only; the service refuses
// the call for any other.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - p: the filters and the page; the zero value lists the first page of
//     every group.
//
// It returns the page of groups, and how many match in all.
//
// Errors: [*PermissionError] outside an enterprise organization;
// [*ConfigError] when the client is closed. See [Error] for the rest.
func (n *NetworkOperations) ListGroups(ctx context.Context, p ListGroupsParams) (*GroupList, error) {
	response, err := administer(ctx, n.client, "ListGroups", &adminv1.ListGroupsRequest{
		Name: p.Name, NetworkId: p.NetworkID, Ids: slices.Clone(p.IDs), Page: p.Page, PageSize: p.PageSize,
	}, n.rpc.ListGroups)
	if err != nil {
		return nil, err
	}
	return toGroupList(response), nil
}

// ListGroupMembers lists the users in an identity-provider group. The
// identity provider decides who is in a group; this only reads it.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - groupID: the group whose members to list.
//
// It returns the group's members.
//
// Errors: [*InvalidRequestError] when groupID is blank (nothing is sent);
// [*PermissionError] outside an enterprise organization; [*NotFoundError];
// [*ConfigError] when the client is closed.
func (n *NetworkOperations) ListGroupMembers(ctx context.Context, groupID string) ([]Member, error) {
	response, err := administer(ctx, n.client, "ListGroupMembers", &adminv1.ListGroupMembersRequest{Id: groupID}, n.rpc.ListGroupMembers)
	if err != nil {
		return nil, err
	}
	return toMembers(response.GetMembers()), nil
}

// AddGroup assigns an identity-provider group to a network, placing its
// members there. A group holds one network per memory domain, so this
// replaces the group's network in this network's domain.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - networkID: the network to assign the group to.
//   - groupID: the group to assign.
//
// Errors: [*InvalidRequestError] when networkID or groupID is blank (nothing
// is sent); [*PermissionError] outside an enterprise organization;
// [*NotFoundError]; [*ConfigError] when the client is closed.
func (n *NetworkOperations) AddGroup(ctx context.Context, networkID, groupID string) error {
	_, err := administer(ctx, n.client, "AddNetworkGroup", &adminv1.AddNetworkGroupRequest{
		Id: networkID, GroupId: groupID,
	}, n.rpc.AddNetworkGroup)
	return err
}

// RemoveGroup takes an identity-provider group out of a network. The group's
// networks in other memory domains are unaffected.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - networkID: the network to take the group out of.
//   - groupID: the group to take out.
//
// Errors: [*InvalidRequestError] when networkID or groupID is blank (nothing
// is sent); [*PermissionError] outside an enterprise organization;
// [*NotFoundError]; [*ConfigError] when the client is closed.
func (n *NetworkOperations) RemoveGroup(ctx context.Context, networkID, groupID string) error {
	_, err := administer(ctx, n.client, "RemoveNetworkGroup", &adminv1.RemoveNetworkGroupRequest{
		Id: networkID, GroupId: groupID,
	}, n.rpc.RemoveNetworkGroup)
	return err
}

// UserOperations manage your external users and their API keys, reached as
// [Client.Users]. Every method names a user by your own id for them, the
// external id they were created with. Calls need an API client's credentials,
// as [NetworkOperations] do.
type UserOperations struct {
	client *Client
	rpc    adminv1.AdminServiceClient
}

// ListUsersParams narrow [UserOperations.List].
type ListUsersParams struct {
	// Search keeps users whose external id, name or email matches this text,
	// case-insensitively.
	Search string
	// Page is the page to return, counting from 1; zero is the first.
	Page int32
	// PageSize is how many users a page holds; zero is the service's default.
	PageSize int32
}

// CreateUserParams describe a new external user.
type CreateUserParams struct {
	// Roles are the content roles the user holds, from "reader", "creator"
	// and "auditor". At least one is required.
	Roles []string
	// Name is the user's name.
	Name string
	// Email is the user's email address.
	Email string
}

// UpdateUserParams change an external user. A nil field is left as it is.
type UpdateUserParams struct {
	// Name is the new name; a pointer to "" clears it.
	Name *string
	// Email is the new email address; a pointer to "" clears it.
	Email *string
	// Roles replace the user's roles. Nil leaves them; a non-nil slice must
	// name at least one.
	Roles []string
}

// CreateKeyParams describe a new API key for an external user.
type CreateKeyParams struct {
	// Preset is the kind of key: "mcp_rw", "mcp_ro" or "audit". It must be
	// within the user's roles. Required.
	Preset string
	// Name is a name to recognise the key by.
	Name string
	// ValidUntil is when the key expires, at most three months ahead; zero is
	// three months.
	ValidUntil time.Time
}

// List lists one page of your organization's external users.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - p: a search and the page; the zero value lists the first page.
//
// It returns the page of users, and how many match in all.
//
// Errors: [*PermissionError] when the credential may not manage users;
// [*ConfigError] when the client is closed. See [Error] for the rest.
func (u *UserOperations) List(ctx context.Context, p ListUsersParams) (*ExternalUserList, error) {
	response, err := administer(ctx, u.client, "ListExternalUsers", &adminv1.ListExternalUsersRequest{
		Search: p.Search, Page: p.Page, PageSize: p.PageSize,
	}, u.rpc.ListExternalUsers)
	if err != nil {
		return nil, err
	}
	return toExternalUserList(response), nil
}

// Get fetches one external user, by your own id for them.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - externalID: your id for the user.
//
// It returns the user.
//
// Errors: [*InvalidRequestError] when externalID is blank (nothing is sent);
// [*NotFoundError] when you have no user with that id; [*ConfigError] when
// the client is closed.
func (u *UserOperations) Get(ctx context.Context, externalID string) (*ExternalUser, error) {
	response, err := administer(ctx, u.client, "GetExternalUser", &adminv1.GetExternalUserRequest{ExternalId: externalID}, u.rpc.GetExternalUser)
	if err != nil {
		return nil, err
	}
	user := toExternalUser(response)
	return &user, nil
}

// Create creates an external user: one of your own users, known to Memco by
// your id for them. The user has no sign-in of its own and never holds admin.
// It starts in no network: place it in a customer network with
// [NetworkOperations.AddMember], passing the created user's ID.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - externalID: your id for the user, unique within your organization. It
//     cannot be changed later.
//   - p: the user's roles, at least one, and optionally a name and an email.
//
// It returns the user as created.
//
// Errors: [*InvalidRequestError] when externalID is blank or p.Roles is empty
// (nothing is sent), or when the service refuses a role;
// [*AlreadyExistsError] when you already have a user with that id;
// [*ConfigError] when the client is closed.
func (u *UserOperations) Create(ctx context.Context, externalID string, p CreateUserParams) (*ExternalUser, error) {
	response, err := administer(ctx, u.client, "CreateExternalUser", &adminv1.CreateExternalUserRequest{
		ExternalId: externalID, Name: p.Name, Email: p.Email, Roles: slices.Clone(p.Roles),
	}, u.rpc.CreateExternalUser)
	if err != nil {
		return nil, err
	}
	user := toExternalUser(response)
	return &user, nil
}

// Update changes an external user's name, email or roles. Only the fields
// given change; the external id itself cannot.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - externalID: your id for the user to change.
//   - p: the changes; see [UpdateUserParams].
//
// It returns the user as it now is.
//
// Errors: [*InvalidRequestError] when externalID is blank or p.Roles is
// given but empty (nothing is sent); [*NotFoundError]; [*ConfigError] when
// the client is closed.
func (u *UserOperations) Update(ctx context.Context, externalID string, p UpdateUserParams) (*ExternalUser, error) {
	response, err := administer(ctx, u.client, "UpdateExternalUser", &adminv1.UpdateExternalUserRequest{
		ExternalId: externalID, Name: cloned(p.Name), Email: cloned(p.Email), Roles: slices.Clone(p.Roles),
	}, u.rpc.UpdateExternalUser)
	if err != nil {
		return nil, err
	}
	user := toExternalUser(response)
	return &user, nil
}

// Delete deletes an external user, and every API key it holds.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - externalID: your id for the user to delete.
//
// Errors: [*InvalidRequestError] when externalID is blank (nothing is sent);
// [*NotFoundError]; [*ConfigError] when the client is closed.
func (u *UserOperations) Delete(ctx context.Context, externalID string) error {
	_, err := administer(ctx, u.client, "DeleteExternalUser", &adminv1.DeleteExternalUserRequest{ExternalId: externalID}, u.rpc.DeleteExternalUser)
	return err
}

// ListKeys lists an external user's API keys, without their values.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - externalID: your id for the user whose keys to list.
//
// It returns the user's keys.
//
// Errors: [*InvalidRequestError] when externalID is blank (nothing is sent);
// [*NotFoundError]; [*ConfigError] when the client is closed.
func (u *UserOperations) ListKeys(ctx context.Context, externalID string) ([]ExternalUserKey, error) {
	response, err := administer(ctx, u.client, "ListExternalUserKeys", &adminv1.ListExternalUserKeysRequest{ExternalId: externalID}, u.rpc.ListExternalUserKeys)
	if err != nil {
		return nil, err
	}
	var keys []ExternalUserKey
	for _, key := range response.GetKeys() {
		keys = append(keys, toExternalUserKey(key))
	}
	return keys, nil
}

// CreateKey creates an API key acting as an external user, such as one for
// that user's own agent to reach Memco over MCP. The key's value is returned
// here and never again.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - externalID: your id for the user the key acts as.
//   - p: the kind of key, and optionally a name and an expiry.
//
// It returns the key's description, and its value.
//
// Errors: [*InvalidRequestError] when externalID is blank (nothing is sent),
// or when the preset is beyond the user's roles; [*InternalError] when the
// service returns a value without the key it belongs to; [*NotFoundError];
// [*ConfigError] when the client is closed.
func (u *UserOperations) CreateKey(ctx context.Context, externalID string, p CreateKeyParams) (*CreatedKey, error) {
	request := &adminv1.CreateExternalUserKeyRequest{ExternalId: externalID, Preset: p.Preset, Name: p.Name}
	if !p.ValidUntil.IsZero() {
		request.ValidUntil = p.ValidUntil.Unix()
	}
	response, err := administer(ctx, u.client, "CreateExternalUserKey", request, u.rpc.CreateExternalUserKey)
	if err != nil {
		return nil, err
	}
	return toCreatedKey(response)
}

// DeleteKey revokes one of an external user's API keys.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - externalID: your id for the user the key belongs to.
//   - keyID: the key to revoke, as [ExternalUserKey.ID] gives it.
//
// Errors: [*InvalidRequestError] when externalID or keyID is blank (nothing
// is sent); [*NotFoundError] when the user holds no such key; [*ConfigError]
// when the client is closed.
func (u *UserOperations) DeleteKey(ctx context.Context, externalID, keyID string) error {
	_, err := administer(ctx, u.client, "DeleteExternalUserKey", &adminv1.DeleteExternalUserKeyRequest{
		ExternalId: externalID, KeyId: keyID,
	}, u.rpc.DeleteExternalUserKey)
	return err
}

// administer checks an administration request, then sends it under the
// client's own credential.
func administer[Req proto.Message, Resp any](ctx context.Context, c *Client, rpc string, req Req,
	method func(context.Context, Req, ...grpc.CallOption) (Resp, error)) (Resp, error) {
	if err := admin.Check(req); err != nil {
		var zero Resp
		return zero, public(err)
	}
	return unary(ctx, c, c.credential, rpc, req, method)
}
