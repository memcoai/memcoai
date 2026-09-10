/**
 * A strict reader for `SDK_PROVENANCE.yaml`.
 *
 * Deliberately not a YAML library. The file is machine-generated with a fixed
 * shape, and a published SDK should not make every consumer install a parser
 * for one metadata file. The trade is that anything not matching the expected
 * shape raises rather than being quietly tolerated, which is the right way
 * round for a descriptor whose whole job is to be exact.
 *
 * Structure is read by indentation rather than by pattern, because the
 * descriptor nests several languages' sections that reuse the same key names —
 * `protoc` and `protobuf` each appear more than once.
 */

import { readFileSync } from 'node:fs'

import { MemcoConfigError } from '../errors.js'
import type { Provenance, ProtoRecord } from '../types.js'
import { PROVENANCE_FILE } from './resources.js'

const RESOURCE = 'SDK_PROVENANCE.yaml'

/**
 * Drop a trailing comment.
 *
 * A `#` opens one only at the start of the line or after whitespace, and never
 * inside quotes. Splitting on every `#` would corrupt a value like `"abc#123"`.
 */
function stripComment(raw: string): string {
  let quote: string | null = null
  for (let index = 0; index < raw.length; index += 1) {
    const char = raw[index]
    if (quote !== null) {
      if (char === quote) {
        quote = null
      }
      continue
    }
    if (char === '"' || char === "'") {
      quote = char
      continue
    }
    if (char === '#' && (index === 0 || /\s/.test(raw[index - 1] ?? ''))) {
      return raw.slice(0, index)
    }
  }
  return raw
}

/** Read one scalar, refusing the shapes this reader does not model. */
function scalarValue(raw: string, where: string): string {
  const value = stripComment(raw).trim()
  if (
    value === '|' ||
    value === '>' ||
    ['|-', '>-', '|+', '>+'].includes(value.slice(0, 2))
  ) {
    throw new MemcoConfigError(
      `${RESOURCE} uses an unsupported block scalar for ${where}`
    )
  }
  if (
    value.length >= 2 &&
    value[0] === value[value.length - 1] &&
    (value[0] === '"' || value[0] === "'")
  ) {
    return value.slice(1, -1)
  }
  return value
}

interface Line {
  indent: number
  content: string
}

/** Drop blank and comment-only lines, and measure what is left. */
function significant(text: string): Line[] {
  const lines: Line[] = []
  for (const raw of text.replace(/\r\n/g, '\n').split('\n')) {
    // trimEnd() rather than a /\s+$/ replace: that pattern backtracks
    // catastrophically on a long run of leading whitespace — measured at ~3s
    // for 100k spaces — and this is the only non-linear step in the parser.
    const stripped = stripComment(raw).trimEnd()
    if (stripped.trim() === '') {
      continue
    }
    lines.push({
      indent: stripped.length - stripped.trimStart().length,
      content: stripped.trim()
    })
  }
  return lines
}

/**
 * Read one `protos` entry's own fields.
 *
 * Only lines at the entry's own indent count, so a nested block — a signature,
 * a dependency list — cannot override the entry's real values.
 */
function entryFields(body: Line[]): Record<string, string> {
  const fields: Record<string, string> = {}
  const base = body[0]?.indent
  if (base === undefined) {
    return fields
  }
  for (const line of body) {
    if (line.indent !== base) {
      continue
    }
    const at = line.content.indexOf(':')
    if (at < 0) {
      continue
    }
    const key = line.content.slice(0, at).trim()
    if (key !== 'path' && key !== 'sha256') {
      continue
    }
    if (key in fields) {
      throw new MemcoConfigError(
        `${RESOURCE} has a protos entry naming ${key} twice`
      )
    }
    fields[key] = scalarValue(line.content.slice(at + 1), key)
  }
  return fields
}

/**
 * Read the `protos` sequence.
 *
 * A YAML sequence may be indented under its key or written flush with it; both
 * are accepted, the latter being what most emitters produce.
 */
