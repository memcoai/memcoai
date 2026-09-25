/**
 * Request construction and argument validation, shared by every operation.
 *
 * Keeping this apart from the transport means one place decides what a call
 * carries and what is refused before it is sent, so no operation can validate
 * differently from another by accident.
 *
 * Each builder validates the structural rules first, then applies whatever caps
 * the service has reported, then constructs the message. That order is what
 * keeps the messages useful: a blank field is named as blank rather than as
 * "zero characters, which exceeds the limit of nothing".
 */

import { status } from '@grpc/grpc-js'

import { MemcoError, MemcoNotFoundError } from '../errors.js'
import type {
  DataSource,
  FeedbackRating,
  ImportedMemory,
  Tag
} from '../types.js'
import type { ClientConfig } from './config.js'
import * as pb from './gen.js'
import { Known } from './limits.js'
import * as validate from './validate.js'

/**
 * Construct a request message, reporting a rejected value as a typed error.
 *
 * A JavaScript string can hold a lone surrogate — which arrives routinely from
 * a mis-decoded filename or scraped JSON — and no protobuf implementation can
 * encode one. Message construction happens outside the RPC call, so an encoder
 * that refused it here would escape untyped and defeat the guarantee that every
 * failure a caller sees is a `MemcoError`.
 *
 * ts-proto's messages are plain objects, so nothing in this SDK's own
 * construction can throw today; the guard is what keeps that true if the
 * generated code ever starts validating on the way in.
 *
 * A rejection that is already typed passes through untouched. The tags are
 * validated inside this callable, so without that a blank tag type would be
 * reported as text the encoder refused rather than as the blank field it is.
 * JavaScript offers no way to catch one class of failure and not another, so
 * the class is tested explicitly. `instanceof` is safe here where it is not for
 * a caller: this module and `errors.ts` are always the same copy, dual package
 * build or not.
 *
 * @param build Callable constructing the message.
 * @returns The constructed message.
 * @throws A `MemcoInvalidRequestError` if a field value cannot be serialised,
 *   and whatever the callable itself rejected, unchanged.
 * @typeParam M The message being built.
 */
function built<M>(build: () => M): M {
  try {
    return build()
  } catch (error) {
    if (error instanceof MemcoError) {
      throw error
    }
    throw validate.reject(`a field value cannot be sent: ${String(error)}`)
  }
}

/**
 * Convert one public tag to its wire message.
 *
 * @param tag The tag supplied by the caller.
 * @returns The wire message, with `version` left unset when none was given: an
 *   empty string there is a version, and the service treats it as one.
 */
function toWireTag(tag: Tag): pb.Tag {
  const message: pb.Tag = { type: tag.type, value: tag.value }
  // `!= null` covers an explicit null as well as an omitted field. Assigning
  // a null here would reach the encoder as a string field holding one.
  if (tag.version != null) {
    message.version = tag.version
  }
  return message
}

/**
 * Validate public tags, apply the trimming cap, and convert them.
 *
 * Call this from inside the {@link built} callable, never before it, so that a
 * tag carrying text the encoder refuses fails as a typed error. Validation
 * failures are unaffected either way: they are already the right type, and the
 * guard rewrites only what it catches.
 *
 * @param tags The tags supplied by the caller, if any.
 * @param cap The domain's tag cap. The service trims rather than refusing, so
 *   this trims too; zero means no cap was reported.
 * @returns The wire messages, empty when no tags were given.
 * @throws A `MemcoInvalidRequestError` if a tag's type or value is blank.
 */
function wireTags(tags: Iterable<Tag> | null | undefined, cap = 0): pb.Tag[] {
  return validate.trim(validate.checkTags(tags), cap, 'tags').map(toWireTag)
}

/**
 * Convert one public rating to its wire message.
 *
 * @param rating The rating supplied by the caller.
 * @returns The wire message, with `comment` left unset when none was given.
 */
