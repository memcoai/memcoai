/**
 * Where the package is, for the suites that compare against its files.
 *
 * `tsconfig.test.json` compiles this to `build/js/tests/`, three levels below
 * the package. Nothing in `src/` may do this: code that asks where it is running
 * breaks once a bundler moves it.
 */

import { join } from 'node:path'

export const packageRoot = join(import.meta.dirname, '..', '..', '..')
