/**
 * The operations reached as `client.memory`, and the session-scoped view of
 * them.
 *
 * Two shapes are worth naming, and both are forced by the language rather than
 * chosen:
 *
 * - Optional arguments are gathered into a single options object, so at most
 *   the first argument of an operation is ever positional.
 * - Node has no synchronous gRPC, so opening a session is deferred:
 *   {@link SessionOpener} is awaitable, and opens at most one session however
 *   often it is reached. It is deliberately not async-disposable — the scope it
 *   yields is, which is what makes `await using` need the inner `await`.
 */

import type {
  CallOptions,
  ClientUnaryCall,
  Metadata,
  ServiceError
} from '@grpc/grpc-js'

import { toolset, type Toolset } from './agent.js'
import * as pb from './internal/gen.js'
import { Known } from './internal/limits.js'
import * as convert from './internal/convert.js'
import * as deprecation from './internal/deprecation.js'
import * as requests from './internal/requests.js'
import {
  DataSource,
  type DomainList,
  type FeedbackRating,
  type FeedbackResult,
  type ImportResult,
  type ImportedMemory,
  type Instructions,
  type Memory,
  type RevertResult,
  type SearchResult,
  type Session,
  type Tag,
  type WriteResult
} from './types.js'

/**
 * One unary method on the generated client, in the form the SDK calls it.
 *
 * Only the four-argument overload is modelled. The SDK always has a deadline to
 * pass, so the shorter ones would be dead surface, and metadata comes before
 * options in the generated signature — the credential is attached by a
 * channel-level interceptor, so what the SDK passes here is always empty.
 */
export type Invoke<Request, Response> = (
  request: Request,
  metadata: Metadata,
  options: Partial<CallOptions>,
  callback: (error: ServiceError | null, response: Response) => void
) => ClientUnaryCall

/**
 * How an operation reaches the wire.
 *
 * The client supplies this, so the operations know nothing about deadlines,
 * error translation or connection state.
 *
 * `rpc` is passed rather than derived: ts-proto messages are plain objects with
 * no constructor to ask for a name, so the name a log record shows has to be
 * handed over.
 */
export type Caller = <Request, Response>(
  invoke: Invoke<Request, Response>,
  request: Request,
  timeout: number | undefined,
  rpc: string
) => Promise<Response>

/**
 * A deadline for one call, in seconds.
 *
 * Seconds, not milliseconds. That is the unit a caller thinks in, and the
 * conversion to the epoch deadline grpc-js wants happens where the call is
 * built. Passing one replaces the client's own default for that call and
 * changes nothing else; the default is {@link DEFAULT_TIMEOUT} seconds unless
 * the client was constructed with another.
 *
 * When the deadline elapses the call fails with a {@link MemcoTimeoutError},
 * and nothing is retried in its place: the deadline is the caller's own, so
 * another attempt past it could not help.
 *
 * A value that is not a positive, finite number is refused with a
 * {@link MemcoConfigError} before anything is sent, because neither end of the
 * range is usefully silent: zero and below expire before the call leaves, and
 * an infinite one sends no deadline at all, so the call never settles.
 */
export interface TimeoutOptions {
  /** Seconds to wait for this call. Defaults to the client's own timeout. */
  timeout?: number
}

/**
 * What {@link MemoryOperations.search} takes beyond the query itself.
 *
 * Name a domain or a session — never neither. A search that names neither is
 * refused before anything is sent, because the request has no other way to say
 * which domain to look in. Naming a session runs the search in that session's
 * domain and records it beside the other searches made under it; naming a
 * domain alone opens a session for this one search and returns its id on the
 * result. Naming both is allowed, and the service takes the domain from the
 * session.
 *
 * A tag either narrows the results or boosts them, and which one it does is a
 * property of its type rather than of the search. {@link DomainEntry} names the
 * narrowing ones in `filterTagTypes`, and the distinction matters: a wrong
 * boosting tag only costs ranking, while a wrong filtering tag returns nothing
 * at all.
 * {@link MemoryOperations.listDomains} says which tag types each domain uses
 * and how many it accepts. More than that many are trimmed to the cap rather
 * than refused: the service trims too, so raising here would reject a search it
 * would have run. Naming a session and no domain skips the local trim entirely
 * — the client cannot see which domain the session belongs to — and the service
 * applies the cap instead. Naming both still trims against the domain's cap.
 */
