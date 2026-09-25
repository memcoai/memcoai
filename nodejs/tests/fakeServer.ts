/**
 * A real in-process gRPC server for the suite to run against.
 *
 * Nothing about gRPC is mocked. The suite dials a loopback socket and speaks
 * the real protocol, so metadata, status codes, retry policy and channel
 * teardown are all exercised for real, because the interesting defects in an
 * SDK like this one live in exactly the layers a mock replaces.
 *
 * It serves the memory service, the token exchange, the administration service
 * and the health probe, all on one socket, as the real endpoint does. It needs
 * no network access and no API key.
 */

import {
  Metadata,
  Server,
  ServerCredentials,
  type ServerUnaryCall,
  type ServiceError,
  type UntypedServiceImplementation,
  type sendUnaryData,
  status
} from '@grpc/grpc-js'

import * as pb from '../src/internal/gen.js'
import {
  HealthService,
  ServingStatus,
  STATUS_DETAILS_KEY,
  encodeStatusDetails,
  type HealthCheckRequest,
  type HealthCheckResponse
} from '../src/internal/wire.js'

/** A status the fake should abort with, instead of answering. */
export interface Failure {
  /** The gRPC status code to report. */
  code: status
  /** The details string to report with it. */
  details: string
}

/** A `google.rpc.ErrorInfo` to attach to an aborted call. */
export interface RichFailure extends Failure {
  /** The service's own reason code, such as `CLIENT_VERSION_SUNSET`. */
  reason: string
  /** The domain that defines it. Memco's is `memco.ai`. */
  domain: string
  /** The detail attached beside the reason, such as the network a user is in. */
  metadata?: Record<string, string>
}

/**
 * A refusal a default answer throws, to be served as the call's status.
 *
 * What lets a default answer depend on state, such as the per-user cap on live
 * impersonation keys, rather than only a canned failure.
 */
export class Refusal extends Error {
  constructor(readonly failure: Failure) {
    super(failure.details)
  }
}

/** One call any service received, in the order they all arrived. */
export interface Arrival {
  /** Which service it reached. */
  readonly service: 'memory' | 'tokens' | 'admin'
  /** The method, in its grpc-js spelling. */
  readonly method: string
  /** The request message. */
  readonly request: unknown
  /** Every `authorization` value it carried. */
  readonly authorization: readonly string[]
}

/**
 * A canned answer computed per call, from the request and its metadata.
 *
 * Staged in `responses` like a canned message; a function there is called
 * rather than returned.
 */
export type Answer = (request: never, metadata: Metadata) => unknown

/** The ten methods the memory service publishes, in their grpc-js spelling. */
export type MemoryMethod =
  | 'listDomains'
  | 'startSession'
  | 'search'
  | 'getMemory'
  | 'createMemory'
  | 'enrichMemory'
  | 'shareFeedback'
  | 'revertMemory'
  | 'importMemories'
  | 'listTools'

/** The one method the token service publishes. */
export type TokenMethod = 'issueToken'

/** The twenty-one methods the administration service publishes. */
export type AdminMethod = keyof typeof pb.admin.AdminServiceService

/**
 * Every method the contract declares, mirroring the real `ListTools` catalog.
 *
 * Used as the default `listTools` response — every method available — so the
 * tests that do not care about access-set filtering keep seeing the full
 * toolset without configuring one.
 */
const ALL_TOOLS = [
  'list_domains',
  'start_session',
  'search',
  'get_memory',
  'create_memory',
  'enrich_memory',
  'share_feedback',
  'revert_memory',
  'import_memories',
  'list_tools'
] as const

/**
 * Keeps every call to one method in flight until the test lets it go.
 *
 * A credential stays leased for as long as a call using it is in flight, so
 * holding one is how a test puts a renewal, or a close, in the middle of a call
 * still carrying the old credential.
 */
export class Hold {
  /** Settles once a held call has reached the server, credential and all. */
  readonly arrived: Promise<void>

  /** Let every held call, and any that arrives later, answer. */
  readonly release: () => void

  private readonly released: Promise<void>
  private readonly arrive: () => void

  constructor() {
    let arrive = (): void => {}
    let release = (): void => {}
    this.arrived = new Promise<void>(settle => {
      arrive = settle
    })
    this.released = new Promise<void>(settle => {
      release = settle
    })
    this.arrive = arrive
    this.release = release
  }