function toWireRating(rating: FeedbackRating): pb.FeedbackRating {
  const message: pb.FeedbackRating = {
    idx: rating.idx,
    relevant: rating.relevant,
    correct: rating.correct
  }
  // `!= null` for the reason toWireTag gives.
  if (rating.comment != null) {
    message.comment = rating.comment
  }
  return message
}

/**
 * Convert a public data source to its wire value.
 *
 * @param source The public enum member.
 * @returns The wire value. The two enums carry identical numbers by
 *   construction, so this is a typing concern only and changes nothing at
 *   runtime.
 */
function wireSource(source: DataSource): pb.DataSource {
  return source as number as pb.DataSource
}

/**
 * Apply the reported cap on handle-shaped values.
 *
 * @param value The handle supplied by the caller.
 * @param field Field name, used verbatim in the error message.
 * @param known What the service has reported, if anything.
 * @throws A `MemcoInvalidRequestError` if a cap is known and the handle exceeds
 *   it.
 */
function checkHandle(
  value: string,
  field: string,
  known: Known | null | undefined
): void {
  if (known && known.limits) {
    validate.checkWithin(value, field, known.limits.maxIdxCharacters)
  }
}

/**
 * Apply the reported cap on title and content, which it bounds jointly.
 *
 * @param title The title supplied by the caller.
 * @param content The content supplied by the caller.
 * @param caps What the service has reported.
 * @param field Field name, used verbatim in the error message. A call carrying
 *   more than one pair names which one this is.
 * @throws A `MemcoInvalidRequestError` if their combined length exceeds the
 *   cap.
 */
function checkTextTogether(
  title: string,
  content: string,
  caps: Known,
  field = 'title and content'
): void {
  const limit = caps.limits ? caps.limits.maxTextCharacters : 0
  if (!limit) {
    return
  }
  const total =
    validate.countCharacters(title) + validate.countCharacters(content)
  if (total > limit) {
    throw validate.reject(
      `${field} are ${total} characters together, which exceeds the combined limit of ${limit}`
    )
  }
}

/**
 * Build a `ListDomains` request.
 *
 * @returns The request message. It carries no fields: the call is the answer to
 *   "which domain?", so it takes no domain of its own.
 */
export function listDomainsRequest(): pb.ListDomainsRequest {
  return {}
}

/**
 * Build a `ListTools` request.
 *
 * @returns The request message. It carries no fields: the catalog is the same
 *   for every caller, and only availability depends on who is asking.
 */
export function listToolsRequest(): pb.ListToolsRequest {
  return {}
}

/**
 * Validate and build a `RevertMemory` request.
 *
 * @param operationId The operation id a create or an enrich returned.
 * @param known What the service has reported about its own limits, if anything.
 * @returns The request message, carrying the id under the contract's own name
 *   for the field, `opId`.
 * @throws A `MemcoInvalidRequestError` if the operation id is blank or too
 *   long.
 */
export function revertMemoryRequest(
  operationId: string,
  known?: Known | null
): pb.RevertMemoryRequest {
  validate.checkOperationId(operationId)
  checkHandle(operationId, 'operation_id', known)
  return built(() => ({ opId: operationId }))
}

/**
 * Validate and build a `StartSession` request.
 *
 * @param domain The memory domain the session belongs to.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if the domain is blank.
 */
export function startSessionRequest(domain: string): pb.StartSessionRequest {
  validate.checkDomain(domain)
  return built(() => ({ domain }))
}

/** Everything a search takes beyond the query itself. */
export interface SearchOptions {
  /** The memory domain, if the search is not scoped by a session. */
  domain: string | null
  /** The session to record this search under, if any. */
  sessionId: string | null
  /** Tags narrowing or boosting the results, if any. */
  tags?: Iterable<Tag> | null
  /** What the service has reported about its own limits, if anything. */
  known?: Known | null
}

/**
 * Validate and build a `Search` request.
 *
 * @param query The task-based query.
 * @param options The scope, the tags and what the service has reported.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if the query is invalid, or if neither a
 *   domain nor a session was given.
 */