export interface SearchOptions extends TimeoutOptions {
  /** The domain to search. Pass this or {@link SearchOptions.sessionId}. */
  domain?: string
  /** The session to record the search under, which supplies the domain. */
  sessionId?: string
  /**
   * Tags to narrow or boost by, depending on each tag's type. Trimmed to the
   * domain's cap rather than refused.
   */
  tags?: Iterable<Tag>
}

/**
 * What {@link MemoryOperations.createMemory} writes a new memory from.
 *
 * Name a domain or a session — never neither. A write that names neither is
 * refused before anything is sent, because the request has no other way to say
 * where to save. Naming the session you have been searching in saves into that
 * session's domain and records the memory as part of that work; naming a domain
 * alone saves a standalone memory. Naming both is allowed, and the service
 * takes the domain from the session.
 *
 * {@link CreateMemoryOptions.title} and {@link CreateMemoryOptions.content} are
 * bounded **together**, not one each, so a long title spends the content's
 * characters. A finding that does not fit is better split across two memories
 * than trimmed. {@link CreateMemoryOptions.query} is bounded on its own. Both
 * caps are the service's, learned when the connection was verified, so a client
 * that has not yet listed domains checks only that the fields are non-blank.
 *
 * {@link CreateMemoryOptions.source} records who produced the content and
 * defaults to {@link DataSource.AGENT}. Claiming {@link DataSource.USER} says a
 * person said this, or corrected what the agent had — a statement about
 * provenance rather than a hint, which is why the toolset binds this field
 * instead of offering it to a model.
 *
 * @example
 * ```ts
 * const written = await client.memory.createMemory({
 *   domain: 'coding',
 *   query: 'how do we verify a gRPC connection before using it',
 *   title: 'The client health-probes before its first real call',
 *   content: 'connect() probes grpc.health.v1 and then lists domains.',
 *   tags: [{ type: 'language', value: 'typescript' }]
 * })
 * console.log(written.operationId)
 * ```
 */
export interface CreateMemoryOptions extends TimeoutOptions {
  /** What someone would search to find this memory. */
  query: string
  /** A short title describing what it is about. */
  title: string
  /** The knowledge to save. */
  content: string
  /** The domain to save into. Pass this or {@link CreateMemoryOptions.sessionId}. */
  domain?: string
  /** The session this was learned during, which supplies the domain. */
  sessionId?: string
  /** Tags narrowing what it applies to. */
  tags?: Iterable<Tag>
  /** Whether a human or the agent produced it. Defaults to {@link DataSource.AGENT}. */
  source?: DataSource
}

/**
 * What {@link MemoryOperations.enrichMemory} writes an addition from.
 *
 * There is no domain to name here, and that is the difference from
 * {@link CreateMemoryOptions}. An addition lands in the domain its session ran
 * in, which is why {@link EnrichMemoryOptions.sessionId} is required rather
 * than one half of a pair — and why the tag cap is left to the service, since
 * the client cannot see which domain that is.
 *
 * {@link EnrichMemoryOptions.memoryIdx} is the memory to extend, copied from a
 * search result, or the {@link NEW_MEMORY} sentinel to open a standalone memory
 * instead. The sentinel is case-sensitive: `'New'` is read as an ordinary
 * handle. It also changes what a later revert can remove: a revert reports
 * {@link RevertOutcome.MEMORY_REMOVED} when the insight it undid was its
 * memory's last, which is reachable for a memory the sentinel opened and not
 * for one that already held others. Read the outcome rather than assuming
 * either.
 *
 * {@link EnrichMemoryOptions.title} and {@link EnrichMemoryOptions.content} are
 * bounded **together** exactly as they are on a create.
 * {@link EnrichMemoryOptions.sources} are trimmed to the service's cap rather
 * than refused, so citing more than it keeps loses the tail — nothing in the
 * result says so, and a debug log record is the only sign of it.
 *
 * @example
 * ```ts
 * const found = await client.memory.search('gRPC health checking', {
 *   domain: 'coding'
 * })
 * await client.memory.enrichMemory({
 *   memoryIdx: found.memories[0].idx,
 *   sessionId: found.sessionId,
 *   title: 'The probe sends an empty service name',
 *   content: 'An empty name asks after the server as a whole.',
 *   sources: found.memories.map(memory => memory.idx)
 * })
 * ```
 */
