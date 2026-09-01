/**
 * A real in-process gRPC server for the suite to run against.
 *
 * Nothing about gRPC is mocked. The suite dials a loopback socket and speaks
 * the real protocol, so interceptors, metadata, status codes, retry policy and
 * channel teardown are all exercised for real, because the interesting defects
 * in an SDK like this one live in exactly the layers a mock replaces.
 *
 * It needs no network access and no API key.
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
}

/** The nine methods the memory service publishes, in their grpc-js spelling. */
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

/**
 * The memory service, recording what it was asked and answering what it is told
 * to.
 */
export class FakeMemoryService {
  /** Method names in call order, including calls that then aborted. */
  readonly calls: MemoryMethod[] = []

  /** The metadata each call arrived with, in the same order as {@link calls}. */
  readonly metadata: Record<string, unknown>[] = []

  /** The last request seen per method. */
  readonly requests = new Map<MemoryMethod, unknown>()

  /** Canned responses, overriding the defaults below. */
  readonly responses = new Map<MemoryMethod, unknown>()

  /** When set, every method aborts with this instead of answering. */
  error: Failure | null = null

  /** When set, every method aborts with this and a status-details trailer. */
  richError: RichFailure | null = null

  /**
   * Failures to serve once each, per method, before answering normally.
   *
   * This is what stages "fails once, then succeeds" for the retry tests.
   */
  readonly transientErrors = new Map<MemoryMethod, Failure[]>()

  /**
   * Milliseconds to wait before answering, per method.
   *
   * Lets a deadline test be decided by the deadline instead of by a race
   * against an in-process server that would otherwise answer immediately.
   */
  readonly delays = new Map<MemoryMethod, number>()

  /** Forget everything recorded so far, leaving the configured behaviour. */
  forget(): void {
    this.calls.length = 0
    this.metadata.length = 0
    this.requests.clear()
  }

  private handle<Req, Res>(
    method: MemoryMethod,
    call: ServerUnaryCall<Req, Res>,
    callback: sendUnaryData<Res>,
    fallback: (request: Req) => Res
  ): void {
    // Recorded before any abort, so a test can count the attempts a retry made.
    this.calls.push(method)
    this.metadata.push(call.metadata.getMap())
    this.requests.set(method, call.request)

    const answer = (): void => {
      const staged = this.transientErrors.get(method)
      const transient = staged?.shift()
      if (transient !== undefined) {
        callback(asServiceError(transient))
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
      callback(
        null,
        canned === undefined ? fallback(call.request) : (canned as Res)
      )
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

  /** The grpc-js service implementation to register on a {@link Server}. */
  implementation(): UntypedServiceImplementation {
    const on =
      <Req, Res>(method: MemoryMethod, fallback: (request: Req) => Res) =>
      (call: ServerUnaryCall<Req, Res>, callback: sendUnaryData<Res>): void => {
        this.handle(method, call, callback, fallback)
      }

    return {
      listDomains: on('listDomains', () =>
        pb.ListDomainsResponse.fromPartial({})
      ),
      startSession: on('startSession', () =>
        pb.StartSessionResponse.fromPartial({ sessionId: 'session-a' })
      ),
      search: on('search', () =>
        pb.SearchResponse.fromPartial({ sessionId: 'session-a' })
      ),
      getMemory: on('getMemory', (request: pb.GetMemoryRequest) =>
        pb.GetMemoryResponse.fromPartial({ memory: { idx: request.idx } })
      ),
      createMemory: on('createMemory', () =>
        pb.CreateMemoryResponse.fromPartial({ operationId: 'create-a' })
      ),
      enrichMemory: on('enrichMemory', () =>
        pb.EnrichMemoryResponse.fromPartial({ operationId: 'enrich-a' })
      ),
      shareFeedback: on('shareFeedback', () =>
        pb.ShareFeedbackResponse.fromPartial({ sessionId: 'session-a' })
      ),
      revertMemory: on('revertMemory', (request: pb.RevertMemoryRequest) =>
        pb.RevertMemoryResponse.fromPartial({
          operationId: request.opId,
          outcome: pb.RevertOutcome.REVERT_OUTCOME_MERGED
        })
      ),
      importMemories: on(
        'importMemories',
        (request: pb.ImportMemoriesRequest) =>
          pb.ImportMemoriesResponse.fromPartial({
            results: request.memories.map((_, index) => ({
              index,
              status: pb.ImportStatus.IMPORT_STATUS_QUEUED
            }))
          })
      )
    } as UntypedServiceImplementation
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
      domain: failure.domain
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
  /** The memory service, for arranging responses and reading what arrived. */
  readonly memory = new FakeMemoryService()

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

  /** Forget every call recorded so far, on both services. */
  forget(): void {
    this.memory.forget()
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
