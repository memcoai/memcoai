/**
 * Client-side argument checks performed before any request is sent.
 *
 * Only **structural** rules live here: a blank value, a missing required
 * combination of arguments, an empty batch. Those follow from the shape of the
 * contract and cannot go out of date.
 *
 * Numeric limits are never *compiled in* here. The service owns those, and a
 * value baked into the SDK goes stale the moment the service changes one: an
 * older client would keep rejecting requests the service had started accepting,
 * locally, with no way for the caller to tell why.
 *
 * They are applied all the same, but only once the service has reported them on
 * a `listDomains` response — see `Known` in `./limits.js`. The helpers below
 * that take a cap treat zero as "nothing was reported" and check nothing. Two
 * of the caps
 * **trim** rather than refuse, because the service trims: raising would reject
 * a call it would have accepted.
 *
 * Every failure throws a {@link MemcoInvalidRequestError} with an
 * `INVALID_ARGUMENT` status, so a caller handles a local rejection and a
 * server-side one the same way.
 *
 * The field names in the messages are deliberately snake_case — `session_id`,
 * `operation_id`, `memory_idx`. They name the field of the wire contract the
 * caller is being told about, which is the spelling the service itself uses
 * when it refuses the same value. Everything else in this package is camelCase.
 */

import { status } from '@grpc/grpc-js'

import { MemcoInvalidRequestError } from '../errors.js'
import type {
  FeedbackRating,
  ImportedInsight,
  ImportedMemory,
  Tag
} from '../types.js'
import { ROOT, getLogger } from './logging.js'

const log = getLogger(`${ROOT}.validate`)

/**
 * Sentinel opening a new memory instead of enriching an existing one.
 *
 * Case-sensitive: `"New"` is treated as an ordinary handle, not the sentinel.
 * This is a value the contract defines, not a limit the service tunes.
 */
export const NEW_MEMORY = 'new'

/**
 * Count the characters of a string the way the service does.
 *
 * A cap the service publishes counts code points, which is not what
 * `String.length` reports: that counts UTF-16 units, so a single emoji counts
 * as two and a run of them can be refused locally at half the length the
 * service would have accepted. Getting this wrong misjudges only non-Latin
 * text, which is exactly the kind of bug that survives review.
 *
 * Counted by walking rather than with `Array.from(value).length`, which is the
 * obvious spelling and allocates one array element per code point. The value
 * being measured is the caller's own content, and until the service reports a
 * cap there is no bound on it: a 20 MB body costs nine figures of bytes to
 * count that way, all of it to produce one integer.
 *
 * @param value The text to measure.
 * @returns Its length in Unicode code points.
 */
export function countCharacters(value: string): number {
  // Coerced rather than trusted: a JavaScript caller can reach the request
  // builders with a non-string, and asking a number for its iterator throws a
  // bare TypeError.
  const characters = String(value)[Symbol.iterator]()
  let count = 0
  while (characters.next().done !== true) {
    count += 1
  }
  return count
}

/**
 * Build the rejection thrown by every check in this module.
 *
 * @param message Explanation naming the offending field.
 * @returns The error to throw, carrying an `INVALID_ARGUMENT` status so it is
 *   indistinguishable from a server-side rejection.
 */
export function reject(message: string): MemcoInvalidRequestError {
  return new MemcoInvalidRequestError(status.INVALID_ARGUMENT, message)
}

/**
 * Require a non-blank string.
 *
 * `null` and `undefined` are rejected explicitly rather than left to throw a
 * `TypeError`: an un-revertible write reports its operation id as `null`, and
 * the documented flow feeds that straight back in.
 *
 * @param value The value supplied by the caller.
 * @param field Field name, used verbatim in the error message.
 * @throws A {@link MemcoInvalidRequestError} if the value is absent or blank.
 */
function checkPresent(value: string | null | undefined, field: string): void {
  // `typeof` rather than a null check alone. TypeScript stops a typed caller
  // passing a number here, but a JavaScript one has no compiler, and reading
  // .trim() off what they sent would throw a bare TypeError — the one thing
  // this SDK promises never to raise.
  if (typeof value !== 'string' || value.trim() === '') {
    throw reject(`${field} must not be empty`)
  }
}

