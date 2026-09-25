/**
 * The renewing credential behind every call: one mint at a time, no key ended
 * under a call.
 *
 * A client's token and an impersonated session's key are each held by one of
 * these. It mints a credential when none is held or the one held has reached
 * its renewal point, and ends a credential it has replaced only once the last
 * call using it has finished. It is driven here with plain functions and the
 * fake clock, so each rule is pinned without a server in the way; the
 * 0.8-of-lifetime renewal point is set by whoever mints, and is pinned where
 * the client does that.
 */

import assert from 'node:assert/strict'
import { test, type TestContext } from 'node:test'
import { setTimeout as sleep } from 'node:timers/promises'
import { inspect } from 'node:util'

import { status } from '@grpc/grpc-js'

import {
  MemcoConfigError,
  MemcoTimeoutError,
  MemcoUnavailableError
} from '../src/errors.js'
import { Minted, Renewing, clock } from '../src/internal/auth.js'
import { FakeClock } from './fakeClock.js'
import { stderrOf } from './stderr.js'

/** Seconds from minting to each test credential's renewal point. */
const DUE_AFTER = 100

/**
 * Seconds from minting to each test credential's expiry: the renewal point is
 * four fifths of the way there, as the client sets it.
 */
const LIFETIME = DUE_AFTER / 0.8

const RACERS = 8

/**
 * A mint that numbers what it hands out, and an end that records what it is
 * given.
 */
class Mints {
  /** How many credentials have been minted. */
  minted = 0
  /** How many mints were attempted, failed ones included. */
  attempts = 0
  /** The value of each credential ended, in order. */
  readonly ended: string[] = []
  /** When set, minting throws it instead. */
  failure: Error | null = null
  /** Milliseconds each mint takes, widening the window for a second mint. */
  delay = 0

  readonly mint = async (): Promise<Minted> => {
    this.attempts += 1
    if (this.delay > 0) {
      await sleep(this.delay)
    }
    if (this.failure !== null) {
      throw this.failure
    }
    this.minted += 1
    return new Minted(
      `value-${this.minted}`,
      clock.monotonic() + DUE_AFTER * 1000,
      `key-${this.minted}`,
      clock.monotonic() + LIFETIME * 1000
    )
  }

  readonly end = async (minted: Minted): Promise<void> => {
    this.ended.push(minted.value)
  }
}

function faked(t: TestContext): FakeClock {
  const fake = new FakeClock().install()
  t.after(() => fake.restore())
  return fake
}

function unavailable(): MemcoUnavailableError {
  return new MemcoUnavailableError(status.UNAVAILABLE, 'token service down')
}

function leaseOnce(renewing: Renewing): Promise<string> {
  return renewing.lease(async held => held.value)
}

/**
 * Let a renewal running in the background land: a mint without a delay
 * settles within the jobs already queued, and one with a delay by then.
 */
function landed(delay = 0): Promise<void> {
  return sleep(delay + 5)
}

// --- minting and renewal -------------------------------------------------

test('the first lease mints and later ones reuse it', async t => {
  faked(t)
  const mints = new Mints()
  const renewing = new Renewing(mints.mint, mints.end)
  assert.deepEqual(
    [await leaseOnce(renewing), await leaseOnce(renewing)],
    ['value-1', 'value-1']
  )
  assert.equal(mints.minted, 1)
})

test('a credential is renewed once its renewal point arrives, in the background', async t => {
  // Due is not expired: the lease that finds the credential due goes on under
  // it, and the renewal serves the leases after it.
  const fake = faked(t)
  const mints = new Mints()
  const renewing = new Renewing(mints.mint, mints.end)
  await leaseOnce(renewing)
  fake.advance(DUE_AFTER - 1)
  assert.equal(await leaseOnce(renewing), 'value-1')
  assert.equal(mints.attempts, 1)
  fake.advance(1)
  assert.equal(await leaseOnce(renewing), 'value-1')
  await landed()
  assert.equal(mints.attempts, 2)
  assert.equal(await leaseOnce(renewing), 'value-2')
})