export interface EnrichMemoryOptions extends TimeoutOptions {
  /** The memory to extend, or `'new'` to open a standalone one. */
  memoryIdx: string
  /** The session the memory was returned in. */
  sessionId: string
  /** A short title describing what was learned. */
  title: string
  /** The knowledge to add. */
  content: string
  /** Tags narrowing what it applies to. */
  tags?: Iterable<Tag>
  /** Memories that helped reach this insight. Trimmed to the service's cap. */
  sources?: Iterable<string>
  /** Whether a human or the agent produced it. Defaults to {@link DataSource.AGENT}. */
  source?: DataSource
}

/**
 * What {@link MemoryOperations.shareFeedback} records a batch of ratings from.
 *
 * Ratings belong to the search that produced the results, so there is no domain
 * to name and {@link ShareFeedbackOptions.sessionId} is required. One call
 * rates one search, while its session id is still to hand.
 *
 * At least one rating is required, and each must name the idx it is about — an
 * empty batch is refused before anything is sent. A batch larger than the
 * service accepts is refused too, rather than trimmed: every rating is a
 * judgement about a specific result, so dropping the tail would silently record
 * a verdict other than the one that was passed.
 */
export interface ShareFeedbackOptions extends TimeoutOptions {
  /** The session whose results are being rated. */
  sessionId: string
  /** The ratings. At least one. */
  feedback: Iterable<FeedbackRating>
}

/**
 * What {@link MemoryOperations.importMemories} takes beyond the memories.
 *
 * Name a domain or a session — never neither. An import that names neither is
 * refused before anything is sent. Naming a session records the batch as part
 * of that work and supplies the domain; naming a domain alone imports into it
 * directly. Naming both is allowed, and the service takes the domain from the
 * session.
 *
 * There is deliberately nothing here for a batch size. The service's cap bounds
 * one *call*, not one import, so the SDK divides a longer batch into calls of
 * that size and sends them in order — the caller hands over whatever it has and
 * never chunks against a number the SDK would otherwise have to publish. The
 * consequence to know is that {@link TimeoutOptions.timeout} bounds each of
 * those calls rather than the import as a whole.
 */
export interface ImportMemoriesOptions extends TimeoutOptions {
  /** The domain to import into. Pass this or {@link ImportMemoriesOptions.sessionId}. */
  domain?: string
  /** The session to record the batch against, which supplies the domain. */
  sessionId?: string
}

/**
 * What {@link SessionScope.search} takes.
 *
 * {@link SearchOptions} with the domain and the session removed: the scope
 * supplies both, so the pair that would otherwise have to be got right cannot
 * be got wrong here. What is left behaves as it does on an unscoped search,
 * except that the tags go out untrimmed — a search naming a session leaves the
 * cap to the service, because the client cannot see which domain the session
 * belongs to.
 */
export type ScopedSearchOptions = TimeoutOptions & Pick<SearchOptions, 'tags'>

/**
 * What {@link SessionScope.createMemory} writes a new memory from.
 *
 * {@link CreateMemoryOptions} with the domain and the session removed, so the
 * memory always lands in the session's domain and is always recorded as part of
 * that work. Everything else holds: title and content are bounded together, and
 * the source still defaults to {@link DataSource.AGENT}.
 */
export type ScopedCreateMemoryOptions = Omit<
  CreateMemoryOptions,
  'domain' | 'sessionId'
>

/**
 * What {@link SessionScope.enrichMemory} writes an addition from.
 *
 * {@link EnrichMemoryOptions} with the session removed, which the scope
 * supplies. There was never a domain on it to remove — an addition takes the
 * domain from its session either way — so the memory idx, still including the
 * {@link NEW_MEMORY} sentinel, is what remains to choose.
 */
export type ScopedEnrichMemoryOptions = Omit<EnrichMemoryOptions, 'sessionId'>

/**
 * What {@link SessionScope.shareFeedback} records a batch of ratings from.
 *
 * {@link ShareFeedbackOptions} with the session removed, which the scope
 * supplies — so the ratings cannot be filed against a session other than the
 * one that returned the results they judge. The batch is still bounded, and
 * still needs at least one rating.
 */
export type ScopedShareFeedbackOptions = Omit<ShareFeedbackOptions, 'sessionId'>