/**
 * Validate a search or memory query.
 *
 * @param query The query text.
 * @throws A {@link MemcoInvalidRequestError} if it is blank.
 */
export function checkQuery(query: string): void {
  checkPresent(query, 'query')
}

/**
 * Validate a memory or insight title.
 *
 * @param title The title text.
 * @throws A {@link MemcoInvalidRequestError} if it is blank.
 */
export function checkTitle(title: string): void {
  checkPresent(title, 'title')
}

/**
 * Validate a memory or insight body.
 *
 * @param content The content text.
 * @throws A {@link MemcoInvalidRequestError} if it is blank.
 */
export function checkContent(content: string): void {
  checkPresent(content, 'content')
}

/**
 * Validate a handle returned by a previous response.
 *
 * @param idx The handle, copied exactly from an earlier result.
 * @param field Name to report in the error. Pass the caller's own argument name
 *   so the message points at the argument the user actually wrote.
 * @throws A {@link MemcoInvalidRequestError} if it is blank.
 */
export function checkIdx(idx: string, field = 'idx'): void {
  checkPresent(idx, field)
}

/**
 * Validate a session handle where one is required.
 *
 * @param sessionId The session handle.
 * @throws A {@link MemcoInvalidRequestError} if it is blank.
 */
export function checkSessionId(sessionId: string): void {
  checkPresent(sessionId, 'session_id')
}

/**
 * Validate a domain slug.
 *
 * @param domain The slug, as returned by `listDomains`.
 * @throws A {@link MemcoInvalidRequestError} if it is blank.
 */
export function checkDomain(domain: string): void {
  checkPresent(domain, 'domain')
}

/**
 * Validate the operation id addressing a previous write.
 *
 * @param operationId The id a create or an enrich returned.
 * @throws A {@link MemcoInvalidRequestError} if it is blank.
 */
export function checkOperationId(operationId: string): void {
  checkPresent(operationId, 'operation_id')
}

/**
 * Validate the target of an enrichment.
 *
 * @param memoryIdx The memory to enrich, or {@link NEW_MEMORY} to open one. The
 *   sentinel is case-sensitive.
 * @throws A {@link MemcoInvalidRequestError} if it is blank.
 */
export function checkMemoryIdx(memoryIdx: string): void {
  if (memoryIdx === NEW_MEMORY) {
    return
  }
  checkPresent(memoryIdx, 'memory_idx')
}

/** The domain and session a call may be scoped by. */
export interface Scope {
  /** The memory domain, if one was given. */
  domain: string | null
  /** The session handle, if one was given. */
  sessionId: string | null
}

/**
 * Require a domain, a session, or both.
 *
 * A session supplies the domain of the session it names, so either alone is
 * sufficient. Passing both is allowed and left for the service to resolve.
 *
 * A half that was given but left blank is still refused, which is what tells a
 * caller passing `domain: ''` from a config file that the config is empty
 * rather than that the argument is optional.
 *
 * @param scope What the caller named the domain by.
 * @throws A {@link MemcoInvalidRequestError} if neither was given, or if a
 *   value that was given is blank.
 */
export function checkScope(scope: Scope): void {
  // A JavaScript caller can reach this with null, where reading .domain off it
  // would throw a bare TypeError. Nothing is named either way, so the answer is
  // the same message they would get for an empty object.
  if (scope == null || typeof scope !== 'object') {
    throw reject(
      'pass a domain or a session_id: a request needs one of them to name a domain'
    )
  }
  if (!scope.domain && !scope.sessionId) {
    throw reject(
      'pass a domain or a session_id: a request needs one of them to name a domain'
    )
  }
  // `typeof` rather than a test against null, so a JavaScript caller who left
  // the property off entirely counts as having given nothing.
  if (typeof scope.domain === 'string') {
    checkDomain(scope.domain)
  }
  if (typeof scope.sessionId === 'string') {
    checkSessionId(scope.sessionId)
  }
}

/**
 * Copy an iterable into an array, refusing what cannot be iterated.
 *
 * Spreading is what makes a generator safe here, and it is also what a
 * JavaScript caller's number or plain object would throw a bare `TypeError`
 * on — the one failure this SDK promises never to produce.
 *
 * @param values What the caller supplied.
 * @param refusal The message to reject a non-iterable with.
 * @returns The values, as an array that can be walked again.
 * @throws A {@link MemcoInvalidRequestError} if it cannot be iterated.
 */