  /** Mark a call as arrived and wait until the test releases it. */
  async wait(): Promise<void> {
    this.arrive()
    await this.released
  }
}

/**
 * Records what one service was called with, and answers what it is told to.
 *
 * The per-call lists are index-aligned — a call's method, request, metadata
 * and credentials sit at the same position in each — which is what lets the
 * crosstalk tests pair every request with the credential it was sent with.
 */
class Recorder<M extends string> {
  /**
   * @param service The service's name in the shared journal.
   * @param journal Every call every service received, in one arrival order.
   */
  constructor(
    private readonly service: Arrival['service'],
    private readonly journal: Arrival[]
  ) {}

  /** Method names in call order, including calls that then aborted. */
  readonly calls: M[] = []

  /** Every request, in the same order as {@link calls}. */
  readonly received: unknown[] = []

  /** The metadata each call arrived with, in the same order as {@link calls}. */
  readonly metadata: Record<string, unknown>[] = []

  /**
   * Every `authorization` value each call arrived with.
   *
   * {@link metadata} keeps only the first value per key, and the number of
   * credentials on a call is exactly what a wire test has to see.
   */
  readonly authorization: string[][] = []

  /** The last request seen per method. */
  readonly requests = new Map<M, unknown>()

  /**
   * Canned responses, overriding the defaults. A function is an
   * {@link Answer}, called per call.
   */
  readonly responses = new Map<M, unknown>()

  /** When set, every method aborts with this instead of answering. */
  error: Failure | null = null

  /** When set, every method aborts with this and a status-details trailer. */
  richError: RichFailure | null = null

  /**
   * Failures to serve once each, per method, before answering normally.
   *
   * This is what stages "fails once, then succeeds" for the retry tests.
   */
  readonly transientErrors = new Map<M, Failure[]>()

  /**
   * Milliseconds to wait before answering, per method.
   *
   * Lets a deadline test be decided by the deadline instead of by a race
   * against an in-process server that would otherwise answer immediately.
   */
  readonly delays = new Map<M, number>()

  /** Holds keeping every call to a method in flight until released. */
  readonly holds = new Map<M, Hold>()

  /** Forget everything recorded so far, leaving the configured behaviour. */
  forget(): void {
    this.calls.length = 0
    this.received.length = 0
    this.metadata.length = 0
    this.authorization.length = 0
    this.requests.clear()
  }

  /**
   * Build the handler for one method.
   *
   * @param method The method's grpc-js name.
   * @param fallback What to answer when nothing is staged. Called only when the
   *   call is answered normally, so a numbered default counts answers given.
   * @returns The grpc-js handler.
   */
  protected on<Req, Res>(
    method: M,
    fallback: (request: Req) => Res
  ): (call: ServerUnaryCall<Req, Res>, callback: sendUnaryData<Res>) => void {
    return (call, callback) => {
      this.handle(method, call, callback, fallback)
    }
  }

  private handle<Req, Res>(
    method: M,
    call: ServerUnaryCall<Req, Res>,
    callback: sendUnaryData<Res>,
    fallback: (request: Req) => Res
  ): void {
    // Recorded before any abort, so a test can count the attempts a retry made.
    this.calls.push(method)
    this.received.push(call.request)
    this.metadata.push(call.metadata.getMap())
    this.authorization.push(call.metadata.get('authorization').map(String))
    this.requests.set(method, call.request)
    this.journal.push({
      service: this.service,
      method,
      request: call.request,
      authorization: call.metadata.get('authorization').map(String)
    })
    const staged = this.transientErrors.get(method)?.shift()

    const answer = (): void => {
      if (staged !== undefined) {
        callback(asServiceError(staged))
        return
      }
      if (this.richError !== null) {
        callback(asServiceError(this.richError, withDetails(this.richError)))
        return
      }
      if (this.error !== null) {
        callback(asServiceError(this.error))
        return
      }
      const canned = this.responses.get(method)
      let answered: Res
      try {
        answered =
          canned === undefined
            ? fallback(call.request)
            : typeof canned === 'function'
              ? ((canned as Answer)(
                  call.request as never,
                  call.metadata
                ) as Res)
              : (canned as Res)
      } catch (error) {
        if (!(error instanceof Refusal)) {
          throw error
        }
        callback(asServiceError(error.failure))
        return
      }
      callback(null, answered)
    }

    const hold = this.holds.get(method)
    if (hold !== undefined) {
      void hold.wait().then(answer)
      return
    }
    const delay = this.delays.get(method)
    if (delay === undefined) {
      answer()
      return
    }
    // Unreferenced: a delay outlasting the deadline that was being tested must
    // not hold the process open after the client has already given up.
    setTimeout(answer, delay).unref()
  }
}

