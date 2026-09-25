/**
 * Network and user administration, reached as `client.networks` and
 * `client.users`.
 *
 * Held apart from the client, which owns the channel, the credential and the
 * health gate; this module owns only the calls.
 *
 * Every call here is made under the token a client is issued for an API
 * client's `clientId` and `clientSecret`, and is bounded by the scopes that API
 * client was granted, such as `network-management` and `user-management`. The
 * organization is always the API client's own, so no call names one.
 *
 * Networks are the memory networks an organization places its people in, and
 * what scopes the knowledge each of them can find. External users are your own
 * users, known to Memco by your id for them: they have no sign-in of their own,
 * and act through API keys or through a memory session opened on their behalf.
 *
 * As everywhere in this SDK, at most the first argument is positional — the
 * network, group or user the method acts on — and everything else is gathered
 * into one options object, required fields included.
 */

import * as convert from './internal/convert.js'
import type * as pb from './internal/gen.js'
import * as requests from './internal/requests.js'
import type { Caller, TimeoutOptions } from './operations.js'
import type {
  CreatedKey,
  DeletedNetwork,
  ExternalUser,
  ExternalUserKey,
  ExternalUserList,
  GroupList,
  Member,
  MemberList,
  MemberPlacement,
  Network,
  NetworkList
} from './types.js'

/**
 * What {@link NetworkOperations.list} narrows the listing by.
 *
 * Every filter is optional, and those given must all match.
 */
export interface ListNetworksOptions extends TimeoutOptions {
  /** Keeps networks with this name, matched case-insensitively. */
  name?: string
  /**
   * Keeps networks of this scope: `internal`, `customer`, or `none` for
   * networks without one.
   */
  scope?: string
  /** Keeps networks with this owner, matched case-insensitively. */
  owner?: string
  /** Keeps networks of this memory domain. */
  domain?: string
  /**
   * Keeps the children of this network. `root` keeps the root networks, one
   * per memory domain.
   */
  parentId?: string
  /**
   * Keeps only these networks. The service bounds how many one call may name.
   * An empty collection filters nothing, since on the wire it reads as not
   * given.
   */
  ids?: Iterable<string>
  /** The page to return, counting from 1. Omitted, the first. */
  page?: number
  /** How many networks a page holds. Omitted, the service's default. */
  pageSize?: number
}

/**
 * What {@link NetworkOperations.create} makes a network from.
 *
 * A network is either a root, placed in a memory domain, or the child of
 * another network, whose domain it takes.
 */
export interface CreateNetworkOptions extends TimeoutOptions {
  /** The network's name. */
  name: string
  /** The network to create this one under. Omitted, the new network is a root. */
  parentId?: string
  /**
   * The memory domain of a root network. Omitted, your organization's default.
   * A child must not name one.
   */
  domain?: string
  /**
   * The network's data residency. Omitted, your organization's default.
   * `global` replicates everywhere.
   */
  region?: string
  /**
   * `internal` or `customer`, where your organization uses scopes. An external
   * user can be placed only in a customer network.
   */
  scope?: string
  /** Who the network's knowledge belongs to. */
  owner?: string
  /** What the network is for. */
  description?: string
}

/**
 * What {@link NetworkOperations.update} changes.
 *
 * Only what is given changes. A field left out stays as it is; an empty string
 * is sent, and clears the field where the service allows it to be cleared.
 */
export interface UpdateNetworkOptions extends TimeoutOptions {
  /** The new name. */
  name?: string
  /** The network to move this one under. */
  parentId?: string
  /** The new scope, `internal` or `customer`. */
  scope?: string
  /** The new owner. */
  owner?: string
  /** The new description. */
  description?: string
}

/** What {@link NetworkOperations.listMembers} narrows the listing by. */
export interface ListMembersOptions extends TimeoutOptions {
  /** Keeps members matching this text. */
  search?: string
  /** The page to return, counting from 1. Omitted, the first. */
  page?: number
  /** How many members a page holds. Omitted, the service's default. */
  pageSize?: number
}