/**
 * The memory operations, reached as `client.memory`.
 *
 * The prose here is hand-written and addresses a developer. The service's own
 * copy — what a model is told each of these operations does — is generated into
 * `src/gen/toolCopy.ts` instead, because TypeScript keeps no doc comments at
 * run time for `agent.ts` to read back off these methods.
 */
export class MemoryOperations {
  /**
   * What the service has reported about its own limits.
   *
   * Empty until {@link MemoryOperations.listDomains} has answered once, which
   * is why the client calls it while verifying the connection. Every request
   * built before then is checked against structural rules only.
   */
  private readonly known = new Known()

  /**
   * @param stub The generated client to call.
   * @param call How to reach the wire, supplied by the client that owns the
   *   connection.
   *
   * @internal Constructed by {@link Memco}, never by a caller. It is reached as
   *   `client.memory`.
   */
  constructor(
    private readonly stub: pb.MemoryServiceClient,
    private readonly call: Caller
  ) {}

  /**
   * List the memory domains this credential may name.
   *
   * This is also how the client learns the limits the service enforces, so it
   * runs once while the connection is being verified. Calling it again
   * refreshes them.
   *
   * @param options Per-call deadline.
   * @returns The domains, the service's guidance, and the limits it enforces.
   * @throws MemcoAuthenticationError If the credential is rejected.
   *
   * @example
   * ```ts
   * const { domains } = await client.memory.listDomains()
   * console.log(domains.map(domain => domain.slug))
   * ```
   */
  async listDomains(options: TimeoutOptions = {}): Promise<DomainList> {
    const response = await this.call(
      this.stub.listDomains.bind(this.stub),
      requests.listDomainsRequest(),
      options.timeout,
      'ListDomains'
    )
    const described = convert.toDomainList(response)
    this.known.update(described.limits, described.domains)
    // Driven by the message being non-empty rather than by the `deprecated`
    // flag: the flag does not say which of the SDK build or the API version it
    // means, and only the message carries the remedy.
    deprecation.warnOnce(described.deprecationMessage, described.sunsetDate)
    return described
  }

  /**
   * Open a session, so the searches made under it are recorded as one series.
   *
   * @param domain The domain to work in.
   * @param options Per-call deadline.
   * @returns The session handle, and the service's guidance for working in it.
   * @throws MemcoInvalidRequestError If the domain is blank.
   */
  async startSession(
    domain: string,
    options: TimeoutOptions = {}
  ): Promise<Session> {
    const response = await this.call(
      this.stub.startSession.bind(this.stub),
      requests.startSessionRequest(domain),
      options.timeout,
      'StartSession'
    )
    return convert.toSession(response)
  }

  /**
   * Open a session and bind it, so no later call can drop the handle.
   *
   * Nothing is sent until the result is awaited, and awaiting it twice opens
   * one session and returns the same scope — which is what makes it safe to
   * hold in a variable.
   *
   * @param domain The domain to work in.
   * @param options Per-call deadline.
   * @returns An opener. Await it for the scope, which is the disposable one.
   *
   * @example
   * ```ts
   * await using session = await client.memory.withSession('coding')
   * const result = await session.search('how does health checking work')
   * ```
   */
  withSession(domain: string, options: TimeoutOptions = {}): SessionOpener {
    return new SessionOpener(this, domain, options)
  }

  /**
   * Search for existing knowledge.
   *
   * Pass a domain or a session id — a search naming neither is refused before
   * anything is sent, because the request has no way to name a domain.
   *
   * @param query What you want to know, in plain language.
   * @param options The scope to search in, tags to narrow by, and a deadline.
   * @returns What matched, and the session it was recorded against.
   * @throws MemcoInvalidRequestError If the query is blank, if neither a domain
   *   nor a session is named, or if the query exceeds the service's limit.
   *
   * @example
   * ```ts
   * const result = await client.memory.search('gRPC health checking', {
   *   domain: 'coding',
   *   tags: [{ type: 'language', value: 'typescript' }]
   * })
   * ```
   */
  async search(
    query: string,
    options: SearchOptions = {}
  ): Promise<SearchResult> {
    const response = await this.call(
      this.stub.search.bind(this.stub),
      requests.searchRequest(query, {
        domain: options.domain ?? null,
        sessionId: options.sessionId ?? null,
        tags: options.tags,
        known: this.known
      }),
      options.timeout,
      'Search'
    )
    return convert.toSearchResult(response)
  }