function iterated<T>(
  values: Iterable<T> | null | undefined,
  refusal: string
): T[] {
  if (values == null) {
    return []
  }
  if (typeof (values as Iterable<T>)[Symbol.iterator] !== 'function') {
    throw reject(refusal)
  }
  return [...values]
}

/**
 * Validate the tags on a search or a write, and materialise them.
 *
 * A blank tag is worth catching: a tag type that *filters* rather than boosts
 * narrows a search to nothing, so an empty one returns no memories at all and
 * is indistinguishable from "nothing is known about this".
 *
 * @param tags The tags supplied by the caller, if any.
 * @param field Field name, used verbatim in the error message. A call carrying
 *   more than one set of tags names which one these are.
 * @returns The tags as an array, empty when none were given.
 * @throws A {@link MemcoInvalidRequestError} if a tag's type or value is blank.
 */
export function checkTags(
  tags: Iterable<Tag> | null | undefined,
  field = 'tag'
): Tag[] {
  const materialised = iterated(tags, `${field}s must be a sequence of tags`)
  for (const tag of materialised) {
    // A null entry would throw a bare TypeError on the property read below.
    if (tag == null || typeof tag !== 'object') {
      throw reject(`${field} must not be empty`)
    }
    checkPresent(tag.type, `${field} type`)
    checkPresent(tag.value, `${field} value`)
  }
  return materialised
}

/**
 * Validate the source handles cited by an enrichment, and materialise them.
 *
 * @param sources Handles of the memories this addition draws on, if any.
 * @returns The handles as an array, empty when none were given.
 * @throws A {@link MemcoInvalidRequestError} if a bare string was passed
 *   instead of a sequence, or if any handle is blank.
 */
export function checkSources(
  sources: Iterable<string> | null | undefined
): string[] {
  if (typeof sources === 'string') {
    // TypeScript refuses a string where string[] is declared, so this is dead
    // for a typed caller — and kept for the JavaScript one, who has no compiler
    // to catch it. Walking a string would cite one memory per character and
    // attribute the write to handles that do not exist.
    throw reject('sources must be a sequence of handles, not a single string')
  }
  const materialised = [...(sources ?? [])]
  for (const source of materialised) {
    checkIdx(source, 'sources entry')
  }
  return materialised
}

/**
 * Reject a value longer than a cap the service reported.
 *
 * @param value The value supplied by the caller.
 * @param field Field name, used verbatim in the error message.
 * @param cap The reported cap. Zero means none was reported, so nothing is
 *   checked.
 * @throws A {@link MemcoInvalidRequestError} if the value exceeds the cap.
 */
export function checkWithin(value: string, field: string, cap: number): void {
  if (!cap) {
    return
  }
  const length = countCharacters(value)
  if (length > cap) {
    throw reject(
      `${field} is ${length} characters, which exceeds the limit of ${cap}`
    )
  }
}

/**
 * Reject a batch larger than a cap the service reported.
 *
 * @param count How many entries the caller supplied.
 * @param field Field name, used verbatim in the error message.
 * @param cap The reported cap. Zero means none was reported.
 * @throws A {@link MemcoInvalidRequestError} if the count exceeds the cap.
 */
export function checkCount(count: number, field: string, cap: number): void {
  if (cap && count > cap) {
    throw reject(
      `${field} has ${count} entries, which exceeds the limit of ${cap}`
    )
  }
}

/**
 * Trim a list to a cap the service applies by trimming.
 *
 * The service keeps the first `cap` entries and drops the rest, so a client
 * that threw here would reject a call the service would have accepted.
 *
 * @param values The entries the caller supplied.
 * @param cap The reported cap. Zero means none was reported.
 * @param field Field name, used verbatim in the log record.
 * @returns The entries, trimmed if a cap applies.
 * @typeParam T What the list holds. Nothing here reads the entries.
 */