/**
 * The memory service, recording what it was asked and answering what it is told
 * to.
 */
export class FakeMemoryService extends Recorder<MemoryMethod> {
  /** The grpc-js service implementation to register on a {@link Server}. */
  implementation(): UntypedServiceImplementation {
    return {
      listDomains: this.on('listDomains', () =>
        pb.ListDomainsResponse.fromPartial({})
      ),
      startSession: this.on('startSession', () =>
        pb.StartSessionResponse.fromPartial({ sessionId: 'session-a' })
      ),
      search: this.on('search', () =>
        pb.SearchResponse.fromPartial({ sessionId: 'session-a' })
      ),
      getMemory: this.on('getMemory', (request: pb.GetMemoryRequest) =>
        pb.GetMemoryResponse.fromPartial({ memory: { idx: request.idx } })
      ),
      createMemory: this.on('createMemory', () =>
        pb.CreateMemoryResponse.fromPartial({ operationId: 'create-a' })
      ),
      enrichMemory: this.on('enrichMemory', () =>
        pb.EnrichMemoryResponse.fromPartial({ operationId: 'enrich-a' })
      ),
      shareFeedback: this.on('shareFeedback', () =>
        pb.ShareFeedbackResponse.fromPartial({ sessionId: 'session-a' })
      ),
      revertMemory: this.on('revertMemory', (request: pb.RevertMemoryRequest) =>
        pb.RevertMemoryResponse.fromPartial({
          operationId: request.opId,
          outcome: pb.RevertOutcome.REVERT_OUTCOME_MERGED
        })
      ),
      importMemories: this.on(
        'importMemories',
        (request: pb.ImportMemoriesRequest) =>
          pb.ImportMemoriesResponse.fromPartial({
            results: request.memories.map((_, index) => ({
              index,
              status: pb.ImportStatus.IMPORT_STATUS_QUEUED
            }))
          })
      ),
      listTools: this.on('listTools', () =>
        pb.ListToolsResponse.fromPartial({
          tools: ALL_TOOLS.map(name => ({
            name,
            description: `${name} tool`,
            available: true
          }))
        })
      )
    } as UntypedServiceImplementation
  }
}

/**
 * Issues a numbered bearer token to whoever asks: `client-token-1`, then `-2`.
 *
 * The number counts tokens actually issued, so a refused request does not use
 * one up and the token a test expects next is always the next number.
 */
export class FakeTokenService extends Recorder<TokenMethod> {
  /** The lifetime in seconds each token is issued with; the real default. */
  expiresIn = 3600

  private issued = 0

  /** The grpc-js service implementation to register on a {@link Server}. */
  implementation(): UntypedServiceImplementation {
    return {
      issueToken: this.on('issueToken', () => {
        this.issued += 1
        return pb.auth.IssueTokenResponse.fromPartial({
          accessToken: `client-token-${this.issued}`,
          tokenType: 'Bearer',
          expiresIn: this.expiresIn
        })
      })
    } as UntypedServiceImplementation
  }
}

/** The network every network method answers with, unless told otherwise. */
export const NETWORK = pb.admin.Network.fromPartial({
  id: 'network-a',
  name: 'Acme',
  parentId: 'network-root',
  domain: 'coding',
  region: 'global',
  scope: 'customer',
  owner: 'acme',
  description: "Acme's support knowledge"
})

/** The member every member listing answers with. */
export const MEMBER = pb.admin.Member.fromPartial({
  userId: 'user-a',
  email: 'ada@example.com',
  name: 'Ada'
})

/** The group every group listing answers with. */
export const GROUP = pb.admin.Group.fromPartial({
  id: 'group-a',
  name: 'Support',
  memoryNetworkId: 'network-a',
  memberCount: 2
})

/** The external user every user method answers with. */
export const EXTERNAL_USER = pb.admin.ExternalUser.fromPartial({
  id: 'xuser-a',
  externalId: 'customer-42',
  name: 'Ada',
  email: 'ada@example.com',
  roles: ['reader'],
  active: true
})

