/**
 * The types every operation returns, and the ones it takes.
 *
 * Two conventions run through them:
 *
 * - A string the service left empty becomes `null` where absence is meaningful
 *   — an operation id, a notice, a reference, a piece of advice. The parts of
 *   {@link Instructions} stay `''`, because "nothing to say" is a real state
 *   there rather than a missing value.
 * - A date arrives as an ISO `YYYY-MM-DD` string, not a `Date`. A `Date` is a
 *   timestamp, and reading a date-only value as one silently attaches the
 *   reader's time zone to it; `updated` would land a day early for anyone west
 *   of UTC. Anything the service sends that is not exactly `YYYY-MM-DD` becomes
 *   `null` rather than throwing.
 */

/**
 * Who produced a piece of content.
 *
 * The operator source is deliberately absent: it is written only by the memory
 * operators and is not a value a caller may claim.
 */
export enum DataSource {
  /** Read by the service as {@link DataSource.AGENT}. */
  UNSPECIFIED = 0,
  /** A human said it, or corrected what the agent had. */
  USER = 1,
  /** The agent worked it out itself. The SDK's default. */
  AGENT = 2
}

/**
 * What a revert actually removed.
 *
 * The service reports the effect rather than restating what was asked for, so
 * every member arrives on a call that did not throw. `NOT_FOUND`, `EXPIRED` and
 * `REFUSED` describe caller-visible state, and nothing is raised to say the
 * revert did not happen: {@link RevertResult.outcome} is the only place it is
 * said.
 *
 * `NOT_FOUND` is the one to expect first after a write. A write is accepted
 * asynchronously, so an operation id whose ingestion has not finished answers
 * to nothing yet, and trying again shortly is the remedy.
 *
 * An outcome this SDK predates arrives as {@link RevertOutcome.UNSPECIFIED}
 * rather than as a raw integer, so a value no case covers cannot reach a
 * `switch` written against an older contract.
 */
export enum RevertOutcome {
  /** The service reported an outcome this SDK does not know. */
  UNSPECIFIED = 0,
  /** The reverted insight was its memory's last, so the memory went too. */
  MEMORY_REMOVED = 1,
  /** An addition to an existing memory was removed. */
  ADDITION_REMOVED = 2,
  /** The insight was removed, and no memory was involved either way. */
  ENTRY_REMOVED = 3,
  /**
   * The write was folded into an existing insight as an endorsement rather
   * than added as new, so the caller's duplicate was removed and the endorsed
   * insight is untouched.
   */
  MERGED = 4,
  /** No write answers to that operation id. */
  NOT_FOUND = 5,
  /** The write is older than the window in which it can be undone. */
  EXPIRED = 6,
  /** The service declined to undo this write. */
  REFUSED = 7
}

/**
 * What became of one memory in an import.
 *
 * The two failures do not share a remedy. `REJECTED` means the entry itself
 * was not usable and {@link ImportOutcome.errors} says what to fix; `ERROR`
 * means it was usable but was not queued, and resubmitting it is the whole of
 * the fix.
 *
 * Resending is safe either way. The service writes an imported memory under an
 * identity derived from its own content, so an entry that did land comes back
 * `DUPLICATE` on the second attempt rather than being written twice — which
 * matters, because an import mints no operation id and nothing undoes one.
 *
 * A status this SDK predates arrives as {@link ImportStatus.UNSPECIFIED} rather
 * than as a raw integer.
 */
export enum ImportStatus {
  /** The service reported a status this SDK does not know. */
  UNSPECIFIED = 0,
  /** Accepted, and being written. */
  QUEUED = 1,
  /** Refused. {@link ImportOutcome.errors} says why. */
  REJECTED = 2,
  /** Not queued. Sending it again is the remedy. */
  ERROR = 3,
  /** Already present, so nothing was written. */
  DUPLICATE = 4
}