test('a lease past the renewal point is not held by a slow renewal while its credential is valid', async t => {
  // A token service or admin service answering slowly must not time out calls
  // the current credential would have served.
  const fake = faked(t)
  const mints = new Mints()
  const renewing = new Renewing(mints.mint, mints.end)
  await leaseOnce(renewing)
  fake.advance(DUE_AFTER * 1.0625)
  mints.delay = 1000
  const started = Date.now()
  assert.deepEqual(
    await Promise.all([
      renewing.lease(async held => held.value, Date.now() + 50),
      renewing.lease(async held => held.value, Date.now() + 50)
    ]),
    ['value-1', 'value-1']
  )
  assert.ok(Date.now() - started < 500, 'the leases waited on the renewal')
  // One renewal, in the background, shared by both.
  assert.equal(mints.attempts, 2)
})

test('a lease with no valid credential still waits on the renewal', async t => {
  const fake = faked(t)
  const mints = new Mints()
  const renewing = new Renewing(mints.mint, mints.end)
  await leaseOnce(renewing)
  fake.advance(LIFETIME)
  mints.delay = 20
  assert.equal(await leaseOnce(renewing), 'value-2')
})

test('a static token is never renewed', async t => {
  // An API key has no lifetime the SDK knows of, so it is held as due never.
  const fake = faked(t)
  let minted = 0
  const renewing = new Renewing(async () => {
    minted += 1
    return new Minted('static-token', Number.POSITIVE_INFINITY)
  })
  await leaseOnce(renewing)
  fake.advance(10 * 365 * 86400)
  assert.equal(await leaseOnce(renewing), 'static-token')
  assert.equal(minted, 1)
})

test('a replaced credential is ended as soon as nothing uses it', async t => {
  // One live key per session: the service caps how many a user may hold.
  const fake = faked(t)
  const mints = new Mints()
  const renewing = new Renewing(mints.mint, mints.end)
  await leaseOnce(renewing)
  fake.advance(DUE_AFTER)
  await leaseOnce(renewing)
  await landed()
  assert.deepEqual(mints.ended, ['value-1'])
})

test('a replaced credential is ended only after its last lease exits', async t => {
  // Ending a key under a call still carrying it would fail that call
  // UNAUTHENTICATED for no reason of the caller's.
  const fake = faked(t)
  const mints = new Mints()
  const renewing = new Renewing(mints.mint, mints.end)
  await renewing.lease(async first => {
    assert.equal(first.value, 'value-1')
    await renewing.lease(async () => {
      fake.advance(DUE_AFTER)
      await leaseOnce(renewing)
      await landed()
      assert.equal(await leaseOnce(renewing), 'value-2')
      assert.deepEqual(mints.ended, [])
    })
    assert.deepEqual(mints.ended, [])
  })
  assert.deepEqual(mints.ended, ['value-1'])
})

test('a failed renewal leases the current credential while it has not expired, and the next lease retries', async t => {
  // Due is not expired: the current credential still works for the fifth of
  // its lifetime left, so a renewal the service refuses costs no call.
  const fake = faked(t)
  const mints = new Mints()
  const renewing = new Renewing(mints.mint, mints.end)
  await leaseOnce(renewing)
  fake.advance(DUE_AFTER * 1.0625) // 0.85 of the lifetime
  mints.failure = unavailable()
  const written = await stderrOf(async () => {
    assert.equal(await leaseOnce(renewing), 'value-1')
    await landed()
  })
  assert.deepEqual(mints.ended, [])
  assert.match(written, /WARNING .*key-1/)
  assert.ok(!written.includes('value-1'), written)
  mints.failure = null
  assert.equal(await leaseOnce(renewing), 'value-1')
  await landed()
  assert.equal(await leaseOnce(renewing), 'value-2')
  assert.deepEqual(mints.ended, ['value-1'])
})

test('a failed renewal is raised once the current credential has expired', async t => {
  const fake = faked(t)
  const mints = new Mints()
  const renewing = new Renewing(mints.mint, mints.end)
  await leaseOnce(renewing)
  fake.advance(LIFETIME)
  mints.failure = unavailable()
  await assert.rejects(leaseOnce(renewing), MemcoUnavailableError)
  assert.deepEqual(mints.ended, [])
})

