/**
 * The SDK's loggers and the level they run at.
 *
 * Node has no standard library logger, so this is a small one rather than a
 * configuration of somebody else's. That is deliberate: the SDK's dependency
 * budget is two packages, and pulling in a logging framework to write five
 * lines to stderr would spend it on the least interesting thing in the package
 * — while also making the SDK's output obey whatever that framework's globals
 * happen to say, which is exactly the coupling an application is entitled not
 * to have imposed on it.
 *
 * Every record the SDK writes goes through a logger named `memco.<area>` —
 * `memco.config`, `memco.client`, `memco.validate` — so a record names the part
 * of the SDK that wrote it. Each reads
 * `<timestamp> <logger> <LEVEL> <message>`, and the timestamp is ISO 8601 in
 * UTC rather than local time, because a library writing into somebody else's
 * log has no business guessing their timezone.
 *
 * The SDK runs at `info` unless told otherwise. At that level it says one line
 * when a client connects, one when it closes, and reports a rejected
 * credential. The detail — every RPC with its outcome and duration, where the
 * credential and endpoint came from, and any list the service trimmed — is a
 * level below, at `debug`. `MEMCO_LOG` changes that, and `none` turns it off.
 *
 * The level is process-wide, because there is one stream to write to and the
 * setting is a debugging knob rather than per-client state. An application with
 * its own logging should set `MEMCO_LOG=none` and read nothing from here.
 *
 * **No credential is ever written to a record, at any level.** The records that
 * mention the credential name where it came from, never what it is.
 */

import { format } from 'node:util'

import { MemcoConfigError } from '../errors.js'

/** Environment variable that sets the SDK's log level. */
export const LOG_ENV = 'MEMCO_LOG'

/**
 * Prefix every SDK logger name carries.
 *
 * Exported so a caller reading records can match on it, and so the SDK's own
 * modules cannot disagree about the spelling.
 */
export const ROOT = 'memco'

/** Level used when `MEMCO_LOG` names none. */
export const DEFAULT_LEVEL = 'info'

const CRITICAL = 50
const ERROR = 40
const WARNING = 30
const INFO = 20
const DEBUG = 10

/**
 * Above every real level, so nothing gets through.
 *
 * Silence is a level rather than a flag so that one comparison decides whether
 * a record is written, and so `none` cannot be half-applied.
 */
const SILENT = 60

/** The name that means silence. */
const SILENCE = 'none'

// No aliases. `warn`, `fatal`, `trace` and `off` all name a level somewhere
// else, and accepting them would make MEMCO_LOG mean something subtly
// different depending on which SDK read it.
const LEVELS = new Map<string, number>([
  ['critical', CRITICAL],
  ['error', ERROR],
  ['warning', WARNING],
  ['info', INFO],
  ['debug', DEBUG]
])

let currentLevel = INFO

/**
 * What one area of the SDK writes its records through.
 *
 * The five methods are the five levels; there is no `log(level, ...)`, because
 * a level chosen at runtime is not something this SDK ever needs to do.
 *
 * Each takes a `node:util` format string and its arguments, and formats only
 * once the level has let the record through — so a silenced SDK costs nothing
 * beyond the comparison. `util.format` has no precision specifier, so a record
 * that wants a rounded number has to round its argument before passing it in.
 */
export interface Logger {
  /** The service failed in a way that ends the client's usefulness. */
  critical(message: string, ...args: unknown[]): void
  /** Something the caller asked for did not happen. */
  error(message: string, ...args: unknown[]): void
  /** Something worked but will not keep working — a deprecation, a trim. */
  warning(message: string, ...args: unknown[]): void
  /** The lifecycle a caller would want in a production log: connect, close. */
  info(message: string, ...args: unknown[]): void
  /** Everything else: each RPC, its duration, and where settings came from. */
  debug(message: string, ...args: unknown[]): void
}

