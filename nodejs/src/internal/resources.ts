import { join } from 'node:path'

import { moduleDir } from './moduleDir.js'

/**
 * Root of the installed package.
 *
 * Every build puts this file at `<out>/src/internal/`, and every `outDir` is
 * exactly two segments deep — `dist/esm`, `dist/cjs`, `build/js` — so one walk
 * reaches the package root in the published ESM build, the published CommonJS
 * build and the compiled test tree alike. Changing an outDir's depth breaks all
 * three at once, which is what tests/resources.test.ts exists to catch.
 */
export const packageRoot = join(moduleDir, '..', '..', '..', '..')

/**
 * The provenance descriptor the export writes, read by {@link provenance}.
 *
 * It ships at its repository-relative path rather than being copied into
 * `dist/`, so the development tree and the published tarball resolve it
 * identically.
 */
export const PROVENANCE_FILE = join(
  packageRoot,
  'client',
  'SDK_PROVENANCE.yaml'
)

/**
 * The agent tool manifest.
 *
 * Nothing reads this at runtime — its copy is compiled into
 * `src/gen/toolCopy.ts` at build time. It ships anyway, because a consumer who
 * wants the definitions raw expects to find them in the installed package.
 */
export const TOOLS_FILE = join(
  packageRoot,
  'client',
  'src',
  'gen',
  'tools.json'
)