export function trim<T>(values: T[], cap: number, field: string): T[] {
  if (!(cap && values.length > cap)) {
    return values
  }
  // Dropping the caller's data is invisible in the result, so this record is
  // the only way to find out that it happened.
  log.debug(
    "%s trimmed from %d to %d by the service's cap",
    field,
    values.length,
    cap
  )
  return values.slice(0, cap)
}

/**
 * Validate a batch of ratings.
 *
 * @param feedback The ratings to record.
 * @throws A {@link MemcoInvalidRequestError} if the batch is empty or holds a
 *   rating whose handle is blank.
 */
export function checkFeedback(
  feedback: Iterable<FeedbackRating>
): FeedbackRating[] {
  // Materialised here and returned, as checkTags and checkSources are, because
  // the caller walks the result again to build the request. Validating the
  // caller's own object and then walking that again would send nothing at all
  // when they passed a generator, and the call would report success.
  //
  // The `?? []` also covers a JavaScript caller's null, which spreading would
  // otherwise throw a bare TypeError on.
  const materialised = iterated(
    feedback,
    'feedback must contain at least one rating'
  )
  if (materialised.length === 0) {
    throw reject('feedback must contain at least one rating')
  }
  for (const rating of materialised) {
    if (rating == null || typeof rating !== 'object') {
      throw reject('feedback idx must not be empty')
    }
    checkIdx(rating.idx, 'feedback idx')
  }
  return materialised
}

/**
 * Validate a batch of memories to import.
 *
 * Every message names the position of the entry it is about. A batch gives the
 * caller no handle to address one memory by, so the index is the only way to
 * say which of five hundred entries is the problem.
 *
 * @param memories The memories to contribute.
 * @throws A {@link MemcoInvalidRequestError} if the batch is empty, if an entry
 *   carries no query or no insight, if a bare string was passed as an entry's
 *   queries, or if any field of an entry is blank.
 */
/**
 * An {@link ImportedMemory} whose collections have been materialised.
 *
 * `ImportedMemory` declares its three collections as `Iterable`, because that
 * is what a caller may hand in — a list, a generator, a `map`. This is what
 * comes back out of {@link checkImportMemories}: the same memory with each of
 * them copied into an array that can be counted and walked again.
 */
export interface MaterialisedMemory {
  /** The queries, materialised. */
  queries: string[]
  /** The insights, materialised. */
  insights: ImportedInsight[]
  /** The tags, materialised — an empty array where the caller gave none. */
  tags: Tag[]
}

export function checkImportMemories(
  memories: Iterable<ImportedMemory>,
  offset = 0
): MaterialisedMemory[] {
  const taken = iterated(memories, 'memories must contain at least one memory')
  if (taken.length === 0) {
    throw reject('memories must contain at least one memory')
  }
  return taken.map((memory, index) => {
    const where = `memories[${offset + index}]`
    // A null entry would throw a bare TypeError on the property read below.
    if (memory == null || typeof memory !== 'object') {
      throw reject(`${where} must contain at least one query`)
    }
    if (typeof memory.queries === 'string') {
      // Dead for a typed caller and kept for the JavaScript one, as in
      // checkSources: walking a string would file one query per character and
      // make the memory findable by nothing.
      throw reject(
        `${where} queries must be a sequence of queries, not a single string`
      )
    }
    const queries = iterated(
      memory.queries,
      `${where} must contain at least one query`
    )
    const insights = iterated(
      memory.insights,
      `${where} must contain at least one insight`
    )
    if (queries.length === 0) {
      throw reject(`${where} must contain at least one query`)
    }
    if (insights.length === 0) {
      throw reject(`${where} must contain at least one insight`)
    }
    queries.forEach((query, at) => {
      checkPresent(query, `${where} queries[${at}]`)
    })
    insights.forEach((insight, at) => {
      // As above: a null entry would throw a bare TypeError on the read below.
      if (insight == null || typeof insight !== 'object') {
        throw reject(`${where} insights[${at}] title must not be empty`)
      }
      checkPresent(insight.title, `${where} insights[${at}] title`)
      checkPresent(insight.content, `${where} insights[${at}] content`)
    })
    // Validated here rather than left to the request builder so the message
    // names the entry, and the checked list is what is carried forward.
    const tags = checkTags(memory.tags, `${where} tag`)
    return { queries, insights, tags }
  })
}