function known<T extends number>(
  members: object,
  value: number,
  fallback: T
): T {
  // A numeric enum carries its own reverse map — `DataSource[2]` is `'AGENT'` —
  // so the compiler has already written the membership test, and keeps it in
  // step with the enum for free. Listing the members by hand beside each enum
  // would work until someone added one and forgot the list, at which point a
  // real value would start folding to UNSPECIFIED with every test still green.
  return Object.hasOwn(members, value) ? (value as T) : fallback
}

/**
 * Read a wire value as a {@link DataSource}, tolerating one this SDK predates.
 *
 * @param value The integer the service sent.
 * @returns The matching member, or {@link DataSource.UNSPECIFIED}.
 */
export function dataSourceFromWire(value: number): DataSource {
  return known(DataSource, value, DataSource.UNSPECIFIED)
}

/**
 * Read a wire value as a {@link RevertOutcome}, tolerating one this SDK predates.
 *
 * @param value The integer the service sent.
 * @returns The matching member, or {@link RevertOutcome.UNSPECIFIED}.
 */
export function revertOutcomeFromWire(value: number): RevertOutcome {
  return known(RevertOutcome, value, RevertOutcome.UNSPECIFIED)
}

/**
 * Read a wire value as an {@link ImportStatus}, tolerating one this SDK predates.
 *
 * @param value The integer the service sent.
 * @returns The matching member, or {@link ImportStatus.UNSPECIFIED}.
 */
export function importStatusFromWire(value: number): ImportStatus {
  return known(ImportStatus, value, ImportStatus.UNSPECIFIED)
}

/**
 * One tag, narrowing what a memory or a search applies to.
 *
 * The service lowercases each field and folds hyphens to underscores, so
 * `Language` and `language` are the same tag type.
 *
 * Which types exist, which of them *filter* rather than merely boost, and which
 * carry a version are all per-domain; {@link MemoryOperations.listDomains}
 * describes them. Read that before the first tagged search: a wrong boosting
 * tag costs ranking, but a wrong filtering tag narrows the search to nothing,
 * and the empty result is indistinguishable from the domain knowing nothing.
 * Start without tags if unsure.
 *
 * A `version` on a type that does not carry one is dropped by the service.
 * Tags beyond {@link DomainEntry.maxTagsPerQuery} are trimmed rather than
 * refused, so a call carrying too many succeeds with the extras silently gone.
 * The SDK writes a `debug` record when it is the one trimming; when it cannot
 * see the domain's cap the service trims instead, and nothing says so.
 */
export interface Tag {
  /** The kind of thing being named, such as `language` or `framework`. */
  type: string
  /** The thing itself, such as `typescript`. */
  value: string
  /** An optional version, on the tag types that take one. */
  version?: string
}

/**
 * What the service asks the caller to do with a result.
 *
 * Every part is a string, empty when there is nothing to say. They are meant to
 * be shown to a model rather than parsed.
 */
export interface Instructions {
  /** The main guidance for what came back. */
  readonly content: string
  /** Rules governing what may be done with it. */
  readonly policy: string
  /** How to contribute to what was returned. */
  readonly adding: string
  /** How to rate what was returned. */
  readonly rating: string
  /** What to do next. */
  readonly next: string
}

/**
 * One memory domain the caller may name.
 *
 * Most of it is prose the service authored, and {@link briefing} renders it as
 * a system prompt so a model is told what you are reading here.
 *
 * Two fields change what a call does rather than describing the domain.
 * {@link DomainEntry.filterTagTypes} is what decides whether a wrong tag costs
 * ranking or costs every result, and {@link DomainEntry.maxTagsPerQuery} is a
 * cap that trims — per-domain, which is why it sits here and not on
 * {@link Limits}.
 *
 * @see {@link Tag} — the vocabulary these fields describe.
 */
export interface DomainEntry {
  /** The handle to pass as `domain`. */
  readonly slug: string
  /** The domain's human-readable name. */
  readonly title: string
  /** What the domain holds. */
  readonly summary: string
  /** When to search this domain. */
  readonly whenToSearch: string
  /** When to save into it. */
  readonly whenToSave: string
  /** What does not belong in it. */
  readonly whatNotToSave: string
  /** The tag vocabulary, and the format it takes. */
  readonly tagsDescription: string
  /** Tag types that narrow results rather than boosting them. */
  readonly filterTagTypes: readonly string[]
  /** Tag types that carry a version. */
  readonly versionTagTypes: readonly string[]
  /** How many tags one call may carry. Extra tags are trimmed, not refused. */
  readonly maxTagsPerQuery: number
}

