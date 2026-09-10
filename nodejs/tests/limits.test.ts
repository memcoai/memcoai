/**
 * What the client remembers of the limits the service reports.
 *
 * The rule the whole module exists for is that an unreported number is not
 * zero: a client told nothing checks nothing and lets the service rule.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { Known } from '../src/internal/limits.js'
import type { DomainEntry, Limits } from '../src/types.js'

function limits(overrides: Partial<Limits> = {}): Limits {
  return {
    maxQueryCharacters: 0,
    maxTextCharacters: 0,
    maxIdxCharacters: 0,
    maxSources: 0,
    maxFeedbackEntries: 0,
    maxImportMemories: 0,
    maxImportQueriesPerMemory: 0,
    maxImportInsightsPerMemory: 0,
    maxImportTagsPerMemory: 0,
    ...overrides
  }
}

function domain(slug: string, maxTagsPerQuery: number): DomainEntry {
  return {
    slug,
    title: '',
    summary: '',
    whenToSearch: '',
    whenToSave: '',
    whatNotToSave: '',
    tagsDescription: '',
    filterTagTypes: [],
    versionTagTypes: [],
    maxTagsPerQuery
  }
}

test('a client that has been told nothing knows no limits', () => {
  const known = new Known()
  assert.equal(known.limits, null)
  assert.equal(known.maxTags('coding'), 0)
})

test('a response reporting limits teaches them to the client', () => {
  const known = new Known()
  known.update(limits({ maxQueryCharacters: 100 }), [domain('coding', 4)])
  assert.equal(known.limits?.maxQueryCharacters, 100)
  assert.equal(known.maxTags('coding'), 4)
})

test('a later response carrying no limits leaves the known ones standing', () => {
  // A response that reports nothing is silence, not a retraction. Clearing on
  // it would make the client stop checking for reasons the service never gave.
  const known = new Known()
  known.update(limits({ maxSources: 3 }), [])
  known.update(null, [domain('coding', 2)])
  assert.equal(known.limits?.maxSources, 3)
  assert.equal(known.maxTags('coding'), 2)
})

test('per-domain tag caps accumulate rather than replacing one another', () => {
  const known = new Known()
  known.update(null, [domain('coding', 4)])
  known.update(null, [domain('writing', 2)])
  assert.equal(known.maxTags('coding'), 4)
  assert.equal(known.maxTags('writing'), 2)
})

test('a later report of the same domain replaces its cap', () => {
  const known = new Known()
  known.update(null, [domain('coding', 4)])
  known.update(null, [domain('coding', 1)])
  assert.equal(known.maxTags('coding'), 1)
})

test('a domain the client was never told about has no tag cap', () => {
  const known = new Known()
  known.update(null, [domain('coding', 4)])
  assert.equal(known.maxTags('unheard-of'), 0)
})

test('a call scoped by a session has no tag cap, its domain being invisible', () => {
  // The session supplies the domain server-side, so the client cannot look up
  // a cap for it. Zero means "no cap", which leaves the trim to the service.
  const known = new Known()
  known.update(null, [domain('coding', 4)])
  assert.equal(known.maxTags(null), 0)
})

test('a caller who reported nothing at all leaves the known limits standing', () => {
  // The JavaScript counterpart of passing null: an omitted argument arrives as
  // undefined, and must mean the same thing rather than wiping what is known.
  const known = new Known()
  known.update(limits({ maxSources: 3 }), [])
  known.update(undefined as unknown as null, [])
  assert.equal(known.limits?.maxSources, 3)
})

test('the limits a client remembers cannot be changed from outside', () => {
  // TypeScript's `readonly` is compile time only, so the copy is what stops a
  // caller mutating the numbers every later request is validated against.
  const known = new Known()
  const reported = limits({ maxQueryCharacters: 100 })
  known.update(reported, [])
  ;(reported as { maxQueryCharacters: number }).maxQueryCharacters = 1
  assert.equal(known.limits?.maxQueryCharacters, 100)
})