export function searchRequest(
  query: string,
  options: SearchOptions
): pb.SearchRequest {
  const { domain, sessionId, tags, known } = options
  validate.checkQuery(query)
  validate.checkScope({ domain, sessionId })
  const caps = known ?? new Known()
  if (caps.limits) {
    validate.checkWithin(query, 'query', caps.limits.maxQueryCharacters)
  }
  const cap = caps.maxTags(domain)
  return built(() => ({
    query,
    domain: domain ?? '',
    sessionId: sessionId ?? '',
    tags: wireTags(tags, cap)
  }))
}

/**
 * Validate and build a `GetMemory` request.
 *
 * @param idx The handle to fetch.
 * @param known What the service has reported about its own limits, if anything.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if the handle is blank or too long.
 */
export function getMemoryRequest(
  idx: string,
  known?: Known | null
): pb.GetMemoryRequest {
  validate.checkIdx(idx)
  checkHandle(idx, 'idx', known)
  return built(() => ({ idx }))
}

/** Everything a create carries. */
export interface CreateMemoryOptions {
  /** What someone would search to find this memory. */
  query: string
  /** Short title for the memory. */
  title: string
  /** The knowledge being saved. */
  content: string
  /** The memory domain, if the write is not scoped by a session. */
  domain: string | null
  /** The session this memory was learned during, if any. */
  sessionId: string | null
  /** Tags describing the memory, if any. */
  tags?: Iterable<Tag> | null
  /** Who produced the content. */
  source: DataSource
  /** What the service has reported about its own limits, if anything. */
  known?: Known | null
}

/**
 * Validate and build a `CreateMemory` request.
 *
 * @param options Everything the memory is written from.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if any field is invalid, or if neither a
 *   domain nor a session was given.
 */
export function createMemoryRequest(
  options: CreateMemoryOptions
): pb.CreateMemoryRequest {
  const { query, title, content, domain, sessionId, tags, source, known } =
    options
  validate.checkQuery(query)
  validate.checkTitle(title)
  validate.checkContent(content)
  validate.checkScope({ domain, sessionId })
  const caps = known ?? new Known()
  if (caps.limits) {
    validate.checkWithin(query, 'query', caps.limits.maxQueryCharacters)
    checkTextTogether(title, content, caps)
  }
  const cap = caps.maxTags(domain)
  return built(() => ({
    query,
    title,
    content,
    domain: domain ?? '',
    sessionId: sessionId ?? '',
    tags: wireTags(tags, cap),
    source: wireSource(source)
  }))
}

/** Everything an enrichment carries. */
export interface EnrichMemoryOptions {
  /** The memory to enrich, or `"new"` to open one. */
  memoryIdx: string
  /**
   * The session the memory was returned under.
   *
   * Required, because it is what supplies the domain.
   */
  sessionId: string
  /** Short title for the addition. */
  title: string
  /** The knowledge being added. */
  content: string
  /** Tags describing the addition, if any. */
  tags?: Iterable<Tag> | null
  /** Handles of the memories this addition draws on, if any. */
  sources?: Iterable<string> | null
  /** Who produced the content. */
  source: DataSource
  /** What the service has reported about its own limits, if anything. */
  known?: Known | null
}

/**
 * Validate and build an `EnrichMemory` request.
 *
 * @param options Everything the addition is written from.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if any field is invalid.
 */
export function enrichMemoryRequest(
  options: EnrichMemoryOptions
): pb.EnrichMemoryRequest {
  const { memoryIdx, sessionId, title, content, tags, sources, source, known } =
    options
  validate.checkMemoryIdx(memoryIdx)
  validate.checkSessionId(sessionId)
  validate.checkTitle(title)
  validate.checkContent(content)
  let materialised = validate.checkSources(sources)
  const caps = known ?? new Known()
  if (caps.limits) {
    checkTextTogether(title, content, caps)
    checkHandle(memoryIdx, 'memory_idx', known)
    for (const entry of materialised) {
      checkHandle(entry, 'sources entry', known)
    }
    // The service keeps the first maxSources and drops the rest, so throwing
    // here would reject a call it would have accepted.
    materialised = validate.trim(
      materialised,
      caps.limits.maxSources,
      'sources'
    )
  }
  return built(() => ({
    memoryIdx,
    sessionId,
    title,
    content,
    // Deliberately the session-scoped cap, which is zero for any domain the
    // service publishes: an enrichment names a session and never a domain, so
    // the client cannot know which domain's tag cap applies and leaves the trim
    // to the service.
    tags: wireTags(tags, caps.maxTags(null)),
    sources: materialised,
    source: wireSource(source)
  }))
}