/**
 * The bounds the service enforces.
 *
 * Nothing here is compiled into the SDK: the values arrive from
 * `listDomains` and are applied to later calls. A client that has not called it
 * checks only structural rules, which is why every operation is still safe
 * before the first round trip.
 */
export interface Limits {
  /** Longest a query may be. Exceeding it is refused. */
  readonly maxQueryCharacters: number
  /** Longest a title and its content may be **together**. Refused. */
  readonly maxTextCharacters: number
  /** Longest any handle may be. Refused. */
  readonly maxIdxCharacters: number
  /** How many sources one enrichment may cite. Extra sources are trimmed. */
  readonly maxSources: number
  /** How many ratings one feedback call may carry. Refused. */
  readonly maxFeedbackEntries: number
  /** How many memories one import call may carry. The SDK splits the batch. */
  readonly maxImportMemories: number
  /** How many queries one imported memory may carry. Refused. */
  readonly maxImportQueriesPerMemory: number
  /** How many insights one imported memory may carry. Refused. */
  readonly maxImportInsightsPerMemory: number
  /** How many tags one imported memory may carry. Refused. */
  readonly maxImportTagsPerMemory: number
}

/**
 * What {@link MemoryOperations.listDomains} returns.
 *
 * Calling it is also how a client learns the caps it enforces locally, which is
 * why {@link Memco.connect} calls it while verifying the connection. Until it
 * has answered, every request is checked against structural rules only and the
 * service decides the rest.
 *
 * `limits` is `null` when the service reported none, and that means "check
 * nothing" rather than "every cap is zero" — reading an absent message as zeros
 * would reject every call before it was sent. A service that predates the field
 * sends nothing at all, so this is the state to expect from an older one, not a
 * malfunction.
 *
 * `deprecated` does not say whether it is the API version or this SDK build
 * that is superseded, and is not meant to: only `deprecationMessage` carries
 * the remedy, so relay the message rather than inferring a cause from the flag.
 * The SDK raises a process warning from the message rather than from the flag,
 * once per distinct message, tagged with {@link DEPRECATION_WARNING_NAME}.
 *
 * @example
 * ```ts
 * const { domains, limits } = await client.memory.listDomains()
 * for (const domain of domains) {
 *   console.log(domain.slug, domain.summary)
 * }
 * if (limits === null) {
 *   console.log('no caps reported; the service judges each call itself')
 * }
 * ```
 */
export interface DomainList {
  /** The domains this credential may name. */
  readonly domains: readonly DomainEntry[]
  /** What the service asks the caller to do with them. */
  readonly instructions: Instructions
  /** The bounds to enforce, or `null` when the service reported none. */
  readonly limits: Limits | null
  /** Whether the service considers this client out of date. */
  readonly deprecated: boolean
  /** The service's own wording of the notice. Empty when there is none. */
  readonly deprecationMessage: string
  /** When the deprecated thing stops working, as `YYYY-MM-DD`. */
  readonly sunsetDate: string | null
  /** The server commit this response came from. */
  readonly serverCommit: string
}

/**
 * One finding inside a memory.
 *
 * This is the knowledge itself: what you read, and what a rating moves. A
 * {@link Memory} is only what made it findable.
 *
 * `endorsed` and `disputed` are the reliability signal. `timesServed` counts
 * deliveries and nothing else, so a reader shown that number without the other
 * two cannot discount a disputed insight.
 *
 * `idx` decides the reach of a rating: this one names this insight, whereas the
 * enclosing {@link Memory.idx} names every insight under it. Copy it exactly —
 * a handle cannot be constructed by hand.
 *
 * `updated` is `null` in two cases: the service sent nothing, and the service
 * sent a value that is not a real calendar day. A malformed date is never
 * fatal, because the rest of the insight is still useful, so branch on it
 * rather than assuming a string is there. Enrichment edits an insight in place,
 * which makes the date the age of the knowledge rather than of the record.
 */
