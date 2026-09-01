/**
 * The enums, and the forward compatibility of reading them off the wire.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  DataSource,
  ImportStatus,
  RevertOutcome,
  dataSourceFromWire,
  importStatusFromWire,
  revertOutcomeFromWire
} from '../src/types.js'

test('the enums carry the numbers the contract assigns', () => {
  // Pinned because these cross the wire as integers: renumbering one here would
  // silently change what every write claims about its own provenance.
  assert.equal(DataSource.UNSPECIFIED, 0)
  assert.equal(DataSource.USER, 1)
  assert.equal(DataSource.AGENT, 2)
  assert.equal(RevertOutcome.MEMORY_REMOVED, 1)
  assert.equal(RevertOutcome.REFUSED, 7)
  assert.equal(ImportStatus.QUEUED, 1)
  assert.equal(ImportStatus.DUPLICATE, 4)
})

test('a known wire value reads back as itself', () => {
  for (const value of [0, 1, 2]) {
    assert.equal(dataSourceFromWire(value), value)
  }
  for (const value of [0, 1, 2, 3, 4, 5, 6, 7]) {
    assert.equal(revertOutcomeFromWire(value), value)
  }
  for (const value of [0, 1, 2, 3, 4]) {
    assert.equal(importStatusFromWire(value), value)
  }
})

test('a value this SDK predates folds to unspecified rather than escaping', () => {
  // The service may add members after this package is published. Folding is
  // what lets an older client keep working against a newer service instead of
  // handing a caller an integer that matches no member.
  assert.equal(dataSourceFromWire(99), DataSource.UNSPECIFIED)
  assert.equal(revertOutcomeFromWire(99), RevertOutcome.UNSPECIFIED)
  assert.equal(importStatusFromWire(99), ImportStatus.UNSPECIFIED)
})

test('the generated UNRECOGNIZED sentinel folds too', () => {
  // ts-proto emits -1 for a value it could not name; it must not reach a caller
  // as a member that does not exist.
  assert.equal(dataSourceFromWire(-1), DataSource.UNSPECIFIED)
  assert.equal(revertOutcomeFromWire(-1), RevertOutcome.UNSPECIFIED)
  assert.equal(importStatusFromWire(-1), ImportStatus.UNSPECIFIED)
})