/** Who {@link NetworkOperations.addMember} places, and whether to move them. */
export interface AddMemberOptions extends TimeoutOptions {
  /**
   * The user to place. For an external user this is {@link ExternalUser.id},
   * not your own id for them.
   */
  userId: string
  /**
   * Whether to move a user already placed in another network of the same
   * memory domain. Off by default, so moving someone is never the accidental
   * outcome.
   */
  force?: boolean
}

/** Who {@link NetworkOperations.removeMember} takes out. */
export interface RemoveMemberOptions extends TimeoutOptions {
  /** The user to take out. */
  userId: string
}

/** What {@link NetworkOperations.listGroups} narrows the listing by. */
export interface ListGroupsOptions extends TimeoutOptions {
  /** Keeps groups with this name, matched case-insensitively. */
  name?: string
  /** Keeps groups assigned to this network. */
  networkId?: string
  /**
   * Keeps only these groups. The service bounds how many one call may name.
   * An empty collection filters nothing, since on the wire it reads as not
   * given.
   */
  ids?: Iterable<string>
  /** The page to return, counting from 1. Omitted, the first. */
  page?: number
  /** How many groups a page holds. Omitted, the service's default. */
  pageSize?: number
}

/**
 * Which group {@link NetworkOperations.addGroup} or
 * {@link NetworkOperations.removeGroup} acts on.
 */
export interface NetworkGroupOptions extends TimeoutOptions {
  /** The identity-provider group. */
  groupId: string
}

/** What {@link UserOperations.list} narrows the listing by. */
export interface ListUsersOptions extends TimeoutOptions {
  /**
   * Keeps users whose external id, name or email matches this text,
   * case-insensitively.
   */
  search?: string
  /** The page to return, counting from 1. Omitted, the first. */
  page?: number
  /** How many users a page holds. Omitted, the service's default. */
  pageSize?: number
}

/** What {@link UserOperations.create} makes an external user from. */
export interface CreateUserOptions extends TimeoutOptions {
  /**
   * The content roles the user holds, from `reader`, `creator` and `auditor`.
   * At least one.
   */
  roles: Iterable<string>
  /** The user's name. */
  name?: string
  /** The user's email address. */
  email?: string
}

/**
 * What {@link UserOperations.update} changes.
 *
 * Only what is given changes. A field left out stays as it is; an empty name or
 * email is sent, and clears it. The external id itself cannot be changed.
 */
export interface UpdateUserOptions extends TimeoutOptions {
  /** The new name. */
  name?: string
  /** The new email address. */
  email?: string
  /**
   * The roles to replace the user's with. At least one: to leave them as they
   * are, leave this out.
   */
  roles?: Iterable<string>
}

/** What {@link UserOperations.createKey} makes a key from. */
export interface CreateKeyOptions extends TimeoutOptions {
  /** The kind of key: `mcp_rw`, `mcp_ro` or `audit`. It must be within the user's roles. */
  preset: string
  /** A name to recognise the key by. */
  name?: string
  /**
   * When the key expires, at most three months ahead, sent as the whole second
   * it falls in. Omitted, three months.
   */
  validUntil?: Date
}

/** Which key {@link UserOperations.deleteKey} revokes. */
export interface DeleteKeyOptions extends TimeoutOptions {
  /** The key to revoke, as {@link ExternalUserKey.id} gives it. */
  keyId: string
}

/**
 * Memory networks, their members and their identity-provider groups, reached
 * as `client.networks`.
 *
 * @example
 * ```ts
 * await using client = await new Memco({ clientId, clientSecret }).connect()
 * const root = (await client.networks.list({ parentId: 'root', domain: 'coding' }))
 *   .networks[0]
 * const acme = await client.networks.create({
 *   name: 'Acme',
 *   parentId: root.id,
 *   scope: 'customer'
 * })
 * ```
 */
export class NetworkOperations {
  /**
   * @param stub The generated administration client to call.
   * @param call How to reach the wire, supplied by the client that owns the
   *   connection and the credential.
   *
   * @internal Constructed by {@link Memco}, never by a caller. It is reached as
   *   `client.networks`.
   */
  constructor(
    private readonly stub: pb.admin.AdminServiceClient,
    private readonly call: Caller
  ) {}