function readProtos(lines: Line[], start: number): ProtoRecord[] {
  const base = lines[start - 1]?.indent ?? 0
  const body: Line[] = []
  let index = start
  while (index < lines.length) {
    const line = lines[index]
    if (line === undefined) {
      break
    }
    if (
      line.indent > base ||
      (line.indent === base && line.content.startsWith('-'))
    ) {
      body.push(line)
      index += 1
      continue
    }
    break
  }

  const records: ProtoRecord[] = []
  let current: Line[] = []
  // Tracked apart from `current`, because a dash with nothing under it starts
  // an entry while contributing no lines to it. Testing `current.length` alone
  // would drop that entry silently, where this reader's whole contract is to
  // refuse a shape it does not model rather than guess at it.
  let started = false
  const flush = (): void => {
    if (!started) {
      return
    }
    started = false
    const fields = entryFields(current)
    if (Object.keys(fields).length === 0) {
      throw new MemcoConfigError(`${RESOURCE} has an empty protos entry`)
    }
    const { path, sha256 } = fields
    if (path === undefined || sha256 === undefined) {
      throw new MemcoConfigError(
        `${RESOURCE} has a protos entry missing path or sha256: ${JSON.stringify(fields)}`
      )
    }
    records.push({ path, sha256 })
    current = []
  }

  for (const line of body) {
    if (line.content.startsWith('-')) {
      flush()
      started = true
      const rest = line.content.slice(1)
      if (rest.trim() !== '') {
        // Seeded at the column the field really starts in, so its siblings
        // measure against that rather than against the dash.
        const offset = rest.length - rest.trimStart().length
        current.push({ indent: line.indent + 1 + offset, content: rest.trim() })
      }
      continue
    }
    current.push(line)
  }
  flush()
  return records
}

/**
 * Read a descriptor's contents.
 *
 * @param text The descriptor, as written by the export.
 * @returns What it records about the generated client.
 * @throws {@link MemcoConfigError} if it is missing `server_commit` or `protos`,
 *   or if any part of it is not in the shape this reader models.
 */
export function parse(text: string): Provenance {
  const lines = significant(text)
  let serverCommit: string | null = null
  const protos: ProtoRecord[] = []

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]
    if (line === undefined || line.indent !== 0) {
      continue
    }
    if (line.content.startsWith('server_commit:')) {
      serverCommit = scalarValue(
        line.content.slice('server_commit:'.length),
        'server_commit'
      )
      continue
    }
    if (line.content === 'protos:') {
      protos.push(...readProtos(lines, index + 1))
    }
  }

  if (serverCommit === null || serverCommit === '') {
    throw new MemcoConfigError(`${RESOURCE} is missing server_commit`)
  }
  if (protos.length === 0) {
    throw new MemcoConfigError(`${RESOURCE} lists no protos`)
  }
  return { serverCommit, protos }
}

let cached: Provenance | undefined

/**
 * Where the generated client in this package came from.
 *
 * Reports what the descriptor records; it does not re-verify the checksums.
 * That is CI's job, and it runs against the contract itself, which an installed
 * package does not carry.
 *
 * The descriptor is read once and the result reused.
 *
 * @returns The server commit the export was cut from, and the contract files
 *   it was generated from.
 * @throws {@link MemcoConfigError} if the descriptor is absent from the
 *   installed package, or is not in the shape the export writes.
 *
 * @example
 * ```ts
 * import { provenance } from '@memcoai/memcoai'
 *
 * console.log(provenance().serverCommit)
 * console.log(provenance().protos[0]?.path)
 * ```
 */
export function provenance(): Provenance {
  if (cached === undefined) {
    let text: string
    try {
      text = readFileSync(PROVENANCE_FILE, 'utf8')
    } catch {
      throw new MemcoConfigError(
        `${RESOURCE} is missing from the installed @memcoai/memcoai package; ` +
          'the tarball was built wrongly'
      )
    }
    cached = parse(text)
  }
  return cached
}