  /**
   * Fetch one memory in full, by an idx a search returned.
   *
   * @param idx The handle, copied exactly as it appeared in a search response.
   * @param options Per-call deadline.
   * @returns The memory.
   * @throws MemcoNotFoundError If nothing answers to that handle.
   *
   * @example
   * ```ts
   * const memory = await client.memory.getMemory('memory-6oiv6b-1')
   * ```
   */
  async getMemory(idx: string, options: TimeoutOptions = {}): Promise<Memory> {
    const response = await this.call(
      this.stub.getMemory.bind(this.stub),
      requests.getMemoryRequest(idx, this.known),
      options.timeout,
      'GetMemory'
    )
    return convert.toMemory(requests.requireMemory(response, idx))
  }

  /**
   * Save new knowledge.
   *
   * The write is accepted asynchronously, so the result carries the operation
   * id addressing it rather than the memory it will become.
   *
   * @param options What to save, the scope to save it in, and a deadline.
   * @returns The operation id, when the write can be undone, and guidance.
   * @throws MemcoInvalidRequestError If a required field is blank, if neither a
   *   domain nor a session is named, or if the title and content together
   *   exceed the service's combined limit.
   */
  async createMemory(options: CreateMemoryOptions): Promise<WriteResult> {
    const response = await this.call(
      this.stub.createMemory.bind(this.stub),
      requests.createMemoryRequest({
        query: options.query,
        title: options.title,
        content: options.content,
        domain: options.domain ?? null,
        sessionId: options.sessionId ?? null,
        tags: options.tags,
        source: options.source ?? DataSource.AGENT,
        known: this.known
      }),
      options.timeout,
      'CreateMemory'
    )
    return convert.toWriteResult(response)
  }

  /**
   * Add to a memory a search returned, or open a new one.
   *
   * @param options What to add, which memory to add it to, and a deadline. Pass
   *   `'new'` as the memory idx to open a standalone memory instead.
   * @returns The operation id, when the write can be undone, and guidance.
   * @throws MemcoInvalidRequestError If a required field is blank or the title
   *   and content together exceed the service's combined limit.
   */
  async enrichMemory(options: EnrichMemoryOptions): Promise<WriteResult> {
    const response = await this.call(
      this.stub.enrichMemory.bind(this.stub),
      requests.enrichMemoryRequest({
        memoryIdx: options.memoryIdx,
        sessionId: options.sessionId,
        title: options.title,
        content: options.content,
        tags: options.tags,
        sources: options.sources,
        source: options.source ?? DataSource.AGENT,
        known: this.known
      }),
      options.timeout,
      'EnrichMemory'
    )
    return convert.toWriteResult(response)
  }

  /**
   * Rate the results of one search.
   *
   * Ratings are what move the reliability signal on an insight, so this is the
   * half of a search that makes the next one better.
   *
   * @param options The session, the ratings, and a deadline.
   * @returns What was recorded, and anything the service wants to say back.
   * @throws MemcoInvalidRequestError If no ratings are given, if a rating names
   *   no idx, or if there are more than the service accepts.
   */
  async shareFeedback(options: ShareFeedbackOptions): Promise<FeedbackResult> {
    const response = await this.call(
      this.stub.shareFeedback.bind(this.stub),
      requests.shareFeedbackRequest({
        sessionId: options.sessionId,
        feedback: options.feedback,
        known: this.known
      }),
      options.timeout,
      'ShareFeedback'
    )
    return convert.toFeedbackResult(response)
  }

  /**
   * Undo one of your own writes.
   *
   * Every outcome is a successful call: `NOT_FOUND`, `EXPIRED` and `REFUSED`
   * report a caller-visible state rather than a service failure, so read
   * {@link RevertResult.outcome} rather than relying on this to throw.
   *
   * @param operationId The handle a write returned.
   * @param options Per-call deadline.
   * @returns What the revert actually removed.
   *
   * @example
   * ```ts
   * const reverted = await client.memory.revertMemory(created.operationId!)
   * if (reverted.outcome === RevertOutcome.EXPIRED) {
   *   console.log('outside the revert window')
   * }
   * ```
   */
  async revertMemory(
    operationId: string,
    options: TimeoutOptions = {}
  ): Promise<RevertResult> {
    const response = await this.call(
      this.stub.revertMemory.bind(this.stub),
      requests.revertMemoryRequest(operationId, this.known),
      options.timeout,
      'RevertMemory'
    )
    return convert.toRevertResult(response)
  }

