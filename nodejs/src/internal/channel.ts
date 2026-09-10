/**
 * Channel construction: what every call announces, and what a lost connection
 * may safely be replayed on.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import {
  Channel,
  ChannelCredentials,
  makeGenericClientConstructor,
  type CallOptions,
  type ChannelOptions,
  type Client,
  type ClientOptions,
  type ClientUnaryCall,
  type MethodConfig,
  type ServiceError
} from '@grpc/grpc-js'

import { authInterceptor } from './auth.js'
import type { ClientConfig } from './config.js'
import * as pb from './gen.js'
import { packageRoot } from './resources.js'
import {
  HealthService,
  type HealthCheckRequest,
  type HealthCheckResponse
} from './wire.js'

function readVersion(): string {
  // The installed package.json is the one place the version is written down.
  // Reaching back into src/index.ts for a constant would be circular —
  // index.ts is what pulls this module in — and would leave two copies to
  // drift apart.
  const manifest = JSON.parse(
    readFileSync(join(packageRoot, 'package.json'), 'utf8')
  ) as { version: string }
  return manifest.version
}

/**
 * Sent as the primary user agent on every channel.
 *
 * The product token is deliberately a single stable `memco-node/<version>`: the
 * service matches deprecation rules against it, and it feeds client tracking
 * that expects that shape. A client sending no recognisable token can never be
 * told its build is out of date. gRPC appends its own runtime token rather than
 * replacing this, so the transport stays identified too.
 */
export const USER_AGENT = `memco-node/${readVersion()}`

/** Full name of the memory service, taken from the generated client. */
export const MEMORY_SERVICE = pb.MemoryServiceClient.serviceName

/** Full name of the standard health service the connection check probes. */
export const HEALTH_SERVICE = 'grpc.health.v1.Health'

/**
 * The memory methods a lost connection may safely be replayed on.
 *
 * gRPC's configurable retries are at-least-once: a retry sent after the server
 * committed produces a duplicate, not a second chance. So a method belongs here
 * only if replaying it cannot mint anything or change what a later call
 * returns.
 *
 * Deliberately absent, and why each one is worse than it looks:
 *
 * - `CreateMemory`, `EnrichMemory`, `ShareFeedback`, `RevertMemory` mint an
 *   operation id on the terminal side. A replay writes twice.
 * - `ImportMemories` mints none, and the service writes each entry under an
 *   identity derived from its own content, so a replay genuinely does not write
 *   twice. It is still out: the second attempt reports what the first already
 *   landed as `DUPLICATE` rather than `QUEUED`, and that is the field a caller
 *   reads to find out what its upload achieved. Retrying would silently answer
 *   a question about this call with the state left by the previous one.
 * - `StartSession` mints a session id.
 * - `Search` mints one too. The contract is explicit that omitting `session_id`
 *   opens a session and returns it on the response, so replaying the
 *   domain-only form — the form the quickstart uses — can orphan a session the
 *   caller never learns the id of. Worse, replaying the *scoped* form can come
 *   back degraded: the first attempt already recorded delivery, so the memories
 *   return as bare `reference` handles carrying no insights. A caller iterating
 *   `insights` would see nothing and read it as "no results", which is a wrong
 *   answer rather than an error. Search is the hot path and leaving it out
 *   costs the most; a service-side idempotency key is what would let it back in.
 *
 * `GetMemory` is in despite counting the delivery: replaying it advances
 * `times_served` by one. The contract says that field "records deliveries and
 * nothing else — a frequently served result is not thereby a correct one", so a
 * duplicate moves a counter that carries no judgement, and the content it
 * returns is the same either way. That is the whole of the cost, unlike the
 * cases above.
 *
 * The health probe is retried too, and is named separately below because it
 * belongs to a service this SDK does not own.
 */
export const RETRYABLE_METHODS = ['ListDomains', 'GetMemory'] as const

const RETRY_POLICY = {
  maxAttempts: 3,
  initialBackoff: '0.1s',
  maxBackoff: '1s',
  backoffMultiplier: 2,
  // UNAVAILABLE only. DEADLINE_EXCEEDED is the caller's own deadline, so
  // retrying past it cannot help, and RESOURCE_EXHAUSTED is a rate limit or a
  // spent quota, which the caller handles by kind.
  retryableStatusCodes: ['UNAVAILABLE']
}