export interface Insight {
  /** The handle addressing this insight. */
  readonly idx: string
  /** What it is about. */
  readonly title: string
  /** What was learned. */
  readonly content: string
  /**
   * When it was last written, as `YYYY-MM-DD`.
   *
   * `null` when the service sent nothing, and also when it sent something that
   * is not a real calendar day — the two are not distinguishable here.
   */
  readonly updated: string | null
  /** How often it has been returned to anyone. */
  readonly timesServed: number
  /** How many readers rated it correct. */
  readonly endorsed: number
  /** How many readers rated it incorrect. */
  readonly disputed: number
}

/**
 * One memory a search returned.
 *
 * A memory is the container that makes knowledge findable, not the finding
 * itself — that lives one level down in {@link Memory.insights}.
 *
 * When {@link Memory.reference} is set the memory carries **no insights**, and
 * iterating them anyway reads as "nothing matched" when something did. Fetch it
 * with {@link MemoryOperations.getMemory} by its own `idx`, never the one in
 * `reference`.
 *
 * None of the `intents` is "the one that matched": a memory is retrieved whole,
 * so they say what it answers rather than why it came back this time.
 *
 * @example
 * ```ts
 * for (const memory of result.memories) {
 *   const full =
 *     memory.reference === null
 *       ? memory
 *       : await client.memory.getMemory(memory.idx)
 *   for (const insight of full.insights) {
 *     console.log(insight.title, insight.content)
 *   }
 * }
 * ```
 */
export interface Memory {
  /** The handle addressing this memory. */
  readonly idx: string
  /** The kind of memory, when the service names one. */
  readonly kind: string
  /** How often it has been returned to anyone. */
  readonly timesServed: number
  /** Questions it has been retrieved by before, oldest first. */
  readonly intents: readonly string[]
  /** The findings it holds. */
  readonly insights: readonly Insight[]
  /**
   * An earlier idx in this session that already carried this content.
   *
   * When set, the insights are empty: fetch the memory by **its own** idx, not
   * by this one, so a later rating stays with the search being worked in.
   */
  readonly reference: string | null
  /**
   * Rate this memory, using the session it was found in.
   *
   * A shortcut for {@link MemoryOperations.shareFeedback} with a single
   * {@link FeedbackRating} built from this memory's own idx.
   *
   * Bound to this memory at conversion time, which means a `Memory` is no
   * longer a plain data record: `structuredClone(memory)` throws, so a
   * {@link SearchResult} carrying one cannot be posted to a worker or
   * structurally cloned. `JSON.stringify` drops the function silently and
   * works as before.
   *
   * @throws MemcoInvalidRequestError If this memory was fetched by the
   *   top-level {@link MemoryOperations.getMemory} (not through a
   *   {@link MemoryOperations.withSession}/{@link MemoryOperations.startSession}
   *   session, and not found by a search): there is no session to record the
   *   rating against, and this fails exactly as calling `shareFeedback` with a
   *   blank `sessionId` would.
   *
   * @example
   * ```ts
   * await result.memories[0].feedback({ relevant: true, correct: true })
   * ```
   */
  readonly feedback: (rating: MemoryFeedback) => Promise<FeedbackEntry>
}

