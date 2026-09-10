/**
 * The copy a model reads is the service's, unchanged.
 *
 * `client/src/gen/tools.json` carries the agent-facing descriptions the hosted
 * MCP server publishes, and the repository's tool-doc generator compiles them
 * into `src/gen/toolCopy.ts`. Nothing opens the manifest at runtime, so this
 * suite is what holds the two ends together: it reads the manifest directly and
 * asserts the copy survived the round trip into the module.
 *
 * A failure here means the module has drifted from the manifest. Run
 * `make tool-docs` from the repository root and commit the result.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

import {
  ANSWERED,
  NESTED_COPY,
  OFFERED,
  SDK_SHAPED,
  TOOL_COPY,
  TOOL_PREFIX,
  type ToolCopy
} from '../src/gen/toolCopy.js'
import { TOOLS_FILE } from '../src/internal/resources.js'

/** One tool as the manifest publishes it, in the part this suite reads. */
interface Published {
  readonly name: string
  readonly description: string
  readonly parameters: Readonly<Record<string, string>>
}

const document = JSON.parse(readFileSync(TOOLS_FILE, 'utf8')) as {
  tools: Published[]
}
const published = new Map(document.tools.map(tool => [tool.name, tool]))

// Widened from their literal types, which are what a caller wants and what a
// loop over the manifest's own strings cannot be checked against.
const copies: Readonly<Record<string, ToolCopy>> = TOOL_COPY
const nested: Readonly<Record<string, string>> = NESTED_COPY
const offered: readonly string[] = OFFERED
const answered: readonly string[] = ANSWERED
const shaped: ReadonlySet<string> = new Set<string>(SDK_SHAPED)

/**
 * The manifest's cross-reference marker.
 *
 * Built fresh per use rather than shared: a global regex carries `lastIndex`,
 * and `matchAll` honours it, so one stray `.test()` elsewhere in this file
 * would make every reader here silently skip the first marker of a string.
 */
function marker(): RegExp {
  return /\$\{tool:([a-z_]+)\}/g
}

/** The one manifest key this SDK spells differently, as in the generator. */
const ALIASES: Readonly<Record<string, string>> = { op_id: 'operation_id' }

/**
 * The name/parameter pairs the comparison below must have made.
 *
 * Pinned, because every skip in that loop is silent: a lookup that stops
 * matching would otherwise leave this passing while comparing less and less.
 * It names every parameter the manifest does, including the ones the bound
 * session supplies and a model therefore never sees.
 */
const COMPARED = [
  'create_memory.content',
  'create_memory.domain',
  'create_memory.query',
  'create_memory.sessionId',
  'create_memory.title',
  'enrich_memory.content',
  'enrich_memory.memoryIdx',
  'enrich_memory.sessionId',
  'enrich_memory.sources',
  'enrich_memory.title',
  'get_memory.idx',
  'import_memories.domain',
  'import_memories.memories',
  'import_memories.sessionId',
  'revert_memory.operationId',
  'search.domain',
  'search.query',
  'search.sessionId',
  'share_feedback.sessionId',
  'start_session.domain'
]

/** The nested paths `NESTED_COPY` must carry, pinned for the same reason. */
const NESTED_COMPARED = [
  'memories[].insights',
  'memories[].insights[].content',
  'memories[].insights[].title',
  'memories[].queries'
]

/** The manifest's entry for one tool, failing rather than returning nothing. */
function publishedAs(name: string): Published {
  const tool = published.get(name)
  assert.ok(tool, `the manifest publishes no ${name}`)
  return tool
}

/** Render the manifest's markers the way the module spells them. */
function spelled(text: string): string {
  return text.replace(marker(), (_whole, name: string) =>
    offered.includes(name) ? TOOL_PREFIX + name : name
  )
}

/** Spell a snake_case manifest key the way the module keys it. */
function camelised(name: string): string {
  const [head, ...rest] = name.split('_')
  return (
    head +
    rest.map(word => word.charAt(0).toUpperCase() + word.slice(1)).join('')
  )
}

/** Every operation a piece of the manifest's copy names. */
function referenced(text: string): string[] {
  return [...text.matchAll(marker())].map(match => match[1])
}

test('the module carries every tool the manifest publishes', () => {
  assert.deepEqual(Object.keys(copies).sort(), [...published.keys()].sort())
  for (const name of [...offered, ...answered]) {
    assert.ok(published.has(name), name)
  }
})