const SERVICE_CONFIG: { methodConfig: MethodConfig[] } = {
  methodConfig: [
    {
      name: [
        ...RETRYABLE_METHODS.map(method => ({
          service: MEMORY_SERVICE,
          method
        })),
        // The probe the client construction gates on. It creates nothing, and
        // leaving it out would make a blip during connect the one transient
        // failure the SDK cannot absorb.
        { service: HEALTH_SERVICE, method: 'Check' }
      ],
      retryPolicy: RETRY_POLICY
    }
  ]
}

/**
 * The options every channel this SDK opens is given, and no others.
 *
 * Exported so a test can assert the whole set rather than the presence of one
 * key: the omissions are decisions too. There is no keepalive and no
 * message-size cap here, because neither is the client's to decide — a limit
 * enforced only on this side is one the service never asked for.
 */
export const CHANNEL_OPTIONS: ChannelOptions = {
  'grpc.primary_user_agent': USER_AGENT,
  'grpc.enable_retries': 1,
  // `service_config`, not `default_service_config`. The default is the option
  // that reads as correct here — it yields to a policy the service publishes —
  // but grpc-js has no such option at all, so a policy set that way is dropped
  // on the floor and silently never retries. The retry tests count the attempts
  // the server saw, so swapping this back fails rather than quietly disabling
  // the policy.
  //
  // The cost is real and worth naming: this one overrides whatever the resolver
  // publishes, so the service cannot hand Node clients a policy of its own
  // while it is set. That is the wrong way round for a value the service owns,
  // and it is the option that works.
  'grpc.service_config': JSON.stringify(SERVICE_CONFIG),
  // gRPC caps reconnect backoff at two minutes by default, so a client idle
  // through a short outage can sit unusable long after the service returns.
  'grpc.initial_reconnect_backoff_ms': 200,
  'grpc.max_reconnect_backoff_ms': 5000
}

/**
 * The health service, as this SDK calls it.
 *
 * Only `Check` is modelled, and only in the three-argument form: the SDK always
 * has a deadline to pass, so the shorter overloads would be dead surface.
 */
export interface HealthClient extends Client {
  /**
   * Ask the endpoint whether it is serving.
   *
   * @param request The service to probe. An empty name asks after the server as
   *   a whole, which is what the connection check sends.
   * @param options Call options, carrying the deadline.
   * @param callback Receives the reported serving status, or the failure.
   * @returns The in-flight call, for cancellation.
   */
  check(
    request: HealthCheckRequest,
    options: Partial<CallOptions>,
    callback: (
      error: ServiceError | null,
      response: HealthCheckResponse
    ) => void
  ): ClientUnaryCall
}

const HealthClientConstructor = makeGenericClientConstructor(
  HealthService,
  HEALTH_SERVICE
) as unknown as {
  new (
    address: string,
    credentials: ChannelCredentials,
    options?: ClientOptions
  ): HealthClient
}

/** One channel, and the two clients that speak over it. */
export interface Transport {
  /** The memory service, carrying the credential on every call. */
  memory: pb.MemoryServiceClient
  /** The health service, which the credential is withheld from. */
  health: HealthClient
  /**
   * Close the shared channel.
   *
   * Calling `close()` on either client does the same thing, since they hold the
   * one channel between them; this is the spelling that says so.
   */
  close(): void
}

/**
 * Open a channel with the credential interceptor attached.
 *
 * One channel serves both clients, so the health probe proves the connection
 * the memory calls then use rather than a sibling of it.
 *
 * @param config Resolved client settings.
 * @returns The clients, and the way to close what they share.
 */
export function buildTransport(config: ClientConfig): Transport {
  const credentials = config.tls
    ? // No arguments: the system trust store, which is what a public endpoint
      // with a public certificate needs and all this SDK ever dials over TLS.
      ChannelCredentials.createSsl()
    : ChannelCredentials.createInsecure()
  const channel = new Channel(config.target, credentials, CHANNEL_OPTIONS)
  const options: ClientOptions = {
    channelOverride: channel,
    // Client-level, which grpc-js lets an `interceptors` key in a call's own
    // options REPLACE rather than compose with. So nothing that builds
    // `CallOptions` for a call may ever spread a caller-supplied object into
    // them: the credential would silently come off that one call.
    interceptors: [authInterceptor(config.token)]
  }
  return {
    // `config.target` and `credentials` are ignored by both constructors —
    // `channelOverride` short-circuits them — and are passed because the
    // signatures require them, not because a second channel is configured here.
    memory: new pb.MemoryServiceClient(config.target, credentials, options),
    health: new HealthClientConstructor(config.target, credentials, options),
    close: () => {
      channel.close()
    }
  }
}