/**
 * What {@link MemoryOperations.search} returns.
 *
 * `memories` is ordered most relevant first, and the knowledge lives one level
 * down in {@link Memory.insights} — a memory is the container that makes an
 * insight findable, not the finding itself. Expect to loop twice.
 *
 * A memory whose {@link Memory.reference} is set carries **no insights**: an
 * earlier result in this same session already delivered them, and this entry
 * only says where. Fetch it with {@link MemoryOperations.getMemory} by its own
 * `idx`, never the one in `reference` — that is what keeps a later rating with
 * the search you are working in.
 *
 * `sessionId` is not always the one you sent. A search naming a domain and no
 * session opens a session and reports it here, so this is where to read the id
 * back if you mean to rate what came out.
 *
 * `notice` is a remark about the query itself rather than about its results,
 * and is `null` when there is nothing to say. `instructions` is written for a
 * model rather than for you; hand it on rather than parsing it.
 *
 * @example
 * ```ts
 * const result = await session.search('how does gRPC health checking work')
 * for (const memory of result.memories) {
 *   for (const insight of memory.insights) {
 *     console.log(insight.title, insight.updated)
 *   }
 * }
 * ```
 *
 * @see {@link MemoryOperations.shareFeedback} — rating what came back is the
 * other half of a search, and what moves an insight's reliability signal.
 */
export interface SearchResult {
  /** The session the search was recorded against. */
  readonly sessionId: string
  /** What matched, most relevant first. */
  readonly memories: readonly Memory[]
  /** Anything the service wants to say about this particular search. */
  readonly notice: string | null
  /** What the service asks the caller to do with the results. */
  readonly instructions: Instructions
}

/**
 * What {@link MemoryOperations.createMemory} and
 * {@link MemoryOperations.enrichMemory} return.
 *
 * A write is accepted asynchronously, so this addresses the *operation* rather
 * than the memory it will become. There is no idx to read here, and a revert
 * issued straight away can report {@link RevertOutcome.NOT_FOUND} because
 * ingestion has not finished.
 *
 * `operationId` is `null` when the write was accepted but no id could be
 * minted: the content is worth more than the ability to undo it, so that
 * degrades the write rather than rejecting it.
 * {@link MemoryOperations.revertMemory} takes a `string`, so narrow it rather
 * than asserting — a `null` fed back in is refused locally as a blank
 * `operation_id`, which is a confusing way to be told the write was fine.
 *
 * @example
 * ```ts
 * const written = await session.createMemory({
 *   query: 'how do I authenticate against the memory API',
 *   title: 'The Bearer prefix is case-sensitive',
 *   content: 'A lowercase bearer is rejected with UNAUTHENTICATED.'
 * })
 * if (written.operationId !== null) {
 *   await session.revertMemory(written.operationId)
 * }
 * ```
 */
export interface WriteResult {
  /** The handle `revertMemory` undoes, or `null` if the write cannot be undone. */
  readonly operationId: string | null
  /** What the service asks the caller to do next. */
  readonly instructions: Instructions
}

/**
 * One rating of one result.
 *
 * `idx` is copied exactly from a search response and cannot be constructed by
 * hand. Which one you copy decides the reach: an {@link Insight.idx} rates that
 * insight, whereas a {@link Memory.idx} rates every insight under it.
 *
 * `relevant` and `correct` are separate questions, and both are required. A
 * result can answer the query and still be wrong, or be true and unhelpful, and
 * collapsing the two is what makes a rating useless.
 *
 * The batch is gathered by walking memories and then their insights, so a
 * search whose memories are all references yields no ratings at all. An empty
 * batch is refused before anything is sent, so check the length first.
 *
 * @example
 * ```ts
 * const ratings: FeedbackRating[] = result.memories.flatMap(memory =>
 *   memory.insights.map(insight => ({
 *     idx: insight.idx,
 *     relevant: true,
 *     correct: true
 *   }))
 * )
 * if (ratings.length > 0) {
 *   await session.shareFeedback({ feedback: ratings })
 * }
 * ```
 */
export interface FeedbackRating {
  /** The idx of the result being rated, copied from a search response. */
  idx: string
  /** Whether it was a good match for the query. */
  relevant: boolean
  /** Whether its content was accurate. */
  correct: boolean
  /** An optional note on why. */
  comment?: string
}

/**
 * What {@link Memory.feedback} takes.
 *
 * {@link FeedbackRating} with the idx removed — the memory supplies its own.
 * `idx` is typed `never` rather than merely omitted, so passing a
 * `FeedbackRating`-typed variable (as opposed to an object literal) is also
 * rejected, instead of silently discarding its idx for the memory's own.
 */
