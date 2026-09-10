/**
 * Hand-written codecs for the two protobuf surfaces the export does not
 * generate.
 *
 * Taking a dependency for them would put this SDK over its dependency budget,
 * which is exactly two packages, so the handful of fields actually needed are
 * encoded and decoded here with the `BinaryReader` and `BinaryWriter` that
 * `@bufbuild/protobuf` already provides for the generated client.
 *
 * Only the fields the SDK reads are modelled. Everything else on the wire is
 * skipped, which is what protobuf's own forward compatibility asks for.
 */

import { BinaryReader, BinaryWriter } from '@bufbuild/protobuf/wire'
import type { ServiceDefinition } from '@grpc/grpc-js'

/** Serving states `grpc.health.v1.HealthCheckResponse` can report. */
export enum ServingStatus {
  UNKNOWN = 0,
  SERVING = 1,
  NOT_SERVING = 2,
  SERVICE_UNKNOWN = 3
}

/** Name of a serving state, for the message on {@link ServingStatus} mismatch. */
export function servingStatusName(status: number): string {
  return ServingStatus[status] ?? `UNRECOGNIZED(${status})`
}

/** `grpc.health.v1.HealthCheckRequest`. */
export interface HealthCheckRequest {
  /** The service to probe. Empty asks after the server as a whole. */
  service: string
}

/** `grpc.health.v1.HealthCheckResponse`. */
export interface HealthCheckResponse {
  /** What the server reports about the service that was named. */
  status: ServingStatus
}

function encodeHealthCheckRequest(message: HealthCheckRequest): Buffer {
  const writer = new BinaryWriter()
  if (message.service !== '') {
    writer.uint32(10).string(message.service)
  }
  return Buffer.from(writer.finish())
}

function decodeHealthCheckRequest(input: Buffer): HealthCheckRequest {
  const reader = new BinaryReader(input)
  const message: HealthCheckRequest = { service: '' }
  while (reader.pos < reader.len) {
    const tag = reader.uint32()
    if (tag === 10) {
      message.service = reader.string()
      continue
    }
    if ((tag & 7) === 4 || tag === 0) {
      break
    }
    reader.skip(tag & 7)
  }
  return message
}

function encodeHealthCheckResponse(message: HealthCheckResponse): Buffer {
  const writer = new BinaryWriter()
  if (message.status !== 0) {
    writer.uint32(8).int32(message.status)
  }
  return Buffer.from(writer.finish())
}

function decodeHealthCheckResponse(input: Buffer): HealthCheckResponse {
  const reader = new BinaryReader(input)
  const message: HealthCheckResponse = { status: ServingStatus.UNKNOWN }
  while (reader.pos < reader.len) {
    const tag = reader.uint32()
    if (tag === 8) {
      message.status = reader.int32()
      continue
    }
    if ((tag & 7) === 4 || tag === 0) {
      break
    }
    reader.skip(tag & 7)
  }
  return message
}

/**
 * `grpc.health.v1.Health`, with only the method this SDK calls.
 *
 * `Watch` is deliberately absent: the SDK never opens one, and leaving it out
 * of the definition means a server built from this reports `UNIMPLEMENTED` for
 * it without anyone writing that handler.
 */
export const HealthService: ServiceDefinition = {
  check: {
    path: '/grpc.health.v1.Health/Check',
    requestStream: false,
    responseStream: false,
    requestSerialize: (value: HealthCheckRequest) =>
      encodeHealthCheckRequest(value),
    requestDeserialize: (value: Buffer) => decodeHealthCheckRequest(value),
    responseSerialize: (value: HealthCheckResponse) =>
      encodeHealthCheckResponse(value),
    responseDeserialize: (value: Buffer) => decodeHealthCheckResponse(value)
  }
}

/** The metadata key carrying a serialised `google.rpc.Status`. */
export const STATUS_DETAILS_KEY = 'grpc-status-details-bin'

