/**
 * Status-code translation and the RESOURCE_EXHAUSTED discriminator.
 *
 * Mirrors python/tests/test_errors.py so the two SDKs' error hierarchies stay
 * provably in step, not just similarly documented.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { Metadata, status } from '@grpc/grpc-js'

import {
  MemcoAPIError,
  MemcoAlreadyExistsError,
  MemcoAuthenticationError,
  MemcoConfigError,
  MemcoError,
  MemcoExternalUserNeedsCustomerNetworkError,
  MemcoInternalError,
  MemcoInvalidRequestError,
  MemcoNotFoundError,
  MemcoPermissionError,
  MemcoPreconditionFailedError,
  MemcoResourceExhaustedError,
  MemcoSunsetError,
  MemcoTimeoutError,
  MemcoUnavailableError,
  MemcoUnhealthyError,
  MemcoUserAlreadyAssignedNetworkError,
  ResourceExhaustedKind,
  fromServiceError
} from '../src/errors.js'
import {
  STATUS_DETAILS_KEY,
  encodeStatusDetails
} from '../src/internal/wire.js'

const STATUS_TO_CLASS = [
  [status.UNAUTHENTICATED, MemcoAuthenticationError],
  [status.PERMISSION_DENIED, MemcoPermissionError],
  [status.INVALID_ARGUMENT, MemcoInvalidRequestError],
  [status.NOT_FOUND, MemcoNotFoundError],
  [status.ALREADY_EXISTS, MemcoAlreadyExistsError],
  [status.FAILED_PRECONDITION, MemcoPreconditionFailedError],
  [status.RESOURCE_EXHAUSTED, MemcoResourceExhaustedError],
  [status.UNAVAILABLE, MemcoUnavailableError],
  [status.DEADLINE_EXCEEDED, MemcoTimeoutError],
  [status.INTERNAL, MemcoInternalError],
  [status.UNKNOWN, MemcoInternalError],
  [status.DATA_LOSS, MemcoInternalError]
] as const

for (const [code, expected] of STATUS_TO_CLASS) {
  test(`status ${status[code]} maps to ${expected.name}`, () => {
    const error = fromServiceError({ code, details: 'boom' })
    assert.equal(error.constructor, expected)
    assert.equal(error.code, code)
    assert.equal(error.detail, 'boom')
    assert.ok(error instanceof MemcoAPIError)
    assert.ok(error instanceof MemcoError)
  })
}

test('an unhealthy result is catchable as unavailable', () => {
  assert.ok(MemcoUnhealthyError.prototype instanceof MemcoUnavailableError)
})

test('a sunset is catchable coarsely', () => {
  // An existing `catch`-and-check-`instanceof MemcoAPIError` must keep
  // catching it, and a caller that does not care which version was blocked
  // can catch the general precondition-failure class.
  assert.ok(MemcoSunsetError.prototype instanceof MemcoPreconditionFailedError)
  assert.ok(MemcoSunsetError.prototype instanceof MemcoAPIError)
})

test('a config error is not an API error', () => {
  assert.ok(MemcoConfigError.prototype instanceof MemcoError)
  assert.ok(!(MemcoConfigError.prototype instanceof MemcoAPIError))
})

const RESOURCE_EXHAUSTED_HEURISTIC = [
  ['rate limit exceeded', ResourceExhaustedKind.RATE_LIMIT],
  ['RATE LIMIT EXCEEDED', ResourceExhaustedKind.RATE_LIMIT],
  [
    "You've reached your daily search limit, try again tomorrow",
    ResourceExhaustedKind.QUOTA
  ],
  ['something else entirely', ResourceExhaustedKind.UNKNOWN]
] as const

for (const [details, kind] of RESOURCE_EXHAUSTED_HEURISTIC) {
  test(`RESOURCE_EXHAUSTED classifies "${details}" as ${kind}`, () => {
    const error = fromServiceError({
      code: status.RESOURCE_EXHAUSTED,
      details
    })
    assert.ok(error instanceof MemcoResourceExhaustedError)
    assert.equal(error.kind, kind)
  })
}

test('only a resource-exhausted error carries a kind', () => {
  const error = fromServiceError({ code: status.NOT_FOUND, details: 'nope' })
  assert.ok(!('kind' in error))
})

// --- what a precondition failure names itself by -------------------------

/** A FAILED_PRECONDITION carrying one ErrorInfo, as the service sends it. */
function refused(
  reason: string,
  metadata: Record<string, string>,
  domain = 'memco.ai'
): MemcoAPIError {
  const trailer = new Metadata()
  trailer.set(
    STATUS_DETAILS_KEY,
    encodeStatusDetails(status.FAILED_PRECONDITION, 'refused', {
      reason,
      domain,
      metadata
    })
  )
  return fromServiceError({
    code: status.FAILED_PRECONDITION,
    details: 'network_move_required: refused',
    metadata: trailer
  })
}