export type MemoryFeedback = Omit<FeedbackRating, 'idx'> & {
  /** Never present — a memory's own idx cannot be overridden by the caller. */
  idx?: never
}

/**
 * What the service recorded for one rating.
 *
 * The verdict is echoed back rather than merely acknowledged, so a caller can
 * see what actually landed against a handle.
 *
 * `advice` is the suggestion this particular verdict earned, and is `null` when
 * the verdict suggests nothing. It addresses one result, which is why it sits
 * here rather than on {@link FeedbackResult}.
 */
export interface FeedbackEntry {
  /** The idx that was rated. */
  readonly idx: string
  /** The relevance that was recorded. */
  readonly relevant: boolean
  /** The correctness that was recorded. */
  readonly correct: boolean
  /** Anything the service wants to say back about this rating. */
  readonly advice: string | null
}

/**
 * What {@link MemoryOperations.shareFeedback} returns.
 *
 * The call names a session, so the id has to still be to hand when the results
 * are rated. That is the argument for rating a search as soon as you have used
 * it rather than at the end of a run.
 *
 * Nothing here reports a rating that was refused: a batch the service will not
 * take fails as a {@link MemcoInvalidRequestError} instead, raised locally
 * where the SDK already knows the rule and by the service otherwise. What is
 * worth reading back is {@link FeedbackEntry.advice}, which is per-result.
 */
export interface FeedbackResult {
  /** The session the ratings were recorded against. */
  readonly sessionId: string
  /** What was recorded, one entry per rating sent. */
  readonly entries: readonly FeedbackEntry[]
  /** What the service asks the caller to do next. */
  readonly instructions: Instructions
}

/**
 * What {@link MemoryOperations.revertMemory} returns.
 *
 * Holding one of these means the call succeeded, so the thing that bites is
 * reading the absence of an exception as success. `NOT_FOUND`, `EXPIRED` and
 * `REFUSED` all arrive as an outcome on a call that did not throw.
 *
 * The outcome names the effect rather than what was asked for: undoing a single
 * write can take its whole memory with it, when the insight it added was the
 * last one that memory held.
 *
 * @example
 * ```ts
 * const reverted = await client.memory.revertMemory(operationId)
 * switch (reverted.outcome) {
 *   case RevertOutcome.NOT_FOUND:
 *     console.log('ingestion may still be running; try again shortly')
 *     break
 *   case RevertOutcome.EXPIRED:
 *     console.log('outside the revert window')
 *     break
 *   default:
 *     console.log(RevertOutcome[reverted.outcome])
 * }
 * ```
 */
export interface RevertResult {
  /** The operation id that was addressed. */
  readonly operationId: string | null
  /**
   * What the revert actually removed.
   *
   * Every outcome is a successful call: `NOT_FOUND`, `EXPIRED` and `REFUSED`
   * report a caller-visible state, not a service failure.
   */
  readonly outcome: RevertOutcome
  /** What the service asks the caller to do next. */
  readonly instructions: Instructions
}

/**
 * One finding in a memory being imported.
 *
 * Both fields are required, and a blank one is rejected with a message naming
 * the entry's position in the batch — the only way to say which entry is at
 * fault, since an import mints no handle to address one by.
 *
 * They are bounded **together** by {@link Limits.maxTextCharacters} rather than
 * each on its own, so a long title spends the content's characters.
 */
export interface ImportedInsight {
  /** What it is about. */
  title: string
  /** What was learned. */
  content: string
}