function write(
  level: number,
  label: string,
  name: string,
  message: string,
  args: unknown[]
): void {
  if (level < currentLevel) {
    return
  }
  const line = `${new Date().toISOString()} ${name} ${label} ${format(message, ...args)}\n`
  try {
    // `process.stderr` is read here rather than captured when this module
    // loads. A process that daemonizes, redirects or reopens its error stream
    // after importing the SDK would otherwise have every record still going to
    // the original stream, or to a descriptor that has since been closed.
    process.stderr.write(line)
  } catch {
    // Swallowed: a closed or destroyed fd 2 is exactly the situation the line
    // above exists for, and turning it into a throw would make writing a log
    // record able to break the call that logged it.
  }
}

/**
 * The logger for one area of the SDK.
 *
 * @param name The logger's name, `memco.<area>`. It is written verbatim into
 *   every record, so it is what tells a reader which part of the SDK spoke.
 * @returns A logger writing under that name at the level in force when each
 *   record is made — not the level in force now, so a logger may be held in a
 *   module constant and still follow a later {@link setLevel}.
 */
export function getLogger(name: string): Logger {
  return {
    critical: (message, ...args) =>
      write(CRITICAL, 'CRITICAL', name, message, args),
    error: (message, ...args) => write(ERROR, 'ERROR', name, message, args),
    warning: (message, ...args) =>
      write(WARNING, 'WARNING', name, message, args),
    info: (message, ...args) => write(INFO, 'INFO', name, message, args),
    debug: (message, ...args) => write(DEBUG, 'DEBUG', name, message, args)
  }
}

function resolveLevel(level: string): number {
  // setLevel is a public export, so a JavaScript caller can reach it with
  // anything at all. Calling .trim() on a number or null would throw a bare
  // TypeError, which is the one thing this SDK promises never to raise.
  if (typeof level !== 'string') {
    throw new MemcoConfigError(
      `${JSON.stringify(level) ?? String(level)} is not a log level; ` +
        `use one of ${[...LEVELS.keys()].sort().join(', ')}, ` +
        `or ${SILENCE} to silence`
    )
  }
  const name = level.trim().toLowerCase()
  if (name === SILENCE) {
    return SILENT
  }
  const resolved = LEVELS.get(name)
  if (resolved === undefined) {
    // Listed alphabetically rather than in severity order: a caller reading
    // this is looking up a name they got wrong, not choosing how loud to be.
    throw new MemcoConfigError(
      `${JSON.stringify(level)} is not a log level; use one of ` +
        `${[...LEVELS.keys()].sort().join(', ')}, or ${SILENCE} to silence`
    )
  }
  return resolved
}

/**
 * Set the level every SDK logger writes at.
 *
 * Process-wide. This is what a client's `logLevel` argument goes through, and
 * it raises rather than warns: an argument is the caller's own code, unlike an
 * ambient `MEMCO_LOG`.
 *
 * @param level One of `critical`, `error`, `warning`, `info`, `debug`, or
 *   `none` to silence the SDK. Case and surrounding whitespace are ignored.
 * @throws MemcoConfigError If the level is not one of those names.
 */
export function setLevel(level: string): void {
  currentLevel = resolveLevel(level)
}

/**
 * Apply `MEMCO_LOG`, or the default level when it names none.
 *
 * Called once when the package is loaded. Safe to call again — it only assigns
 * the level, so repeated calls cannot duplicate output.
 *
 * An unusable value warns and falls back rather than throwing. This runs during
 * `import`, and an environment variable is ambient: a typo in a debugging knob
 * must never break a program, and must never leave the SDK louder or quieter
 * than it would have been without the variable at all.
 *
 * @param env Environment to read from. Defaults to `process.env`; passing one
 *   is mainly useful in tests.
 */
export function configureLogging(
  env: Readonly<Record<string, string | undefined>> = process.env
): void {
  const setting = (env[LOG_ENV] ?? '').trim().toLowerCase()
  if (setting === '') {
    setLevel(DEFAULT_LEVEL)
    return
  }
  try {
    setLevel(setting)
  } catch (error) {
    if (!(error instanceof MemcoConfigError)) {
      throw error
    }
    process.emitWarning(`${LOG_ENV} ignored: ${error.message}`)
    setLevel(DEFAULT_LEVEL)
  }
}