/** Everything a feedback call carries. */
export interface ShareFeedbackOptions {
  /** The session whose search is being rated. */
  sessionId: string
  /** The ratings to record. */
  feedback: Iterable<FeedbackRating>
  /** What the service has reported about its own limits, if anything. */
  known?: Known | null
}

/**
 * Validate and build a `ShareFeedback` request.
 *
 * @param options The session and the ratings to record under it.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if the session or any rating is invalid.
 */
export function shareFeedbackRequest(
  options: ShareFeedbackOptions
): pb.ShareFeedbackRequest {
  const { sessionId, feedback, known } = options
  validate.checkSessionId(sessionId)
  const rated = validate.checkFeedback(feedback)
  const caps = known ?? new Known()
  if (caps.limits) {
    validate.checkCount(
      rated.length,
      'feedback',
      caps.limits.maxFeedbackEntries
    )
    for (const rating of rated) {
      checkHandle(rating.idx, 'feedback idx', known)
    }
  }
  return built(() => ({
    sessionId,
    feedback: rated.map(toWireRating)
  }))
}

/** Everything an import carries beyond the memories themselves. */
export interface ImportMemoriesOptions {
  /** The memory domain, if the import is not scoped by a session. */
  domain: string | null
  /** The session this knowledge was contributed during, if any. */
  sessionId: string | null
  /** What the service has reported about its own limits, if anything. */
  known?: Known | null
}

/**
 * Validate a batch of memories and split it into the calls that carry it.
 *
 * `maxImportMemories` bounds one *call*, not one batch, and it refuses rather
 * than trims — so a batch above it is divided into groups of that size and sent
 * as several calls. A caller hands over whatever it has without having to learn
 * the number or chunk against it. Nothing is dropped: the groups partition the
 * batch in order. An unreported cap means the service rules, as everywhere
 * else, so the batch goes out whole.
 *
 * The three per-entry caps refuse too, and are checked against what the caller
 * actually supplied. The per-domain tag cap still trims, and is applied after
 * the refusing one so that the refusal stays reachable.
 *
 * @param memories The memories to contribute.
 * @param options The scope and what the service has reported.
 * @returns One `[offset, request]` per call to make, in order. The offset is
 *   the position the group's first memory held in the whole batch, which is
 *   what turns each response's own numbering back into the caller's.
 * @throws A `MemcoInvalidRequestError` if the batch or any entry is invalid, or
 *   if neither a domain nor a session was given.
 */