/** The key every key listing answers with. 2026-01-01T00:00:00Z. */
export const KEY = pb.admin.ExternalUserKey.fromPartial({
  id: 'apikey-a',
  name: 'ci',
  valuePrefix: 'mk_live_ab',
  roles: ['reader'],
  scopes: ['mcp:read'],
  validUntil: 1767225600
})

/** The value every created key answers with: a working credential. */
export const KEY_VALUE = 'mk_live_ab-supersecret-key-value'

/**
 * The administration service: every method answers something sensible.
 *
 * Lists answer one canned entry, and a method acting on something echoes the
 * handles it was sent, so a result can be traced to its request.
 *
 * Impersonation keys are numbered in the order they are minted, shared across
 * users: the first is `impersonation-<externalId>-1` with key id `key-1`, the
 * next `...-2` and `key-2`. The value and the key id share the number, so a test
 * can tell from a bearer which key it is.
 */
export class FakeAdminService extends Recorder<AdminMethod> {
  /** Seconds from minting to each impersonation key's expiry; the real default. */
  keyLifetime = 3600

  /** How many keys may be live per user at once; the real service allows 20. */
  keyCap = 20

  /**
   * Whether a key carries `expiresIn`, the seconds it has left by the
   * service's own clock. Off, as a service predating the field sends it: 0.
   */
  sendExpiresIn = true

  /** Every key minted, by key id: the user it acts as, and its expiry in ms. */
  readonly minted = new Map<string, { externalId: string; expiresAt: number }>()

  /** The keys minted and neither ended nor expired: user, then key id, to expiry. */
  private readonly liveKeys = new Map<string, Map<string, number>>()

  /**
   * The wall clock, in milliseconds, a key's absolute `expiresAt` is counted
   * from. A test that moves the SDK's clock points this at the same one.
   */
  clock: () => number = Date.now

  private keys = 0