/** The `google.protobuf.Any` type URL prefix for `google.rpc.ErrorInfo`. */
const ERROR_INFO_TYPE_URL = 'type.googleapis.com/google.rpc.ErrorInfo'

/** The machine-readable half of a `google.rpc.ErrorInfo` detail. */
export interface ErrorInfo {
  /** Why the call failed, in the service's own vocabulary. */
  reason: string
  /** Who defined that vocabulary. Memco's is `memco.ai`. */
  domain: string
}

function decodeErrorInfo(input: Uint8Array): ErrorInfo {
  const reader = new BinaryReader(input)
  const message: ErrorInfo = { reason: '', domain: '' }
  while (reader.pos < reader.len) {
    const tag = reader.uint32()
    if (tag === 10) {
      message.reason = reader.string()
      continue
    }
    if (tag === 18) {
      message.domain = reader.string()
      continue
    }
    if ((tag & 7) === 4 || tag === 0) {
      break
    }
    reader.skip(tag & 7)
  }
  return message
}

/**
 * Read every `google.rpc.ErrorInfo` out of a `grpc-status-details-bin` trailer.
 *
 * The trailer holds a serialised `google.rpc.Status`, whose field 3 is a
 * repeated `google.protobuf.Any`. Only the `Any` entries carrying an
 * `ErrorInfo` are decoded, and only their `reason` and `domain` are read —
 * they are the whole of what {@link MemcoSunsetError} is discriminated by.
 *
 * A malformed, truncated or absent trailer yields an empty array rather than
 * throwing. The caller is reporting a failure already; losing a discriminator
 * is a smaller loss than replacing their error with a parse error.
 */
export function errorDetails(trailer: Uint8Array): ErrorInfo[] {
  const found: ErrorInfo[] = []
  try {
    const reader = new BinaryReader(trailer)
    while (reader.pos < reader.len) {
      const tag = reader.uint32()
      // Field 3 of google.rpc.Status: repeated google.protobuf.Any details.
      if (tag === 26) {
        const detail = new BinaryReader(reader.bytes())
        let typeUrl = ''
        // Widened: BinaryReader.bytes() yields Uint8Array<ArrayBufferLike>,
        // and a bare `new Uint8Array()` is the narrower Uint8Array<ArrayBuffer>.
        let value: Uint8Array<ArrayBufferLike> = new Uint8Array()
        while (detail.pos < detail.len) {
          const inner = detail.uint32()
          if (inner === 10) {
            typeUrl = detail.string()
            continue
          }
          if (inner === 18) {
            value = detail.bytes()
            continue
          }
          if ((inner & 7) === 4 || inner === 0) {
            break
          }
          detail.skip(inner & 7)
        }
        if (typeUrl === ERROR_INFO_TYPE_URL) {
          found.push(decodeErrorInfo(value))
        }
        continue
      }
      if ((tag & 7) === 4 || tag === 0) {
        break
      }
      reader.skip(tag & 7)
    }
  } catch {
    return []
  }
  return found
}

/**
 * Build a `grpc-status-details-bin` trailer carrying one `ErrorInfo`.
 *
 * Used only by the test harness, which has to produce the trailer the service
 * sends in order to prove {@link errorDetails} reads it. Keeping the encoder
 * beside the decoder is what stops the two drifting.
 */
export function encodeStatusDetails(
  code: number,
  message: string,
  info: ErrorInfo
): Buffer {
  const detail = new BinaryWriter()
  detail.uint32(10).string(info.reason)
  detail.uint32(18).string(info.domain)

  const any = new BinaryWriter()
  any.uint32(10).string(ERROR_INFO_TYPE_URL)
  any.uint32(18).bytes(detail.finish())

  const status = new BinaryWriter()
  status.uint32(8).int32(code)
  status.uint32(18).string(message)
  status.uint32(26).bytes(any.finish())
  return Buffer.from(status.finish())
}