export function* importMemoriesRequests(
  memories: Iterable<ImportedMemory>,
  options: ImportMemoriesOptions
): Generator<[number, pb.ImportMemoriesRequest]> {
  const { domain, sessionId, known } = options
  validate.checkScope({ domain, sessionId })
  const caps = known ?? new Known()
  const limits = caps.limits
  const cap = caps.maxTags(domain)

  const oneCall = (
    taken: readonly validate.MaterialisedMemory[]
  ): pb.ImportMemoriesRequest =>
    built(() => ({
      domain: domain ?? '',
      sessionId: sessionId ?? '',
      memories: taken.map(memory => ({
        queries: [...memory.queries],
        insights: [...memory.insights].map(insight => ({
          title: insight.title,
          content: insight.content
        })),
        tags: wireTags(memory.tags, cap)
      }))
    }))

  // An unreported cap is one group holding everything, so the batch goes out
  // whole rather than against a size the SDK made up.
  const group = limits ? limits.maxImportMemories : 0
  const pending = (memories ?? [])[Symbol.iterator]()
  let offset = 0
  for (;;) {
    const raw: ImportedMemory[] = []
    // Driven by hand rather than with `for...of`, which calls `return()` on the
    // iterator when you break out of it — finishing a generator, so only the
    // first group would ever be taken and the rest of the batch would vanish.
    while (!group || raw.length < group) {
      const next = pending.next()
      if (next.done === true) {
        break
      }
      raw.push(next.value)
    }
    if (raw.length === 0) {
      break
    }
    const taken = validate.checkImportMemories(raw, offset)
    if (limits) {
      taken.forEach((memory, index) => {
        const where = `memories[${offset + index}]`
        validate.checkCount(
          memory.queries.length,
          `${where} queries`,
          limits.maxImportQueriesPerMemory
        )
        validate.checkCount(
          memory.insights.length,
          `${where} insights`,
          limits.maxImportInsightsPerMemory
        )
        validate.checkCount(
          (memory.tags ?? []).length,
          `${where} tags`,
          limits.maxImportTagsPerMemory
        )
        memory.insights.forEach((insight, at) => {
          checkTextTogether(
            insight.title,
            insight.content,
            caps,
            `${where} insights[${at}] title and content`
          )
        })
      })
    }
    yield [offset, oneCall(taken)]
    offset += taken.length
  }
  if (offset === 0) {
    // Only knowable once nothing came out, since the batch is taken lazily.
    throw validate.reject('memories must contain at least one memory')
  }
}

/**
 * Return the memory a `GetMemory` response carries.
 *
 * Presence, not truthiness: the service sends an all-default message for a
 * memory with nothing in it, and treating that as absent would report a handle
 * as unresolved when it resolved. The other way round is worse — converting an
 * unset field would hand the caller a memory with every field empty,
 * indistinguishable from a real one, instead of saying the handle found
 * nothing.
 *
 * @param response The generated response.
 * @param idx The handle that was requested, for the error message.
 * @returns The memory message.
 * @throws A {@link MemcoNotFoundError} if the response carries no memory.
 */
export function requireMemory(
  response: pb.GetMemoryResponse,
  idx: string
): pb.MemoryResult {
  if (response.memory === undefined) {
    throw new MemcoNotFoundError(
      status.NOT_FOUND,
      `no memory was returned for ${JSON.stringify(idx)}`
    )
  }
  return response.memory
}

/**
 * Leave a patch field unset when the caller gave none.
 *
 * On a patch, unset and empty mean different things — unset leaves the field
 * as it is, `''` clears it — and ts-proto writes anything but `undefined`. A
 * JavaScript `null` is a caller saying "not given", so it is folded to unset
 * rather than written as the text `null`.
 *
 * @param value The field as the caller gave it.
 * @returns The value, or `undefined` when none was given.
 */
function patched(value: string | null | undefined): string | undefined {
  return value ?? undefined
}

/**
 * Build the `IssueToken` request exchanging the client's credentials for a
 * token.
 *
 * @param config Resolved client settings holding the client credentials.
 * @returns The request message. It names no scope, which takes everything the
 *   API client was granted, and an unset `tokenLifetime` is sent as 0, which
 *   takes the service's default.
 */
export function issueTokenRequest(
  config: ClientConfig
): pb.auth.IssueTokenRequest {
  return {
    grantType: 'client_credentials',
    clientId: config.clientId ?? '',
    clientSecret: config.clientSecret ?? '',
    scope: [],
    ttlSeconds: config.tokenLifetime ?? 0
  }
}

/**
 * Build an `ImpersonateExternalUser` request minting a session's key.
 *
 * @param externalId The user the key acts as, already checked for blankness by
 *   the session opening it.
 * @returns The request message. It asks for no lifetime, which takes the
 *   service's default: the session renews its key before it expires, so a
 *   longer one would only leave a leaked key usable for longer.
 */
export function impersonateRequest(
  externalId: string
): pb.admin.ImpersonateExternalUserRequest {
  return built(() => ({ externalId, ttlMinutes: 0 }))
}