  /** The grpc-js service implementation to register on a {@link Server}. */
  implementation(): UntypedServiceImplementation {
    const a = pb.admin
    return {
      listNetworks: this.on('listNetworks', () =>
        a.ListNetworksResponse.fromPartial({
          networks: [NETWORK],
          totalCount: 1
        })
      ),
      createNetwork: this.on(
        'createNetwork',
        (request: pb.admin.CreateNetworkRequest) =>
          a.Network.fromPartial({
            id: 'network-new',
            name: request.name,
            parentId: request.parentId,
            domain: request.domain || 'coding',
            region: request.region || 'global',
            scope: request.scope,
            owner: request.owner,
            description: request.description
          })
      ),
      updateNetwork: this.on(
        'updateNetwork',
        (request: pb.admin.UpdateNetworkRequest) => {
          const changed = Object.fromEntries(
            Object.entries(request).filter(([, value]) => value !== undefined)
          )
          return a.Network.fromPartial({ ...NETWORK, ...changed })
        }
      ),
      deleteNetwork: this.on(
        'deleteNetwork',
        (request: pb.admin.DeleteNetworkRequest) =>
          a.DeleteNetworkResponse.fromPartial({
            id: request.id,
            removed: { network_members: 1, memories: 3 }
          })
      ),
      listNetworkMembers: this.on('listNetworkMembers', () =>
        a.ListNetworkMembersResponse.fromPartial({
          members: [MEMBER],
          totalCount: 1
        })
      ),
      addNetworkMember: this.on(
        'addNetworkMember',
        (request: pb.admin.AddNetworkMemberRequest) =>
          a.AddNetworkMemberResponse.fromPartial({
            id: request.id,
            userId: request.userId
          })
      ),
      removeNetworkMember: this.on(
        'removeNetworkMember',
        (request: pb.admin.RemoveNetworkMemberRequest) =>
          a.RemoveNetworkMemberResponse.fromPartial(request)
      ),
      listGroups: this.on('listGroups', () =>
        a.ListGroupsResponse.fromPartial({ groups: [GROUP], totalCount: 1 })
      ),
      listGroupMembers: this.on('listGroupMembers', () =>
        a.ListGroupMembersResponse.fromPartial({ members: [MEMBER] })
      ),
      addNetworkGroup: this.on(
        'addNetworkGroup',
        (request: pb.admin.AddNetworkGroupRequest) =>
          a.AddNetworkGroupResponse.fromPartial(request)
      ),
      removeNetworkGroup: this.on(
        'removeNetworkGroup',
        (request: pb.admin.RemoveNetworkGroupRequest) =>
          a.RemoveNetworkGroupResponse.fromPartial(request)
      ),
      listExternalUsers: this.on('listExternalUsers', () =>
        a.ListExternalUsersResponse.fromPartial({
          externalUsers: [EXTERNAL_USER],
          totalCount: 1
        })
      ),
      getExternalUser: this.on(
        'getExternalUser',
        (request: pb.admin.GetExternalUserRequest) =>
          a.ExternalUser.fromPartial({
            ...EXTERNAL_USER,
            externalId: request.externalId
          })
      ),
      createExternalUser: this.on(
        'createExternalUser',
        (request: pb.admin.CreateExternalUserRequest) =>
          a.ExternalUser.fromPartial({
            id: 'xuser-new',
            externalId: request.externalId,
            name: request.name,
            email: request.email,
            roles: request.roles,
            active: true
          })
      ),
      updateExternalUser: this.on(
        'updateExternalUser',
        (request: pb.admin.UpdateExternalUserRequest) =>
          a.ExternalUser.fromPartial({
            ...EXTERNAL_USER,
            externalId: request.externalId,
            name: request.name ?? EXTERNAL_USER.name,
            email: request.email ?? EXTERNAL_USER.email,
            roles:
              request.roles.length > 0 ? request.roles : EXTERNAL_USER.roles
          })
      ),
      deleteExternalUser: this.on(
        'deleteExternalUser',
        (request: pb.admin.DeleteExternalUserRequest) =>
          a.DeleteExternalUserResponse.fromPartial(request)
      ),
      listExternalUserKeys: this.on('listExternalUserKeys', () =>
        a.ListExternalUserKeysResponse.fromPartial({ keys: [KEY] })
      ),
      createExternalUserKey: this.on(
        'createExternalUserKey',
        (request: pb.admin.CreateExternalUserKeyRequest) =>
          a.CreateExternalUserKeyResponse.fromPartial({
            key: { ...KEY, name: request.name },
            value: KEY_VALUE
          })
      ),
      deleteExternalUserKey: this.on(
        'deleteExternalUserKey',
        (request: pb.admin.DeleteExternalUserKeyRequest) =>
          a.DeleteExternalUserKeyResponse.fromPartial(request)
      ),
      impersonateExternalUser: this.on(
        'impersonateExternalUser',
        (request: pb.admin.ImpersonateExternalUserRequest) => {
          const live = this.keysOf(request.externalId)
          if (live.size >= this.keyCap) {
            throw new Refusal({
              code: status.RESOURCE_EXHAUSTED,
              details: `${request.externalId} already holds ${live.size} live keys`
            })
          }
          this.keys += 1
          const expiresAt = Math.floor(this.clock() / 1000) + this.keyLifetime
          live.set(`key-${this.keys}`, expiresAt * 1000)
          this.minted.set(`key-${this.keys}`, {
            externalId: request.externalId,
            expiresAt: expiresAt * 1000
          })
          return a.ImpersonationKey.fromPartial({
            value: `impersonation-${request.externalId}-${this.keys}`,
            expiresAt,
            expiresIn: this.sendExpiresIn ? this.keyLifetime : 0,
            roles: ['creator'],
            keyId: `key-${this.keys}`
          })
        }
      ),
      endImpersonation: this.on(
        'endImpersonation',
        (request: pb.admin.EndImpersonationRequest) => {
          // As the real service does: a key already ended, or never minted
          // for this user, is not found.
          if (!this.keysOf(request.externalId).delete(request.keyId)) {
            throw new Refusal({
              code: status.NOT_FOUND,
              details: `no live key ${request.keyId}`
            })
          }
          return a.EndImpersonationResponse.fromPartial(request)
        }
      )
    } satisfies Record<AdminMethod, unknown> as UntypedServiceImplementation
  }

  /**
   * The keys a user holds that have neither been ended nor expired.
   *
   * @param externalId The user.
   * @returns Their live keys, key id to expiry in milliseconds.
   */
  keysOf(externalId: string): Map<string, number> {
    let live = this.liveKeys.get(externalId)
    if (live === undefined) {
      live = new Map()
      this.liveKeys.set(externalId, live)
    }
    for (const [keyId, expiresAt] of live) {
      if (expiresAt <= this.clock()) {
        live.delete(keyId)
      }
    }
    return live
  }
}