  /**
   * List one page of your organization's memory networks, narrowed by the
   * filters given.
   *
   * @param options Filters, the page, and a deadline.
   * @returns The page of networks, and how many match in all.
   * @throws MemcoInvalidRequestError If `ids` is a single string rather than a
   *   collection of them.
   *
   * @example
   * ```ts
   * const page = await client.networks.list({ parentId: 'root', domain: 'coding' })
   * console.log(page.networks.map(network => network.name))
   * ```
   */
  async list(options: ListNetworksOptions = {}): Promise<NetworkList> {
    const response = await this.call(
      this.stub.listNetworks.bind(this.stub),
      requests.listNetworksRequest(options),
      options.timeout,
      'ListNetworks'
    )
    return convert.toNetworkList(response)
  }

  /**
   * Create a memory network.
   *
   * @param options The network's name, where it goes, and a deadline.
   * @returns The network as created.
   * @throws MemcoAlreadyExistsError If the name is already taken.
   *
   * @example
   * ```ts
   * const acme = await client.networks.create({
   *   name: 'Acme',
   *   parentId: root.id,
   *   scope: 'customer'
   * })
   * ```
   */
  async create(options: CreateNetworkOptions): Promise<Network> {
    const response = await this.call(
      this.stub.createNetwork.bind(this.stub),
      requests.createNetworkRequest(options),
      options.timeout,
      'CreateNetwork'
    )
    return convert.toNetwork(response)
  }

  /**
   * Change a network's name, parent, scope, owner or description.
   *
   * @param networkId The network to change.
   * @param options What to change, and a deadline. A field left out stays as
   *   it is; an empty string clears it.
   * @returns The network as it now is.
   * @throws MemcoInvalidRequestError If `networkId` is blank.
   *
   * @example
   * ```ts
   * await client.networks.update('network-a', { name: 'Acme Corp', description: '' })
   * ```
   */
  async update(
    networkId: string,
    options: UpdateNetworkOptions = {}
  ): Promise<Network> {
    const response = await this.call(
      this.stub.updateNetwork.bind(this.stub),
      requests.updateNetworkRequest(networkId, options),
      options.timeout,
      'UpdateNetwork'
    )
    return convert.toNetwork(response)
  }

  /**
   * Delete a network, and everything that hangs off it.
   *
   * Cannot be undone. The result reports what the deletion removed with the
   * network, so its cost can be seen.
   *
   * @param networkId The network to delete.
   * @param options Per-call deadline.
   * @returns The network deleted, and how many rows the cascade removed from
   *   each table.
   * @throws MemcoInvalidRequestError If `networkId` is blank.
   *
   * @example
   * ```ts
   * const deleted = await client.networks.delete('network-a')
   * console.log(deleted.removed) // { memories: 3, network_members: 1 }
   * ```
   */
  async delete(
    networkId: string,
    options: TimeoutOptions = {}
  ): Promise<DeletedNetwork> {
    const response = await this.call(
      this.stub.deleteNetwork.bind(this.stub),
      requests.deleteNetworkRequest(networkId),
      options.timeout,
      'DeleteNetwork'
    )
    return convert.toDeletedNetwork(response)
  }

  /**
   * List one page of the users placed in a network.
   *
   * @param networkId The network whose members to list.
   * @param options A search, the page, and a deadline.
   * @returns The page of members, and how many match in all.
   * @throws MemcoInvalidRequestError If `networkId` is blank.
   *
   * @example
   * ```ts
   * const { members } = await client.networks.listMembers('network-a')
   * ```
   */
  async listMembers(
    networkId: string,
    options: ListMembersOptions = {}
  ): Promise<MemberList> {
    const response = await this.call(
      this.stub.listNetworkMembers.bind(this.stub),
      requests.listNetworkMembersRequest(networkId, options),
      options.timeout,
      'ListNetworkMembers'
    )
    return convert.toMemberList(response)
  }

