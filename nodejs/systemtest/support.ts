/**
 * What the live suites share: credentials, polling, names, and cleanup.
 *
 * Not a test file itself — `npm run system-test` runs only `*.test.js` — so
 * nothing here runs unless a suite calls it.
 *
 * The memory suites need `MEMCO_API_TOKEN`; the administration and
 * impersonation suites need an API client's `MEMCO_CLIENT_ID` and
 * `MEMCO_CLIENT_SECRET` instead. Each client here names its credential
 * explicitly, because CI sets both kinds and the pair would otherwise win over
 * the token. `MEMCO_API_TLS=false` dials without TLS, for a local development
 * server such as `localhost:50052`: every client here reads it from the
 * environment.
 *
 * The organisation these run against is shared with the other SDKs' suites,
 * possibly at the same time. So everything this creates carries a `nodesys-`
 * name unique to the run, only what this run created is ever changed or
 * deleted, and nothing asserts on organisation-wide counts.
 */

import { randomBytes } from 'node:crypto'

import { Memco, MemcoNotFoundError } from '../src/index.js'
import type { ExternalUser, Network } from '../src/index.js'

export const TOKEN_ENV = 'MEMCO_API_TOKEN'
const CLIENT_ID_ENV = 'MEMCO_CLIENT_ID'
const CLIENT_SECRET_ENV = 'MEMCO_CLIENT_SECRET'

/** Why an administration test is skipped, when it is. */
export const NO_CLIENT =
  `${CLIENT_ID_ENV} and ${CLIENT_SECRET_ENV} are not both set: ` +
  'administration and impersonation need an API client'

/** The longest a write is given to become searchable. */
export const INGEST_TIMEOUT_MS = 180_000

/**
 * The pauses between one poll's attempts: 1s, 2s, 4s, 8s, then 15s each.
 *
 * Ingestion often lands within a few seconds, and a fixed long pause would
 * spend most of every wait idle. The cap keeps a slow wait within the
 * service's search rate limit.
 */
export function* pollWaits(): Generator<number, never> {
  let wait = 1000
  for (;;) {
    yield Math.min(wait, 15_000)
    wait *= 2
  }
}

/** A tag naming this run: the CI run and attempt, or random bytes locally. */
function runTag(): string {
  const run = process.env['GITHUB_RUN_ID']
  const attempt = process.env['GITHUB_RUN_ATTEMPT'] ?? '1'
  return run ? `${run}-${attempt}` : randomBytes(4).toString('hex')
}

/**
 * A name no other run, and no other object of this run, carries.
 *
 * In CI it names the run too, so one a failed cleanup left behind can be traced
 * to the run that made it.
 */
function unique(): string {
  return `nodesys-${runTag()}-${randomBytes(3).toString('hex')}`
}

/** Whether the API client's credentials are both set. */
export function hasClient(): boolean {
  return Boolean(process.env[CLIENT_ID_ENV] && process.env[CLIENT_SECRET_ENV])
}

/** A client authenticated as the API client, not yet connected. */
export function adminClient(): Memco {
  return new Memco({
    clientId: process.env[CLIENT_ID_ENV],
    clientSecret: process.env[CLIENT_SECRET_ENV]
  })
}

/**
 * Run `body` against a connected API client, closing it whatever happens.
 *
 * Built before the try and connected inside it, because `connect()` leaves the
 * channel open when it fails, so the caller owns the close either way.
 */
export async function withAdmin<T>(
  body: (admin: Memco) => Promise<T>
): Promise<T> {
  const admin = adminClient()
  try {
    await admin.connect()
    return await body(admin)
  } finally {
    await admin.close()
  }
}

/**
 * The root network the suites create their customer networks under.
 *
 * There is one root per memory domain, and the first the service lists is
 * taken: its domain is the one the administration and impersonation tests run
 * in. It is only read and built under, never changed.
 */
export async function rootNetwork(admin: Memco): Promise<Network> {
  const [root] = (await admin.networks.list({ parentId: 'root' })).networks
  if (root === undefined) {
    throw new Error(
      'the organisation has no root network to create customer networks under'
    )
  }
  return root
}

/**
 * The networks and users one test creates, removed again however it ends.
 *
 * Each is recorded before its create is sent: a create the service commits but
 * the client sees fail, such as one answered after the deadline, still made
 * something. Deleting a network takes everything placed in it with it, the
 * memories its members wrote included, so this is also the safety net for a
 * write a session could not revert.
 */
export class Created {
  private readonly networks: string[] = []
  private readonly users: string[] = []

  constructor(
    private readonly admin: Memco,
    private readonly root: Network
  ) {}

  /** A customer network under the root, named for this run alone. */
  async network(): Promise<Network> {
    const name = unique()
    this.networks.push(name)
    return this.admin.networks.create({
      name,
      parentId: this.root.id,
      scope: 'customer',
      description:
        'A network the Node.js SDK system test creates and deletes again.'
    })
  }

  /** An external user, a creator unless asked otherwise. */
  async user(roles: string[] = ['creator']): Promise<ExternalUser> {
    const externalId = unique()
    this.users.push(externalId)
    return this.admin.users.create(externalId, { roles })
  }

  /** A new user, placed in a customer network of its own. */
  async placed(): Promise<[Network, ExternalUser]> {
    const network = await this.network()
    const user = await this.user()
    await this.admin.networks.addMember(network.id, { userId: user.id })
    return [network, user]
  }

  /**
   * Remove everything this created. Never throws: cleanup runs after a failure
   * and must not replace it with an error of its own.
   */
  async remove(): Promise<void> {
    for (const externalId of this.users.toReversed()) {
      try {
        await this.admin.users.delete(externalId)
      } catch (error) {
        if (!(error instanceof MemcoNotFoundError)) {
          console.log(`cleanup: deleting user ${externalId} failed: ${error}`)
        }
      }
    }
    for (const name of this.networks.toReversed()) {
      try {
        // Found by its exact name, since a create seen to fail returned no id.
        const { networks } = await this.admin.networks.list({
          name,
          parentId: this.root.id
        })
        for (const network of networks.filter(each => each.name === name)) {
          await this.admin.networks.delete(network.id)
        }
      } catch (error) {
        if (!(error instanceof MemcoNotFoundError)) {
          console.log(`cleanup: deleting network ${name} failed: ${error}`)
        }
      }
    }
  }
}