/** The health service, answering the probe every client construction makes. */
export class FakeHealthService {
  /** What to report. */
  status: ServingStatus = ServingStatus.SERVING

  /** The service names that were probed, in call order. */
  readonly checkedServices: string[] = []

  /** The metadata each probe arrived with — asserted to carry no credential. */
  readonly metadata: Record<string, unknown>[] = []

  /** Failures to serve once each before answering normally. */
  readonly transientErrors: Failure[] = []

  /** Forget everything recorded so far. */
  forget(): void {
    this.checkedServices.length = 0
    this.metadata.length = 0
  }

  /** The grpc-js service implementation to register on a {@link Server}. */
  implementation(): UntypedServiceImplementation {
    return {
      check: (
        call: ServerUnaryCall<HealthCheckRequest, HealthCheckResponse>,
        callback: sendUnaryData<HealthCheckResponse>
      ): void => {
        this.checkedServices.push(call.request.service)
        this.metadata.push(call.metadata.getMap())
        const transient = this.transientErrors.shift()
        if (transient !== undefined) {
          callback(asServiceError(transient))
          return
        }
        callback(null, { status: this.status })
      }
    } as UntypedServiceImplementation
  }
}

function withDetails(failure: RichFailure): Record<string, Buffer> {
  return {
    [STATUS_DETAILS_KEY]: encodeStatusDetails(failure.code, failure.details, {
      reason: failure.reason,
      domain: failure.domain,
      metadata: failure.metadata ?? {}
    })
  }
}

function asServiceError(
  failure: Failure,
  trailer?: Record<string, Buffer>
): Partial<ServiceError> {
  const error: Partial<ServiceError> = {
    code: failure.code,
    details: failure.details
  }
  if (trailer !== undefined) {
    // Carried on a real Metadata object, so the binary value is framed the way
    // a real server's trailer would be.
    const metadata = new Metadata()
    for (const [key, value] of Object.entries(trailer)) {
      metadata.set(key, value)
    }
    error.metadata = metadata
  }
  return error
}

/** A running fake, and the address to dial it on. */
export class Harness {
  /** Every call every service but health received, in the order they arrived. */
  readonly journal: Arrival[] = []

  /** The memory service, for arranging responses and reading what arrived. */
  readonly memory = new FakeMemoryService('memory', this.journal)

  /** The token exchange an API client's credentials go to. */
  readonly tokens = new FakeTokenService('tokens', this.journal)

  /** The administration service, impersonation included. */
  readonly admin = new FakeAdminService('admin', this.journal)

  /** The health service, for the probe every client construction makes. */
  readonly health = new FakeHealthService()

  /** `host:port` of the loopback socket, with a port the OS chose. */
  address = ''

  private readonly server = new Server()

  /** Bind the server and start serving. */
  async start(): Promise<void> {
    this.server.addService(
      pb.MemoryServiceService,
      this.memory.implementation()
    )
    this.server.addService(
      pb.auth.TokenServiceService,
      this.tokens.implementation()
    )
    this.server.addService(
      pb.admin.AdminServiceService,
      this.admin.implementation()
    )
    this.server.addService(HealthService, this.health.implementation())
    const port = await new Promise<number>((resolve, reject) => {
      this.server.bindAsync(
        '127.0.0.1:0',
        ServerCredentials.createInsecure(),
        (error, bound) => {
          if (error) {
            reject(error)
            return
          }
          resolve(bound)
        }
      )
    })
    this.address = `127.0.0.1:${port}`
  }

  /**
   * Stop serving, without waiting for in-flight calls.
   *
   * Forced rather than graceful. A graceful shutdown waits for handlers that
   * are still pending, which a test using {@link FakeMemoryService.delays}
   * would make it wait the full delay for — long after the call under test
   * has been decided.
   */
  async stop(): Promise<void> {
    this.server.forceShutdown()
  }

  /** Forget every call recorded so far, on every service. */
  forget(): void {
    this.journal.length = 0
    this.memory.forget()
    this.tokens.forget()
    this.admin.forget()
    this.health.forget()
  }
}

/**
 * Start a harness, hand it to `body`, and stop it however that ends.
 *
 * @param body What to run against the running fake.
 */
export async function withHarness(
  body: (harness: Harness) => Promise<void>
): Promise<void> {
  const harness = new Harness()
  await harness.start()
  try {
    await body(harness)
  } finally {
    await harness.stop()
  }
}
