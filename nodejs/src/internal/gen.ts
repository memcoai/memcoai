/**
 * The generated gRPC client, re-exported.
 *
 * This is the only file in the SDK that names a path under `client/`, which the
 * service export empties and rewrites wholesale. Everything else reaches the
 * contract through here, so a change to the export's output layout is a
 * one-line edit rather than a sweep.
 *
 * Import it as a namespace — `import * as pb from './internal/gen.js'`. The
 * generated module exports `Tag`, `Instructions`, `Limits`, `DataSource` and a
 * dozen more names that the SDK's own public types in `src/types.ts` also use,
 * and the namespace is what keeps the wire shape and the public shape legibly
 * apart.
 */
export * from '../../client/src/gen/memco/memory/v1/memory.js'