test('leases made at once fall back together, and the failure is logged once', async t => {
  const fake = faked(t)
  const mints = new Mints()
  const renewing = new Renewing(mints.mint, mints.end)
  await leaseOnce(renewing)
  fake.advance(DUE_AFTER * 1.0625)
  mints.delay = 20
  mints.failure = unavailable()
  let values: string[] = []
  const written = await stderrOf(async () => {
    values = await race(renewing)
    await landed(mints.delay)
  })
  assert.deepEqual(values, Array(RACERS).fill('value-1'))
  assert.equal(mints.attempts, 2)
  assert.equal(
    written.split('\n').filter(line => line.includes('WARNING')).length,
    1,
    written
  )
})

test('a failed lease is not counted as a user', async t => {
  // Otherwise the credential would look busy for ever, and close() would wait
  // on a lease that no longer exists to end it.
  const fake = faked(t)
  const mints = new Mints()
  const renewing = new Renewing(mints.mint, mints.end)
  await leaseOnce(renewing)
  fake.advance(LIFETIME)
  mints.failure = unavailable()
  await assert.rejects(leaseOnce(renewing), MemcoUnavailableError)
  await renewing.close()
  assert.deepEqual(mints.ended, ['value-1'])
})

test('a lease whose call fails still lets its credential be ended', async t => {
  faked(t)
  const mints = new Mints()
  const renewing = new Renewing(mints.mint, mints.end)
  await assert.rejects(
    renewing.lease(async () => {
      throw unavailable()
    }),
    MemcoUnavailableError
  )
  await renewing.close()
  assert.deepEqual(mints.ended, ['value-1'])
})

test('without an end a replaced credential is simply dropped', async t => {
  // The contract has no call that revokes a client token, so that use has
  // nothing to end one with.
  const fake = faked(t)
  const mints = new Mints()
  const renewing = new Renewing(mints.mint)
  await leaseOnce(renewing)
  fake.advance(DUE_AFTER)
  await leaseOnce(renewing)
  await landed()
  assert.equal(await leaseOnce(renewing), 'value-2')
  await renewing.close()
  assert.deepEqual(mints.ended, [])
})

// --- close ---------------------------------------------------------------

test('close ends an idle credential at once and only once', async t => {
  faked(t)
  const mints = new Mints()
  const renewing = new Renewing(mints.mint, mints.end)
  await leaseOnce(renewing)
  await renewing.close()
  await renewing.close()
  assert.deepEqual(mints.ended, ['value-1'])
})

test('close leaves a busy credential to its last lease', async t => {
  faked(t)
  const mints = new Mints()
  const renewing = new Renewing(mints.mint, mints.end)
  await renewing.lease(async () => {
    await renewing.close()
    assert.deepEqual(mints.ended, [])
  })
  assert.deepEqual(mints.ended, ['value-1'])
})

test('close before anything was minted ends nothing', async t => {
  faked(t)
  const mints = new Mints()
  const renewing = new Renewing(mints.mint, mints.end)
  await renewing.close()
  assert.deepEqual([mints.minted, mints.ended], [0, []])
})

test('a lease after close is refused without minting', async t => {
  faked(t)
  const mints = new Mints()
  const renewing = new Renewing(mints.mint, mints.end)
  await renewing.close()
  await assert.rejects(leaseOnce(renewing), (error: unknown) => {
    assert.ok(error instanceof MemcoConfigError)
    assert.match(error.message, /closed/)
    return true
  })
  assert.equal(mints.minted, 0)
})

test('a close during a mint ends what the mint produces once its lease is done', async t => {
  // The Node form of an open abandoned part-way: nothing can cancel a mint the
  // service is already answering, so the credential it produces must still be
  // ended rather than left live against the user's cap until it expires.
  faked(t)
  const mints = new Mints()
  mints.delay = 20
  const renewing = new Renewing(mints.mint, mints.end)
  const leasing = leaseOnce(renewing)
  const closing = renewing.close()
  assert.deepEqual(await Promise.all([leasing, closing]), [
    'value-1',
    undefined
  ])
  assert.deepEqual([mints.minted, mints.ended], [1, ['value-1']])
})

test('a close during a mint that fails ends nothing and does not throw', async t => {
  faked(t)
  const mints = new Mints()
  mints.delay = 20
  mints.failure = unavailable()
  const renewing = new Renewing(mints.mint, mints.end)
  const leasing = assert.rejects(leaseOnce(renewing), MemcoUnavailableError)
  await renewing.close()
  await leasing
  assert.deepEqual([mints.minted, mints.ended], [0, []])
})