  /**
   * Place a user in a network.
   *
   * A user holds one network per memory domain. One already placed in another
   * network of the same domain is refused with
   * {@link MemcoUserAlreadyAssignedNetworkError}, naming that network, unless
   * `force` is set, which moves them. An external user can be placed only in
   * a customer network; any other is refused with
   * {@link MemcoExternalUserNeedsCustomerNetworkError}.
   *
   * @param networkId The network to place the user in.
   * @param options The user, whether to move them, and a deadline.
   * @returns Where the user was placed, and the network they were moved out
   *   of, if any.
   * @throws MemcoInvalidRequestError If `networkId` or `userId` is blank.
   * @throws MemcoUserAlreadyAssignedNetworkError If the user is in another
   *   network of the domain and `force` is not set.
   * @throws MemcoExternalUserNeedsCustomerNetworkError If an external user is
   *   placed in a network that is not a customer one.
   *
   * @example
   * ```ts
   * const placed = await client.networks.addMember(acme.id, {
   *   userId: user.id,
   *   force: true
   * })
   * console.log(placed.movedFrom) // the network they left, if any
   * ```
   */
  async addMember(
    networkId: string,
    options: AddMemberOptions
  ): Promise<MemberPlacement> {
    const response = await this.call(
      this.stub.addNetworkMember.bind(this.stub),
      requests.addNetworkMemberRequest(
        networkId,
        options.userId,
        options.force ?? false
      ),
      options.timeout,
      'AddNetworkMember'
    )
    return convert.toMemberPlacement(response)
  }

  /**
   * Take a user out of a network.
   *
   * @param networkId The network to take the user out of.
   * @param options The user, and a deadline.
   * @throws MemcoInvalidRequestError If `networkId` or `userId` is blank.
   *
   * @example
   * ```ts
   * await client.networks.removeMember('network-a', { userId: 'user-a' })
   * ```
   */
  async removeMember(
    networkId: string,
    options: RemoveMemberOptions
  ): Promise<void> {
    await this.call(
      this.stub.removeNetworkMember.bind(this.stub),
      requests.removeNetworkMemberRequest(networkId, options.userId),
      options.timeout,
      'RemoveNetworkMember'
    )
  }

  /**
   * List one page of your organization's identity-provider groups.
   *
   * Groups are how an enterprise organization places its people in networks,
   * and are available to enterprise organizations only; the service refuses
   * this call for any other with {@link MemcoPermissionError}.
   *
   * @param options Filters, the page, and a deadline.
   * @returns The page of groups, and how many match in all.
   * @throws MemcoInvalidRequestError If `ids` is a single string rather than a
   *   collection of them.
   *
   * @example
   * ```ts
   * const { groups } = await client.networks.listGroups({ name: 'Support' })
   * ```
   */
  async listGroups(options: ListGroupsOptions = {}): Promise<GroupList> {
    const response = await this.call(
      this.stub.listGroups.bind(this.stub),
      requests.listGroupsRequest(options),
      options.timeout,
      'ListGroups'
    )
    return convert.toGroupList(response)
  }

  /**
   * List the users in an identity-provider group.
   *
   * The identity provider manages who is in a group; this only reads it.
   * Available to enterprise organizations only.
   *
   * @param groupId The group whose members to list.
   * @param options Per-call deadline.
   * @returns The group's members.
   * @throws MemcoInvalidRequestError If `groupId` is blank.
   *
   * @example
   * ```ts
   * const members = await client.networks.listGroupMembers('group-a')
   * ```
   */
  async listGroupMembers(
    groupId: string,
    options: TimeoutOptions = {}
  ): Promise<readonly Member[]> {
    const response = await this.call(
      this.stub.listGroupMembers.bind(this.stub),
      requests.listGroupMembersRequest(groupId),
      options.timeout,
      'ListGroupMembers'
    )
    return convert.toGroupMembers(response)
  }

  /**
   * Assign an identity-provider group to a network, placing its members there.
   *
   * A group holds one network per memory domain, so this replaces the group's
   * network in this network's domain. Available to enterprise organizations
   * only.
   *
   * @param networkId The network to assign the group to.
   * @param options The group, and a deadline.
   * @throws MemcoInvalidRequestError If `networkId` or `groupId` is blank.
   *
   * @example
   * ```ts
   * await client.networks.addGroup('network-support', { groupId: 'group-a' })
   * ```
   */
  async addGroup(
    networkId: string,
    options: NetworkGroupOptions
  ): Promise<void> {
    await this.call(
      this.stub.addNetworkGroup.bind(this.stub),
      requests.addNetworkGroupRequest(networkId, options.groupId),
      options.timeout,
      'AddNetworkGroup'
    )
  }