/**
 * Build an `EndImpersonation` request revoking a session's key.
 *
 * @param externalId The user the key acts as.
 * @param keyId The key to revoke, as the service named it when minting it.
 * @returns The request message.
 */
export function endImpersonationRequest(
  externalId: string,
  keyId: string
): pb.admin.EndImpersonationRequest {
  return built(() => ({ externalId, keyId }))
}

/** Everything a network listing is narrowed by. */
export interface ListNetworksOptions {
  name?: string | null
  scope?: string | null
  owner?: string | null
  domain?: string | null
  parentId?: string | null
  ids?: Iterable<string> | null
  page?: number | null
  pageSize?: number | null
}

/**
 * Validate and build a `ListNetworks` request.
 *
 * @param options The filters and the page.
 * @returns The request message, carrying only the filters given.
 * @throws A `MemcoInvalidRequestError` if `ids` is a single string.
 */
export function listNetworksRequest(
  options: ListNetworksOptions
): pb.admin.ListNetworksRequest {
  const ids = validate.checkStrings(options.ids, 'ids')
  return built(() => ({
    name: options.name ?? '',
    scope: options.scope ?? '',
    owner: options.owner ?? '',
    domain: options.domain ?? '',
    parentId: options.parentId ?? '',
    ids,
    page: options.page ?? 0,
    pageSize: options.pageSize ?? 0
  }))
}

/** Everything a new network is made from. */
export interface CreateNetworkOptions {
  name?: string | null
  parentId?: string | null
  domain?: string | null
  region?: string | null
  scope?: string | null
  owner?: string | null
  description?: string | null
}

/**
 * Build a `CreateNetwork` request.
 *
 * Nothing is checked here: every field is one the service defaults or refuses
 * on its own terms.
 *
 * @param options The network's name, where it goes, and what describes it.
 * @returns The request message.
 */
export function createNetworkRequest(
  options: CreateNetworkOptions
): pb.admin.CreateNetworkRequest {
  return built(() => ({
    name: options.name ?? '',
    parentId: options.parentId ?? '',
    domain: options.domain ?? '',
    region: options.region ?? '',
    scope: options.scope ?? '',
    owner: options.owner ?? '',
    description: options.description ?? ''
  }))
}

/** Everything a network patch may change. */
export interface UpdateNetworkOptions {
  name?: string | null
  parentId?: string | null
  scope?: string | null
  owner?: string | null
  description?: string | null
}

/**
 * Validate and build an `UpdateNetwork` request.
 *
 * @param networkId The network to change.
 * @param options What to change. A field left out is left unset.
 * @returns The request message, with only the fields given set.
 * @throws A `MemcoInvalidRequestError` if the network is blank.
 */
export function updateNetworkRequest(
  networkId: string,
  options: UpdateNetworkOptions
): pb.admin.UpdateNetworkRequest {
  validate.checkIdx(networkId, 'network_id')
  return built(() => ({
    id: networkId,
    name: patched(options.name),
    parentId: patched(options.parentId),
    scope: patched(options.scope),
    owner: patched(options.owner),
    description: patched(options.description)
  }))
}

/**
 * Validate and build a `DeleteNetwork` request.
 *
 * @param networkId The network to delete.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if the network is blank.
 */
export function deleteNetworkRequest(
  networkId: string
): pb.admin.DeleteNetworkRequest {
  validate.checkIdx(networkId, 'network_id')
  return built(() => ({ id: networkId }))
}

/** Everything a member listing is narrowed by. */
export interface ListMembersOptions {
  search?: string | null
  page?: number | null
  pageSize?: number | null
}

/**
 * Validate and build a `ListNetworkMembers` request.
 *
 * @param networkId The network whose members to list.
 * @param options A search and the page.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if the network is blank.
 */
export function listNetworkMembersRequest(
  networkId: string,
  options: ListMembersOptions
): pb.admin.ListNetworkMembersRequest {
  validate.checkIdx(networkId, 'network_id')
  return built(() => ({
    id: networkId,
    search: options.search ?? '',
    page: options.page ?? 0,
    pageSize: options.pageSize ?? 0
  }))
}