test('a close landing between a mint and the lease waiting on it refuses that lease', async t => {
  // The close is made from a job queued before the waiting lease resumes, so
  // it retires the fresh credential while no lease has yet counted itself.
  // Sending it then would carry a key being ended.
  faked(t)
  const ended: string[] = []
  let renewing: Renewing | undefined
  let closing: Promise<void> | undefined
  renewing = new Renewing(
    () =>
      new Promise<Minted>(settle => {
        setTimeout(() => {
          settle(new Minted('value-1', Number.POSITIVE_INFINITY))
          queueMicrotask(() => {
            closing = renewing?.close()
          })
        }, 10)
      }),
    async minted => {
      ended.push(minted.value)
    }
  )
  let sent = false
  await assert.rejects(
    renewing.lease(async () => {
      sent = true
    }, Date.now() + 60_000),
    (error: unknown) => {
      assert.ok(error instanceof MemcoConfigError)
      assert.match(error.message, /closed/)
      return true
    }
  )
  await closing
  assert.equal(sent, false, 'the lease sent a credential being ended')
  assert.deepEqual(ended, ['value-1'])
})

test('a deadline too far off for a timer still waits for the mint', async t => {
  // Node clamps a timer past 2^31-1 ms to one millisecond, which would refuse
  // at once a call that asked to wait a month.
  faked(t)
  const mints = new Mints()
  mints.delay = 20
  const renewing = new Renewing(mints.mint, mints.end)
  const month = 30 * 86_400_000
  assert.equal(
    await renewing.lease(async held => held.value, Date.now() + month),
    'value-1'
  )
})

test('a second close waits for the end the first one started', async t => {
  faked(t)
  let finish: (() => void) | undefined
  const ended: string[] = []
  const renewing = new Renewing(
    async () => new Minted('value-1', Number.POSITIVE_INFINITY),
    minted =>
      new Promise<void>(settle => {
        finish = () => {
          ended.push(minted.value)
          settle()
        }
      })
  )
  await leaseOnce(renewing)
  const first = renewing.close()
  let secondDone = false
  const second = renewing.close().then(() => {
    secondDone = true
  })
  await sleep(10)
  assert.equal(secondDone, false, 'the second close returned before the end')
  finish?.()
  await Promise.all([first, second])
  assert.deepEqual(ended, ['value-1'])
})

test(
  'the last lease on a replaced credential does not wait for its end',
  { timeout: 5000 },
  async t => {
    // The end belongs to the holder; the call that happened to be last has its
    // answer already and is not held while the key is revoked.
    const fake = faked(t)
    const mints = new Mints()
    let finish: (() => void) | undefined
    const renewing = new Renewing(mints.mint, minted => {
      mints.ended.push(`${minted.value} started`)
      return new Promise<void>(settle => {
        finish = settle
      })
    })
    const outcome = await renewing.lease(async first => {
      fake.advance(DUE_AFTER)
      await leaseOnce(renewing)
      await landed()
      assert.equal(await leaseOnce(renewing), 'value-2')
      return first.value
    })
    assert.equal(outcome, 'value-1')
    assert.deepEqual(mints.ended, ['value-1 started'])
    finish?.()
  }
)

// --- concurrency ---------------------------------------------------------

function race(renewing: Renewing): Promise<string[]> {
  return Promise.all(Array.from({ length: RACERS }, () => leaseOnce(renewing)))
}

test('leases made at once share a single mint', async t => {
  faked(t)
  const mints = new Mints()
  mints.delay = 20
  const renewing = new Renewing(mints.mint, mints.end)
  assert.deepEqual(await race(renewing), Array(RACERS).fill('value-1'))
  assert.equal(mints.minted, 1)
})

test('leases made at once past the renewal point renew once', async t => {
  const fake = faked(t)
  const mints = new Mints()
  const renewing = new Renewing(mints.mint, mints.end)
  await leaseOnce(renewing)
  fake.advance(DUE_AFTER)
  mints.delay = 20
  assert.deepEqual(await race(renewing), Array(RACERS).fill('value-1'))
  await landed(mints.delay)
  assert.equal(mints.minted, 2)
  assert.deepEqual(mints.ended, ['value-1'])
  assert.equal(await leaseOnce(renewing), 'value-2')
})