  /**
   * Take an identity-provider group out of a network.
   *
   * The group's networks in other memory domains are unaffected. Available to
   * enterprise organizations only.
   *
   * @param networkId The network to take the group out of.
   * @param options The group, and a deadline.
   * @throws MemcoInvalidRequestError If `networkId` or `groupId` is blank.
   *
   * @example
   * ```ts
   * await client.networks.removeGroup('network-support', { groupId: 'group-a' })
   * ```
   */
  async removeGroup(
    networkId: string,
    options: NetworkGroupOptions
  ): Promise<void> {
    await this.call(
      this.stub.removeNetworkGroup.bind(this.stub),
      requests.removeNetworkGroupRequest(networkId, options.groupId),
      options.timeout,
      'RemoveNetworkGroup'
    )
  }
}

/**
 * External users and their API keys, reached as `client.users`.
 *
 * Every method names a user by your own id for them, the `externalId` they
 * were created with.
 *
 * @example
 * ```ts
 * await using client = await new Memco({ clientId, clientSecret }).connect()
 * const user = await client.users.create('customer-42', {
 *   roles: ['creator'],
 *   name: 'Ada'
 * })
 * ```
 */
export class UserOperations {
  /**
   * @param stub The generated administration client to call.
   * @param call How to reach the wire, supplied by the client that owns the
   *   connection and the credential.
   *
   * @internal Constructed by {@link Memco}, never by a caller. It is reached as
   *   `client.users`.
   */
  constructor(
    private readonly stub: pb.admin.AdminServiceClient,
    private readonly call: Caller
  ) {}

  /**
   * List one page of your organization's external users.
   *
   * @param options A search, the page, and a deadline.
   * @returns The page of users, and how many match in all.
   *
   * @example
   * ```ts
   * const page = await client.users.list({ search: 'acme' })
   * console.log(page.externalUsers.map(user => user.externalId))
   * ```
   */
  async list(options: ListUsersOptions = {}): Promise<ExternalUserList> {
    const response = await this.call(
      this.stub.listExternalUsers.bind(this.stub),
      requests.listExternalUsersRequest(options),
      options.timeout,
      'ListExternalUsers'
    )
    return convert.toExternalUserList(response)
  }

  /**
   * Fetch one external user, by your own id for them.
   *
   * @param externalId Your id for the user.
   * @param options Per-call deadline.
   * @returns The user.
   * @throws MemcoInvalidRequestError If `externalId` is blank.
   * @throws MemcoNotFoundError If you have no user with that id.
   *
   * @example
   * ```ts
   * const user = await client.users.get('customer-42')
   * ```
   */
  async get(
    externalId: string,
    options: TimeoutOptions = {}
  ): Promise<ExternalUser> {
    const response = await this.call(
      this.stub.getExternalUser.bind(this.stub),
      requests.getExternalUserRequest(externalId),
      options.timeout,
      'GetExternalUser'
    )
    return convert.toExternalUser(response)
  }

  /**
   * Create an external user: one of your own users, known to Memco by your id
   * for them.
   *
   * The user has no sign-in of its own, and never holds `admin`. It starts in
   * no network: place it in a customer network with
   * {@link NetworkOperations.addMember}, passing the created user's
   * {@link ExternalUser.id}.
   *
   * @param externalId Your id for the user, unique within your organization. It
   *   cannot be changed later.
   * @param options The user's roles, name and email, and a deadline.
   * @returns The user as created.
   * @throws MemcoInvalidRequestError If `externalId` is blank, or `roles` is a
   *   single string or names no role.
   * @throws MemcoAlreadyExistsError If you already have a user with that id.
   *
   * @example
   * ```ts
   * const user = await client.users.create('customer-42', {
   *   roles: ['reader', 'creator'],
   *   name: 'Ada'
   * })
   * ```
   */
  async create(
    externalId: string,
    options: CreateUserOptions
  ): Promise<ExternalUser> {
    const response = await this.call(
      this.stub.createExternalUser.bind(this.stub),
      requests.createExternalUserRequest(externalId, options),
      options.timeout,
      'CreateExternalUser'
    )
    return convert.toExternalUser(response)
  }