test('a user already placed in the domain is refused with where they are', () => {
  const error = refused('USER_ALREADY_ASSIGNED_NETWORK', {
    current_network_id: 'network-a',
    current_network_name: 'Acme'
  })
  assert.ok(error instanceof MemcoUserAlreadyAssignedNetworkError)
  assert.ok(error instanceof MemcoPreconditionFailedError)
  assert.equal(error.name, 'MemcoUserAlreadyAssignedNetworkError')
  assert.equal(error.currentNetworkId, 'network-a')
  assert.equal(error.currentNetworkName, 'Acme')
  assert.equal(error.detail, 'network_move_required: refused')
  assert.equal(
    MemcoUserAlreadyAssignedNetworkError.reason,
    'USER_ALREADY_ASSIGNED_NETWORK'
  )
})

test('an external user outside a customer network is refused with the scope it needs', () => {
  const error = refused('EXTERNAL_USER_NEEDS_CUSTOMER_NETWORK', {
    required_network_scope: 'customer'
  })
  assert.ok(error instanceof MemcoExternalUserNeedsCustomerNetworkError)
  assert.ok(!(error instanceof MemcoSunsetError))
  assert.equal(error.requiredNetworkScope, 'customer')
  assert.equal(
    MemcoExternalUserNeedsCustomerNetworkError.reason,
    'EXTERNAL_USER_NEEDS_CUSTOMER_NETWORK'
  )
})

test('a refusal whose metadata is missing reports null rather than failing', () => {
  const error = refused('USER_ALREADY_ASSIGNED_NETWORK', {})
  assert.ok(error instanceof MemcoUserAlreadyAssignedNetworkError)
  assert.equal(error.currentNetworkId, null)
  assert.equal(error.currentNetworkName, null)
})

test('a reason this SDK does not know stays a plain precondition failure, with its metadata', () => {
  const error = refused('BILLING_ACCOUNT_DISABLED', { account: 'acct-1' })
  assert.equal(error.constructor, MemcoPreconditionFailedError)
  assert.ok(error instanceof MemcoPreconditionFailedError)
  assert.deepEqual(error.metadata, { account: 'acct-1' })
})

test("a reason from another domain is not read as Memco's", () => {
  const error = refused(
    'USER_ALREADY_ASSIGNED_NETWORK',
    { current_network_id: 'network-a' },
    'example.com'
  )
  assert.equal(error.constructor, MemcoPreconditionFailedError)
  assert.ok(error instanceof MemcoPreconditionFailedError)
  assert.deepEqual(error.metadata, {})
})

test('a precondition failure with no trailer carries empty metadata', () => {
  const error = fromServiceError({
    code: status.FAILED_PRECONDITION,
    details: 'nope'
  })
  assert.ok(error instanceof MemcoPreconditionFailedError)
  assert.deepEqual(error.metadata, {})
})

test('the metadata cannot be changed', () => {
  const error = refused('USER_ALREADY_ASSIGNED_NETWORK', {
    current_network_id: 'network-a'
  })
  assert.ok(error instanceof MemcoPreconditionFailedError)
  assert.ok(Object.isFrozen(error.metadata))
  assert.throws(() => {
    ;(error.metadata as Record<string, string>)['current_network_id'] = 'x'
  }, TypeError)
})

test('a metadata key that names a prototype is kept as plain data', () => {
  // Assigned rather than defined, it would hit the prototype setter and vanish.
  const error = refused('SOMETHING_ELSE', JSON.parse('{"__proto__": "kept"}'))
  assert.ok(error instanceof MemcoPreconditionFailedError)
  assert.ok(Object.hasOwn(error.metadata, '__proto__'))
  assert.equal(Object.getPrototypeOf(error.metadata), Object.prototype)
})

test('a sunset carries the metadata the service attached, like any precondition failure', () => {
  const error = refused('API_VERSION_SUNSET', { sunset_date: '2026-01-01' })
  assert.ok(error instanceof MemcoSunsetError)
  assert.equal(error.kind, 'api_version')
  assert.deepEqual(error.metadata, { sunset_date: '2026-01-01' })
  assert.ok(Object.isFrozen(error.metadata))
})