test('every description is the service copy', () => {
  for (const [name, copy] of Object.entries(copies)) {
    assert.equal(copy.description, spelled(publishedAs(name).description), name)
  }
})

test('every parameter the manifest names carries its copy', () => {
  const compared: string[] = []
  const carried: string[] = []
  for (const [name, copy] of Object.entries(copies)) {
    for (const parameter of Object.keys(copy.parameters)) {
      carried.push(`${name}.${parameter}`)
    }
    for (const [key, text] of Object.entries(publishedAs(name).parameters)) {
      if (key.includes('[')) continue // Nested; NESTED_COPY carries it.
      // Through the alias table first: the manifest calls it op_id and this SDK
      // calls it operationId, and camelising the manifest's own spelling would
      // look for an opId that no method takes.
      const field = ALIASES[key] ?? key
      if (shaped.has(field)) continue // Hand-written where the schema is built.
      const parameter = camelised(field)
      const where = `${name}.${parameter}`
      assert.equal(copy.parameters[parameter], spelled(text), where)
      compared.push(where)
    }
  }
  assert.deepEqual(compared.sort(), [...COMPARED].sort())
  // The same set from the module's side. Without it the loop above proves only
  // that nothing was dropped, and a parameter the module invented — or one of
  // SDK_SHAPED leaking back in with the wire copy on it, which is the case the
  // omission exists to prevent — is never looked at.
  assert.deepEqual(carried.sort(), [...COMPARED].sort())
})

test('every nested field the manifest names carries its copy', () => {
  const compared: string[] = []
  for (const [path, text] of Object.entries(
    publishedAs('import_memories').parameters
  )) {
    if (!path.includes('[')) continue
    // A nested path is judged by its last segment, which is the field it
    // describes: memories[].tags is the same tags as the flat one.
    if (shaped.has(path.slice(path.lastIndexOf('.') + 1))) continue
    assert.equal(nested[path], spelled(text), path)
    compared.push(path)
  }
  assert.deepEqual(compared.sort(), [...NESTED_COMPARED].sort())
  // Nothing invented either, so the module states the manifest and no more.
  assert.deepEqual(Object.keys(nested).sort(), [...NESTED_COMPARED].sort())
})

test('no marker ever reaches a model', () => {
  // The copy lives in template literals, where a surviving ${ is interpolation
  // rather than text, so this is the last line of a defence that starts in the
  // generator.
  for (const [name, copy] of Object.entries(copies)) {
    assert.ok(!copy.description.includes('${'), name)
    for (const [parameter, text] of Object.entries(copy.parameters)) {
      assert.ok(!text.includes('${'), `${name}.${parameter}`)
    }
  }
  for (const [path, text] of Object.entries(nested)) {
    assert.ok(!text.includes('${'), path)
  }
})

test('a tool named in the copy is named as a model would call it', () => {
  // "rate it with share_feedback" points a model at a tool that does not exist,
  // and it has no other way to find the one that does. Derived from the
  // manifest rather than pinned, so the next export cannot rot it.
  let named = 0
  for (const [name, copy] of Object.entries(copies)) {
    for (const one of referenced(publishedAs(name).description)) {
      if (!offered.includes(one)) continue // Not a tool here; a briefing says so.
      assert.ok(
        copy.description.includes(TOOL_PREFIX + one),
        `${name} -> ${one}`
      )
      named += 1
    }
  }
  assert.ok(
    named > 0,
    'no cross-reference was exercised, so this proved nothing'
  )
})

test('every operation the copy names is either offered or answered', () => {
  // The service's copy points at list_domains and start_session, which are
  // right for the surface it was written for and are not tools here. A model
  // told to call one has been pointed at nothing, so a briefing has to say so —
  // and this is what notices when the copy starts naming a third. The generator
  // will still emit it, spelled bare; what it cannot do is decide what a
  // briefing should then say.
  const named = new Set<string>()
  for (const tool of published.values()) {
    for (const text of [tool.description, ...Object.values(tool.parameters)]) {
      for (const one of referenced(text)) named.add(one)
    }
  }
  assert.ok(named.size > 0, 'no marker was found, so this proved nothing')
  assert.deepEqual(
    [...named].filter(one => !offered.includes(one) && !answered.includes(one)),
    []
  )
})