  /**
   * Change an external user's name, email or roles.
   *
   * @param externalId Your id for the user to change.
   * @param options What to change, and a deadline. A field left out stays as
   *   it is; an empty name or email clears it.
   * @returns The user as it now is.
   * @throws MemcoInvalidRequestError If `externalId` is blank, or `roles` is
   *   given as a single string or naming no role.
   *
   * @example
   * ```ts
   * await client.users.update('customer-42', { roles: ['reader'] })
   * ```
   */
  async update(
    externalId: string,
    options: UpdateUserOptions = {}
  ): Promise<ExternalUser> {
    const response = await this.call(
      this.stub.updateExternalUser.bind(this.stub),
      requests.updateExternalUserRequest(externalId, options),
      options.timeout,
      'UpdateExternalUser'
    )
    return convert.toExternalUser(response)
  }

  /**
   * Delete an external user, and every API key it holds.
   *
   * @param externalId Your id for the user to delete.
   * @param options Per-call deadline.
   * @throws MemcoInvalidRequestError If `externalId` is blank.
   *
   * @example
   * ```ts
   * await client.users.delete('customer-42')
   * ```
   */
  async delete(
    externalId: string,
    options: TimeoutOptions = {}
  ): Promise<void> {
    await this.call(
      this.stub.deleteExternalUser.bind(this.stub),
      requests.deleteExternalUserRequest(externalId),
      options.timeout,
      'DeleteExternalUser'
    )
  }

  /**
   * List an external user's API keys, without their values.
   *
   * @param externalId Your id for the user whose keys to list.
   * @param options Per-call deadline.
   * @returns The user's keys. A value is shown only once, when its key is
   *   created, so none is here.
   * @throws MemcoInvalidRequestError If `externalId` is blank.
   *
   * @example
   * ```ts
   * const keys = await client.users.listKeys('customer-42')
   * console.log(keys.map(key => [key.name, key.validUntil]))
   * ```
   */
  async listKeys(
    externalId: string,
    options: TimeoutOptions = {}
  ): Promise<readonly ExternalUserKey[]> {
    const response = await this.call(
      this.stub.listExternalUserKeys.bind(this.stub),
      requests.listExternalUserKeysRequest(externalId),
      options.timeout,
      'ListExternalUserKeys'
    )
    return convert.toExternalUserKeys(response)
  }

  /**
   * Create an API key acting as an external user.
   *
   * The key's value is returned here and never again, so store it now.
   *
   * @param externalId Your id for the user the key acts as.
   * @param options The kind of key, its name and expiry, and a deadline.
   * @returns The key's description, and its value.
   * @throws MemcoInvalidRequestError If `externalId` is blank, or `validUntil`
   *   is not a valid `Date`.
   * @throws MemcoInternalError If the service returns a value without the key
   *   it belongs to.
   *
   * @example
   * ```ts
   * const created = await client.users.createKey('customer-42', {
   *   preset: 'mcp_ro',
   *   name: 'ci'
   * })
   * storeSecret(created.value) // shown only this once
   * ```
   */
  async createKey(
    externalId: string,
    options: CreateKeyOptions
  ): Promise<CreatedKey> {
    const response = await this.call(
      this.stub.createExternalUserKey.bind(this.stub),
      requests.createExternalUserKeyRequest(externalId, options),
      options.timeout,
      'CreateExternalUserKey'
    )
    return convert.toCreatedKey(response)
  }

  /**
   * Revoke one of an external user's API keys.
   *
   * @param externalId Your id for the user the key belongs to.
   * @param options The key, and a deadline.
   * @throws MemcoInvalidRequestError If `externalId` or `keyId` is blank.
   * @throws MemcoNotFoundError If the user holds no such key.
   *
   * @example
   * ```ts
   * await client.users.deleteKey('customer-42', { keyId: created.key.id })
   * ```
   */
  async deleteKey(
    externalId: string,
    options: DeleteKeyOptions
  ): Promise<void> {
    await this.call(
      this.stub.deleteExternalUserKey.bind(this.stub),
      requests.deleteExternalUserKeyRequest(externalId, options.keyId),
      options.timeout,
      'DeleteExternalUserKey'
    )
  }
}