  /**
   * Contribute many memories at once.
   *
   * A batch longer than the service accepts is split across several calls by
   * the SDK, and every outcome is renumbered back into the array that was
   * passed in — so {@link ImportOutcome.index} always indexes `memories`,
   * whatever the service's batch limit happens to be.
   *
   * Each memory is judged on its own, so a refused entry does not stop the
   * others. A batch mints no operation id, so an import cannot be reverted.
   *
   * @param memories The memories to import. At least one, each with at least
   *   one query and one insight.
   * @param options The scope to import into, and a deadline.
   * @returns One outcome per memory, in the order they were given.
   * @throws MemcoInvalidRequestError If the batch is empty, if an entry is
   *   incomplete, or if neither a domain nor a session is named.
   */
  async importMemories(
    memories: Iterable<ImportedMemory>,
    options: ImportMemoriesOptions = {}
  ): Promise<ImportResult> {
    const planned = requests.importMemoriesRequests(memories, {
      domain: options.domain ?? null,
      sessionId: options.sessionId ?? null,
      known: this.known
    })
    const answered: [number, pb.ImportMemoriesResponse][] = []
    // Sent in order rather than concurrently: the service records a batch
    // against the session, and interleaving the parts of one logical import
    // would report them as unrelated writes.
    for (const [offset, request] of planned) {
      answered.push([
        offset,
        await this.call(
          this.stub.importMemories.bind(this.stub),
          request,
          options.timeout,
          'ImportMemories'
        )
      ])
    }
    return convert.toImportResult(answered)
  }
}

/**
 * The operations, bound to one session.
 *
 * No method here takes a session id or a domain: the scope supplies both. That
 * is the point of it — a call that silently drops the session id is still a
 * valid call. It opens a session of its own and records the search there, so
 * the series you were assembling quietly splits in two, and a later rating
 * lands against a search you did not mean to make.
 */
export class SessionScope {
  /**
   * @param operations The namespace to delegate to.
   * @param session The session this scope is bound to.
   */
  constructor(
    private readonly operations: MemoryOperations,
    private readonly session: Session
  ) {}

  /** The session handle, for a call that needs to name it directly. */
  get sessionId(): string {
    return this.session.sessionId
  }

  /** What the service asks the caller to do within this session. */
  get instructions(): Instructions {
    return this.session.instructions
  }

  /**
   * Leave the scope.
   *
   * Deliberately a no-op. The contract has no operation that ends a session,
   * so there is nothing to release; this exists so `await using` can bound the
   * region of code a session belongs to, which is a claim about the reader's
   * attention rather than about a resource.
   */
  async [Symbol.asyncDispose](): Promise<void> {
    // Nothing to release. See the comment above.
  }

  /**
   * Search within this session.
   *
   * @param query What you want to know, in plain language.
   * @param options Tags to narrow by, and a deadline.
   * @returns What matched.
   */
  search(
    query: string,
    options: ScopedSearchOptions = {}
  ): Promise<SearchResult> {
    return this.operations.search(query, {
      ...options,
      sessionId: this.sessionId
    })
  }

  /**
   * Fetch one memory in full, by an idx this session returned.
   *
   * @param idx The handle, copied exactly as it appeared in a search response.
   * @param options Per-call deadline.
   * @returns The memory.
   */
  getMemory(idx: string, options: TimeoutOptions = {}): Promise<Memory> {
    return this.operations.getMemory(idx, options)
  }

  /**
   * Save new knowledge into this session's domain.
   *
   * @param options What to save, and a deadline.
   * @returns The operation id, when the write can be undone, and guidance.
   */
  createMemory(options: ScopedCreateMemoryOptions): Promise<WriteResult> {
    return this.operations.createMemory({
      ...options,
      sessionId: this.sessionId
    })
  }

  /**
   * Add to a memory this session returned, or open a new one.
   *
   * @param options What to add, which memory to add it to, and a deadline.
   * @returns The operation id, when the write can be undone, and guidance.
   */
  enrichMemory(options: ScopedEnrichMemoryOptions): Promise<WriteResult> {
    return this.operations.enrichMemory({
      ...options,
      sessionId: this.sessionId
    })
  }

