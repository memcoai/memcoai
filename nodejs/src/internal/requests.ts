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