/**
 * One memory being imported.
 *
 * `queries` are what someone would type to find this memory rather than a
 * description of what it holds, so several phrasings are worth giving. An entry
 * needs at least one query and at least one insight.
 *
 * All three fields take any `Iterable`, and a generator is safe: each is copied
 * into an array before being validated and sent, so nothing is walked twice and
 * silently emptied.
 *
 * Validation is per call rather than per batch, and that is the thing to know
 * before importing anything large. A batch above
 * {@link Limits.maxImportMemories} goes out as several calls, and each group is
 * checked only as its own call is built — so a blank field in a late entry
 * throws after the earlier groups have already been written, and no operation
 * id exists to undo them with. Check a long batch yourself before submitting
 * it.
 *
 * `tags` alone is optional, and the two caps on it differ:
 * {@link Limits.maxImportTagsPerMemory} refuses, as the per-entry caps on
 * queries and insights do, while {@link DomainEntry.maxTagsPerQuery} trims.
 *
 * @see {@link MemoryOperations.createMemory} — the call for knowledge learned
 * during a task, which mints an operation id you can undo with.
 */
export interface ImportedMemory {
  /** What someone would search to find this. At least one. */
  queries: Iterable<string>
  /** The findings it holds. At least one. */
  insights: Iterable<ImportedInsight>
  /** Tags narrowing what it applies to. */
  tags?: Iterable<Tag>
}

/**
 * What became of one memory in an import.
 *
 * An import mints no operation id, so `index` is the only thing identifying the
 * entry this is about, and nothing undoes an import once it has landed.
 *
 * `errors` is empty for an entry that was queued, so
 * {@link ImportOutcome.status} is what says whether there is anything to read.
 *
 * @see {@link ImportStatus} — `REJECTED` and `ERROR` do not share a remedy.
 */
export interface ImportOutcome {
  /**
   * Its position in the batch **as the caller sent it**.
   *
   * The SDK splits a long batch across several calls and renumbers each
   * response, so this always indexes the array that was passed in.
   */
  readonly index: number
  /** Whether it was queued, refused, duplicated or failed. */
  readonly status: ImportStatus
  /** Why it was refused, when it was. */
  readonly errors: readonly string[]
}

/**
 * What {@link MemoryOperations.importMemories} returns.
 *
 * Each memory is judged on its own, so a refused entry does not stop the others
 * and there is no whole-batch verdict to read: walk `results` and act per
 * entry.
 *
 * {@link ImportOutcome.index} indexes the array the caller passed, whatever the
 * service's per-call batch limit happens to be. A long batch goes out as
 * several calls, each numbering its own results from zero, and the SDK
 * renumbers them back; without that a split batch would report position 0 once
 * per call and identify nothing.
 *
 * `instructions` comes from the first of those calls — they are one operation
 * in one domain, so their guidance is the same.
 *
 * @example
 * ```ts
 * const result = await client.memory.importMemories(batch, {
 *   domain: 'coding'
 * })
 * for (const outcome of result.results) {
 *   if (outcome.status === ImportStatus.REJECTED) {
 *     console.log(outcome.index, [...batch[outcome.index].queries][0])
 *     for (const problem of outcome.errors) {
 *       console.log(`  ${problem}`)
 *     }
 *   }
 * }
 * ```
 */
export interface ImportResult {
  /** One outcome per memory sent, in the order they were sent. */
  readonly results: readonly ImportOutcome[]
  /** What the service asks the caller to do next. */
  readonly instructions: Instructions
}

/**
 * One contract file the generated client was built from.
 *
 * The checksum was taken when the client was generated, so it identifies the
 * file as it was compiled rather than as it stands in any checkout now. Nothing
 * verifies it at run time; it is here so a bug report can say precisely which
 * contract this package speaks.
 */
export interface ProtoRecord {
  /** Its path within the contract, such as `memco/memory/v1/memory.proto`. */
  readonly path: string
  /** The SHA-256 of its contents when the client was generated. */
  readonly sha256: string
}

/**
 * Where the generated client came from.
 *
 * Read it with {@link provenance}, which parses a descriptor shipped inside the
 * package — so this needs no connection and no credential, and describes the
 * build rather than the service. The commit the service reports for *itself* is
 * {@link DomainList.serverCommit}, and the two are not expected to match: this
 * one names the export the client was cut from.
 */
export interface Provenance {
  /** The server commit the export was cut from. */
  readonly serverCommit: string
  /** The contract files it was generated from, and their checksums. */
  readonly protos: readonly ProtoRecord[]
}