test('leases made at once share one failed mint rather than each retrying', async t => {
  // Each queued caller minting again in turn would make the last of them wait
  // for every failure before it, far past its own deadline.
  faked(t)
  const mints = new Mints()
  mints.delay = 20
  mints.failure = unavailable()
  const renewing = new Renewing(mints.mint, mints.end)
  const settled = await Promise.allSettled(
    Array.from({ length: RACERS }, () => leaseOnce(renewing))
  )
  assert.ok(settled.every(one => one.status === 'rejected'))
  assert.equal(mints.attempts, 1)
  // Cleared once it settled, so the next lease tries again.
  mints.failure = null
  assert.equal(await leaseOnce(renewing), 'value-1')
  assert.equal(mints.attempts, 2)
})

test('a lease waits on a mint no longer than its own deadline, and the mint goes on', async t => {
  faked(t)
  const mints = new Mints()
  mints.delay = 600
  const renewing = new Renewing(mints.mint, mints.end)
  const started = Date.now()
  await assert.rejects(
    renewing.lease(async held => held.value, Date.now() + 20),
    MemcoTimeoutError
  )
  assert.ok(Date.now() - started < 400, 'the lease outwaited its deadline')
  // The mint it gave up on still lands, and serves the next lease.
  assert.equal(await leaseOnce(renewing), 'value-1')
  assert.equal(mints.attempts, 1)
})

test('a lease that gave up on a mint leaves what it produces to be ended', async t => {
  faked(t)
  const mints = new Mints()
  mints.delay = 50
  const renewing = new Renewing(mints.mint, mints.end)
  await assert.rejects(
    renewing.lease(async held => held.value, Date.now() + 10),
    MemcoTimeoutError
  )
  await renewing.close()
  assert.deepEqual([mints.minted, mints.ended], [1, ['value-1']])
})

test('a renewal does not hold its leases on the end of the credential it replaced', async t => {
  // The end belongs to the holder, not to the calls that happened to find the
  // credential due: it runs to completion without them waiting on it, and
  // never cut short — abandoning it would strand a live key against the
  // user's cap until it expired.
  const fake = faked(t)
  const mints = new Mints()
  let ending: (() => void) | undefined
  const renewing = new Renewing(mints.mint, minted => {
    mints.ended.push(`${minted.value} started`)
    return new Promise<void>(settle => {
      ending = () => {
        mints.ended.push(`${minted.value} done`)
        settle()
      }
    })
  })
  await leaseOnce(renewing)
  fake.advance(LIFETIME)
  // Expired, so this lease waits on the renewal — and on nothing after it.
  assert.equal(
    await renewing.lease(async held => held.value, Date.now() + 50),
    'value-2'
  )
  assert.deepEqual(mints.ended, ['value-1 started'])
  ending?.()
  await landed()
  assert.deepEqual(mints.ended, ['value-1 started', 'value-1 done'])
})

// --- secrecy -------------------------------------------------------------

test('the value never reaches a rendering of the credential', () => {
  const minted = new Minted('secret-bearer-value', 1, 'key-1')
  for (const rendered of [
    inspect(minted),
    inspect(minted, { showHidden: true, depth: null, getters: true }),
    JSON.stringify(minted),
    JSON.stringify({ minted }),
    inspect({ ...minted }),
    String(minted)
  ]) {
    assert.ok(!rendered.includes('secret-bearer-value'), rendered)
  }
  assert.match(inspect(minted), /key-1/)
  // Still readable where the SDK needs it: it is what each call sends.
  assert.equal(minted.value, 'secret-bearer-value')
})

test('a holder renders without the credential it holds', async t => {
  faked(t)
  const renewing = new Renewing(
    async () => new Minted('secret-bearer-value', Number.POSITIVE_INFINITY)
  )
  await leaseOnce(renewing)
  assert.ok(!inspect(renewing, { depth: null }).includes('secret-bearer'))
  assert.ok(!JSON.stringify(renewing).includes('secret-bearer'))
})
