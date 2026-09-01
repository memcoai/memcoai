/**
 * Every example must import, and importing one must run nothing.
 *
 * `tsc` resolves every import and `npm test` compiles before it runs, so an
 * example naming a symbol that no longer exists cannot stay green.
 *
 * What is left is what a compiler cannot see: whether the ESM specifiers
 * resolve at run time, whether a type-only import was elided into something
 * that no longer loads, and — the one that matters — whether the module body
 * does anything when it is loaded. An example without its entry-point guard
 * would connect to the service, and two of these would write to it.
 */

import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

import { LEGACY_TOKEN_ENV, TOKEN_ENV } from '../src/internal/config.js'

// This file compiles to `<outDir>/tests/`, and every outDir is two segments
// deep — see the walk in src/internal/resources.ts, which relies on the same
// thing. So the package root is three levels up, and the compiled examples sit
// one level up, beside this directory.
const here = fileURLToPath(new URL('.', import.meta.url))
const COMPILED = join(here, '..', 'examples')
const SOURCES = join(here, '..', '..', '..', 'examples')

// Listed from the TypeScript sources rather than from the build: those are what
// a reader copies, and a stale .js left in build/ must not invent an example.
const EXAMPLES = readdirSync(SOURCES)
  .filter(name => name.endsWith('.ts'))
  .sort()

// Node 22 has no `import.meta.main` — that arrived in 24.2 — and engines.node
// is `>=22`, so this is the spelling every example uses.
const GUARD = 'if (process.argv[1] === fileURLToPath(import.meta.url)) {'

/**
 * The compiled counterpart of one example source, as a URL to import.
 *
 * @param name The source file name, such as `quickstart.ts`.
 * @returns The `file:` URL of the JavaScript it compiled to.
 */
function compiled(name: string): string {
  return pathToFileURL(join(COMPILED, name.replace(/\.ts$/, '.js'))).href
}

test('there are examples to check', () => {
  assert.ok(EXAMPLES.length > 0, 'no examples found; the directory moved')
})

for (const name of EXAMPLES) {
  test(`${name} imports`, async () => {
    await import(compiled(name))
  })

  test(`${name} guards its entry point`, () => {
    // Without the guard, importing an example would try to reach the service.
    const source = readFileSync(join(SOURCES, name), 'utf8')
    assert.ok(source.includes(GUARD), `${name} has no entry-point guard`)
  })
}

test('importing an example runs nothing', async () => {
  const written: unknown[][] = []
  const original = console.log
  console.log = (...args: unknown[]) => {
    written.push(args)
  }
  // Taken away for the duration, so a guard that had been removed could not
  // reach the service even on a developer's machine with a live credential in
  // the shell. Two of these examples write; a suite that can perform a write
  // by accident is not one to run casually. Named from the SDK's own constants
  // rather than as strings, so renaming a variable cannot quietly defeat this.
  const held = [TOKEN_ENV, LEGACY_TOKEN_ENV].map(
    name => [name, process.env[name]] as const
  )
  for (const [name] of held) {
    delete process.env[name]
  }
  try {
    for (const name of EXAMPLES) {
      // A query string defeats the module cache, so every module body really
      // runs again under this spy rather than being handed back from the
      // import above — which would make this assertion prove nothing.
      await import(`${compiled(name)}?entry-point-guard`)
    }
  } finally {
    console.log = original
    for (const [name, value] of held) {
      if (value !== undefined) {
        process.env[name] = value
      }
    }
  }
  assert.deepEqual(written, [])
})