/**
 * Validate and build an `AddNetworkMember` request.
 *
 * @param networkId The network to place the user in.
 * @param userId The user to place.
 * @param force Whether to move a user already placed in another network of
 *   the same memory domain.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if the network or the user is blank.
 */
export function addNetworkMemberRequest(
  networkId: string,
  userId: string,
  force: boolean
): pb.admin.AddNetworkMemberRequest {
  validate.checkIdx(networkId, 'network_id')
  validate.checkIdx(userId, 'user_id')
  return built(() => ({ id: networkId, userId, force }))
}

/**
 * Validate and build a `RemoveNetworkMember` request.
 *
 * @param networkId The network to take the user out of.
 * @param userId The user to take out.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if the network or the user is blank.
 */
export function removeNetworkMemberRequest(
  networkId: string,
  userId: string
): pb.admin.RemoveNetworkMemberRequest {
  validate.checkIdx(networkId, 'network_id')
  validate.checkIdx(userId, 'user_id')
  return built(() => ({ id: networkId, userId }))
}

/** Everything a group listing is narrowed by. */
export interface ListGroupsOptions {
  name?: string | null
  networkId?: string | null
  ids?: Iterable<string> | null
  page?: number | null
  pageSize?: number | null
}

/**
 * Validate and build a `ListGroups` request.
 *
 * @param options The filters and the page.
 * @returns The request message, carrying only the filters given.
 * @throws A `MemcoInvalidRequestError` if `ids` is a single string.
 */
export function listGroupsRequest(
  options: ListGroupsOptions
): pb.admin.ListGroupsRequest {
  const ids = validate.checkStrings(options.ids, 'ids')
  return built(() => ({
    name: options.name ?? '',
    networkId: options.networkId ?? '',
    ids,
    page: options.page ?? 0,
    pageSize: options.pageSize ?? 0
  }))
}

/**
 * Validate and build a `ListGroupMembers` request.
 *
 * @param groupId The group whose members to list.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if the group is blank.
 */
export function listGroupMembersRequest(
  groupId: string
): pb.admin.ListGroupMembersRequest {
  validate.checkIdx(groupId, 'group_id')
  return built(() => ({ id: groupId }))
}

/**
 * Validate and build an `AddNetworkGroup` request.
 *
 * @param networkId The network to assign the group to.
 * @param groupId The group to assign.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if the network or the group is blank.
 */
export function addNetworkGroupRequest(
  networkId: string,
  groupId: string
): pb.admin.AddNetworkGroupRequest {
  validate.checkIdx(networkId, 'network_id')
  validate.checkIdx(groupId, 'group_id')
  return built(() => ({ id: networkId, groupId }))
}

/**
 * Validate and build a `RemoveNetworkGroup` request.
 *
 * @param networkId The network to take the group out of.
 * @param groupId The group to take out.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if the network or the group is blank.
 */
export function removeNetworkGroupRequest(
  networkId: string,
  groupId: string
): pb.admin.RemoveNetworkGroupRequest {
  validate.checkIdx(networkId, 'network_id')
  validate.checkIdx(groupId, 'group_id')
  return built(() => ({ id: networkId, groupId }))
}

/** Everything a user listing is narrowed by. */
export interface ListUsersOptions {
  search?: string | null
  page?: number | null
  pageSize?: number | null
}

/**
 * Build a `ListExternalUsers` request.
 *
 * @param options A search and the page.
 * @returns The request message, carrying only the filters given.
 */
export function listExternalUsersRequest(
  options: ListUsersOptions
): pb.admin.ListExternalUsersRequest {
  return built(() => ({
    search: options.search ?? '',
    page: options.page ?? 0,
    pageSize: options.pageSize ?? 0
  }))
}

/**
 * Validate and build a `GetExternalUser` request.
 *
 * @param externalId The user to fetch, by your own id for them.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if the id is blank.
 */
export function getExternalUserRequest(
  externalId: string
): pb.admin.GetExternalUserRequest {
  validate.checkIdx(externalId, 'external_id')
  return built(() => ({ externalId }))
}

