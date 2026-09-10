/**
 * Status-code translation and the RESOURCE_EXHAUSTED discriminator.
 *
 * Mirrors python/tests/test_errors.py so the two SDKs' error hierarchies stay
 * provably in step, not just similarly documented.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { status } from '@grpc/grpc-js'

import {
  MemcoAPIError,
  MemcoAuthenticationError,
  MemcoConfigError,
  MemcoError,
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
  ResourceExhaustedKind,
  fromServiceError
} from '../src/errors.js'

const STATUS_TO_CLASS = [
  [status.UNAUTHENTICATED, MemcoAuthenticationError],
  [status.PERMISSION_DENIED, MemcoPermissionError],
  [status.INVALID_ARGUMENT, MemcoInvalidRequestError],
  [status.NOT_FOUND, MemcoNotFoundError],
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
