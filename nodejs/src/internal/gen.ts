/**
 * The generated gRPC clients, re-exported.
 *
 * This is the only file in the SDK that names a path under `client/`, which the
 * service export empties and rewrites wholesale. Everything else reaches the
 * contracts through here, so a change to the export's output layout is a
 * one-line edit rather than a sweep.
 *
 * Import it as a namespace — `import * as pb from './internal/gen.js'`. The
 * generated module exports `Tag`, `Instructions`, `Limits`, `DataSource` and a
 * dozen more names that the SDK's own public types in `src/types.ts` also use,
 * and the namespace is what keeps the wire shape and the public shape legibly
 * apart.
 *
 * The memory contract is spread at the top level, as it always has been. The
 * administration and token contracts each sit under a namespace of their own —
 * `pb.admin`, `pb.auth` — because all three modules export helpers under the
 * same names (`protobufPackage`, `DeepPartial`, …), and a flat re-export of
 * more than one of them would be ambiguous.
 */
export * from '../../client/src/gen/memcoai/memory/v1/memory.js'
export * as admin from '../../client/src/gen/memcoai/admin/v1/admin.js'
export * as auth from '../../client/src/gen/memcoai/auth/v1/auth.js'