/** Everything a new external user is made from. */
export interface CreateUserOptions {
  roles?: Iterable<string> | null
  name?: string | null
  email?: string | null
}

/**
 * Validate and build a `CreateExternalUser` request.
 *
 * @param externalId Your own id for the new user.
 * @param options The roles, name and email. The roles are consumed once.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if the id is blank, or `roles` is a
 *   single string or names no role.
 */
export function createExternalUserRequest(
  externalId: string,
  options: CreateUserOptions
): pb.admin.CreateExternalUserRequest {
  validate.checkIdx(externalId, 'external_id')
  const roles = validate.checkRoles(options.roles)
  return built(() => ({
    externalId,
    name: options.name ?? '',
    email: options.email ?? '',
    roles
  }))
}

/** Everything a user patch may change. */
export interface UpdateUserOptions {
  name?: string | null
  email?: string | null
  roles?: Iterable<string> | null
}

/**
 * Validate and build an `UpdateExternalUser` request.
 *
 * @param externalId The user to change.
 * @param options What to change. A field left out is left unset.
 * @returns The request message, with only the fields given set.
 * @throws A `MemcoInvalidRequestError` if the id is blank, or `roles` is given
 *   as a single string or naming no role.
 */
export function updateExternalUserRequest(
  externalId: string,
  options: UpdateUserOptions
): pb.admin.UpdateExternalUserRequest {
  validate.checkIdx(externalId, 'external_id')
  const roles = options.roles == null ? [] : validate.checkRoles(options.roles)
  return built(() => ({
    externalId,
    name: patched(options.name),
    email: patched(options.email),
    roles
  }))
}

/**
 * Validate and build a `DeleteExternalUser` request.
 *
 * @param externalId The user to delete.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if the id is blank.
 */
export function deleteExternalUserRequest(
  externalId: string
): pb.admin.DeleteExternalUserRequest {
  validate.checkIdx(externalId, 'external_id')
  return built(() => ({ externalId }))
}

/**
 * Validate and build a `ListExternalUserKeys` request.
 *
 * @param externalId The user whose keys to list.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if the id is blank.
 */
export function listExternalUserKeysRequest(
  externalId: string
): pb.admin.ListExternalUserKeysRequest {
  validate.checkIdx(externalId, 'external_id')
  return built(() => ({ externalId }))
}

/** Everything a new key is made from. */
export interface CreateKeyOptions {
  preset?: string | null
  name?: string | null
  validUntil?: Date | null
}

/**
 * Validate and build a `CreateExternalUserKey` request.
 *
 * @param externalId The user to create the key for.
 * @param options The preset, the name and the expiry.
 * @returns The request message, with the expiry as the Unix second it falls
 *   in, or 0 for the service's default.
 * @throws A `MemcoInvalidRequestError` if the id is blank, or the expiry is not
 *   a valid `Date`.
 */
export function createExternalUserKeyRequest(
  externalId: string,
  options: CreateKeyOptions
): pb.admin.CreateExternalUserKeyRequest {
  validate.checkIdx(externalId, 'external_id')
  validate.checkInstant(options.validUntil, 'valid_until')
  const validUntil = options.validUntil
  return built(() => ({
    externalId,
    name: options.name ?? '',
    preset: options.preset ?? '',
    validUntil: validUntil == null ? 0 : Math.floor(validUntil.getTime() / 1000)
  }))
}

/**
 * Validate and build a `DeleteExternalUserKey` request.
 *
 * @param externalId The user the key belongs to.
 * @param keyId The key to delete.
 * @returns The request message.
 * @throws A `MemcoInvalidRequestError` if the id or the key is blank.
 */
export function deleteExternalUserKeyRequest(
  externalId: string,
  keyId: string
): pb.admin.DeleteExternalUserKeyRequest {
  validate.checkIdx(externalId, 'external_id')
  validate.checkIdx(keyId, 'key_id')
  return built(() => ({ externalId, keyId }))
}