  /**
   * Rate what this session returned.
   *
   * @param options The ratings, and a deadline.
   * @returns What was recorded, and anything the service wants to say back.
   */
  shareFeedback(options: ScopedShareFeedbackOptions): Promise<FeedbackResult> {
    return this.operations.shareFeedback({
      ...options,
      sessionId: this.sessionId
    })
  }

  /**
   * Undo one of your own writes.
   *
   * @param operationId The handle a write returned.
   * @param options Per-call deadline.
   * @returns What the revert actually removed.
   */
  revertMemory(
    operationId: string,
    options: TimeoutOptions = {}
  ): Promise<RevertResult> {
    return this.operations.revertMemory(operationId, options)
  }

  /**
   * Contribute many memories at once, recorded against this session.
   *
   * @param memories The memories to import.
   * @param options Per-call deadline.
   * @returns One outcome per memory, in the order they were given.
   */
  importMemories(
    memories: Iterable<ImportedMemory>,
    options: TimeoutOptions = {}
  ): Promise<ImportResult> {
    return this.operations.importMemories(memories, {
      ...options,
      sessionId: this.sessionId
    })
  }

  /**
   * This session's operations, described and rendered for an LLM.
   *
   * Each tool carries what the operation is for, a JSON Schema for its
   * arguments, and a call that renders the result as text. Every one is bound
   * to this session, so nothing a model sends can change which session a call
   * is recorded under.
   *
   * The toolset hands itself to a framework — `toLangChain()`,
   * `toAnthropic()`, `toOpenAI()` — or runs what a model named with `call()`.
   *
   * @returns One tool per offered operation, as a {@link Toolset}.
   *
   * @example
   * ```ts
   * await using session = await client.memory.withSession('coding')
   * createAgent({ model, tools: await session.tools().toLangChain() })
   * ```
   */
  tools(): Toolset {
    return toolset(this)
  }
}

/**
 * A session that has not been opened yet.
 *
 * Awaiting it opens one and yields the {@link SessionScope}; awaiting it again
 * yields the same scope rather than opening a second session. Nothing is sent
 * until it is awaited, so an opener that is created and dropped costs no call
 * and produces no unhandled rejection.
 *
 * It deliberately carries no `Symbol.asyncDispose` of its own. `await using`
 * binds the expression rather than anything awaited out of it, so
 * `await using s = client.memory.withSession(d)` would bind this object and not
 * the scope — leaving that off makes it a type error rather than a puzzle at
 * run time. Write `await using s = await client.memory.withSession(d)`.
 */
export class SessionOpener implements PromiseLike<SessionScope> {
  private opened: Promise<SessionScope> | undefined

  /**
   * @param operations The namespace to open the session on.
   * @param domain The domain to open it in.
   * @param options Per-call deadline.
   */
  constructor(
    private readonly operations: MemoryOperations,
    private readonly domain: string,
    private readonly options: TimeoutOptions
  ) {}

  /**
   * Open the session, at most once, and hand the scope on.
   *
   * @param onfulfilled Called with the bound scope.
   * @param onrejected Called if the session could not be opened.
   * @returns The chained promise.
   */
  then<Fulfilled = SessionScope, Rejected = never>(
    onfulfilled?:
      ((value: SessionScope) => Fulfilled | PromiseLike<Fulfilled>) | null,
    onrejected?: ((reason: unknown) => Rejected | PromiseLike<Rejected>) | null
  ): PromiseLike<Fulfilled | Rejected> {
    this.opened ??= this.open()
    return this.opened.then(onfulfilled, onrejected)
  }

  /**
   * Open the session, forgetting the attempt if it fails.
   *
   * A rejection must not be cached. `StartSession` is deliberately outside the
   * retry policy — a replayed one mints a second session id — so nothing else
   * absorbs a transient failure, and holding the rejected promise would poison
   * this opener for the life of the object.
   */
  private open(): Promise<SessionScope> {
    const opening = this.operations
      .startSession(this.domain, this.options)
      .then(session => new SessionScope(this.operations, session))
    opening.catch(() => {
      this.opened = undefined
    })
    return opening
  }
}
