/**
 * The memory operations as agent tools: descriptions, schemas, rendered results.
 *
 * Handing these operations to an LLM takes three things beyond the calls
 * themselves — text telling a model what a tool does and what to pass it, a
 * JSON Schema for its arguments, and results rendered as text a model can read.
 * None of that is specific to an agent framework, and all of it is the SDK's to
 * get right: the descriptions are the service's copy, the schemas are this
 * package's own types, and a caller who writes their own rendering has to keep
 * it in step with every field the contract adds.
 *
 * So it lives here, reached from the session the tools are bound to, and wiring
 * it into a framework is the only work left:
 *
 * ```ts
 * await using session = await client.memory.withSession('coding')
 * for (const tool of session.tools()) {
 *   tool.name, tool.description, tool.parameters, tool.call
 * }
 * ```
 *
 * Each tool is bound to the session it was built from, so nothing a model sends
 * can change which session a call is recorded under. What a model sends is
 * untrusted: arguments are checked against the same shapes the schema was built
 * from, and a bad one is reported the way a bad request is.
 *
 * ## Where the descriptions and schemas come from
 *
 * Neither can be derived from the operations themselves: TypeScript keeps no
 * doc comments and no parameter types at run time, so there is nothing to read
 * back off a method. Both halves are stated instead:
 *
 * - The descriptions come from `src/gen/toolCopy.ts`, compiled at build time
 *   from the service's own tool manifest. Nothing here hand-writes copy the
 *   manifest carries.
 * - The schemas come from {@link OPERATIONS} below: one entry per offered
 *   operation, naming its arguments, their shapes, and which are required.
 *   `tests/agent.test.ts` fails if that table and the generated copy stop
 *   describing the same set of parameters — the check a derived schema would
 *   get for free, and the price of declaring one.
 * - The copy for `tags` and `feedback` is hand-written here, because the
 *   manifest describes them as XML strings while this SDK takes {@link Tag} and
 *   {@link FeedbackRating} objects. `SDK_SHAPED` names those two and `source`
 *   as the parameters the generator would not copy — though `source` is there
 *   for a different reason: it is in `BOUND`, so a model is never offered it at
 *   all and there is nothing for copy to describe.
 *
 * Four details of the surface follow from the language rather than from a
 * choice made here:
 *
 * - Every call is a promise, so {@link Tool.call} and {@link Toolset.call} are
 *   `async` and {@link Toolset.toLangChain} is too — a lazy `import()` cannot
 *   be anything else.
 * - {@link render} dispatches on the fields a result carries rather than on its
 *   class. These results are interfaces, which have no run-time identity.
 * - A wrong argument is reported with the type JavaScript would call it —
 *   `not string`, `not number`, and `not array` where `typeof` would only have
 *   said `object` — and a rendered rating reads `relevant=true`.
 * - {@link briefing} names the sentinel argument `memoryIdx`, not `memory_idx`.
 *   It is telling a model what to send, and this SDK's schema spells it that
 *   way. Prose the service wrote keeps the service's spelling.
 */

import {
  MemcoConfigError,
  MemcoInvalidRequestError,
  MemcoNotFoundError
} from './errors.js'
import {
  ANSWERED,
  OFFERED,
  TOOL_COPY,
  TOOL_PREFIX,
  type ToolCopy
} from './gen/toolCopy.js'
import { NEW_MEMORY, reject } from './internal/validate.js'
import type { SessionScope } from './operations.js'
import {
  RevertOutcome,
  type DomainEntry,
  type FeedbackRating,
  type FeedbackResult,
  type Instructions,
  type Memory,
  type RevertResult,
  type SearchResult,
  type Tag,
  type WriteResult
} from './types.js'

/**
 * The failures a model can act on, and the only ones a tool reports as text.
 *
 * A malformed request it can correct, and a handle that resolves to nothing it
 * can look for elsewhere. Everything else — a rejected or unscoped credential,
 * a timeout, an unreachable service, a spent quota — is thrown, because no
 * wording a model reads will fix it and letting it read the failure only
 * invites it to try again.
 */
export const AGENT_RECOVERABLE = [
  MemcoInvalidRequestError,
  MemcoNotFoundError
] as const

/**
 * The parameters the caller supplies, never the model.
 *
 * `sessionId` and `domain` are bound because the scope supplies both: a model
 * that could name its own session would defeat the point of binding one.
 * `source` is bound because it records who produced the content, and a model
 * offered the choice could claim the content came from a person — the same
 * misattribution the contract already refuses for the operator source. The
 * SDK's default says agent, which is what is true here. `timeout` is the
 * caller's deadline, not something a model has any basis to set.
 */
export const BOUND = ['timeout', 'source', 'sessionId', 'domain'] as const

/**
 * One JSON Schema fragment.
 *
 * Deliberately loose. This is the shape handed to whatever framework the caller
 * uses, several of which normalise it in place, so pinning it to a stricter
 * type would only be a claim this package cannot keep.
 */
export type JsonSchema = Record<string, unknown>

/**
 * A JSON Schema object describing one tool's arguments.
 *
 * Plain data, handed to a framework unchanged: {@link Toolset.toAnthropic} puts
 * it under `input_schema`, {@link Toolset.toOpenAI} under
 * `function.parameters`, and {@link Toolset.toLangChain} passes it as `schema`.
 * Reach for it directly only when driving an API none of those cover.
 *
 * A fresh object per {@link SessionScope.tools} call, so a framework that
 * normalises one in place is rewriting a copy and not the next caller's.
 */
export interface ToolParameters {
  /** Always `object`: every operation takes named arguments. */
  type: 'object'
  /** One fragment per argument, each carrying a description. */
  properties: Record<string, JsonSchema>
  /** The arguments the operation cannot be called without. */
  required: string[]
}

/**
 * One memory operation, ready to hand to an agent framework.
 *
 * The four fields are the whole handover: {@link Tool.name} and
 * {@link Tool.description} tell a model what this is, {@link Tool.parameters}
 * tells it what to send, and {@link Tool.call} runs it and returns text to hand
 * straight back as the tool result — no rendering left to do.
 *
 * {@link Tool.call} throws every failure except the two in
 * {@link AGENT_RECOVERABLE}, a malformed request and a handle that resolves to
 * nothing, which come back as text for the model to act on. So a rejected
 * credential, a timeout, an unreachable service or a spent quota reaches your
 * code as an exception rather than reaching the model as a suggestion to try
 * again.
 *
 * Reach for these individually when wiring a framework by hand; {@link Toolset}
 * already does it for the frameworks it names.
 *
 * @example
 * ```ts
 * for (const tool of session.tools()) {
 *   console.log(tool.name, tool.description, tool.parameters.required)
 * }
 * ```
 */
export interface Tool {
  /** The name a model calls, such as `memco_search`. */
  readonly name: string
  /** What the operation is for, as a model should read it. */
  readonly description: string
  /**
   * A JSON Schema object describing the arguments, with a description on each.
   *
   * Built fresh per {@link SessionScope.tools} call, so a framework that
   * normalises schemas in place cannot reach through and corrupt another
   * tool's.
   */
  readonly parameters: ToolParameters
  /**
   * Run the operation and render the result as text.
   *
   * @param args The arguments the schema describes. Untrusted.
   * @returns The result as text, or what the model got wrong.
   */
  call(args: Record<string, unknown>): Promise<string>
}

/**
 * One tool as the Anthropic Messages API takes it.
 *
 * A definition only. {@link Toolset.toAnthropic} builds the array to pass as
 * `tools` on a `messages.create` call, and every `tool_use` block that comes
 * back goes to {@link Toolset.call}, whose text is the `content` of the
 * `tool_result` block you send in reply.
 *
 * @example
 * ```ts
 * const toolset = session.tools()
 * const tools: AnthropicTool[] = toolset.toAnthropic()
 * // tools -> anthropic.messages.create({ model, max_tokens, messages, tools })
 *
 * // Each tool_use block carries a name and an input object:
 * async function used(name: string, input: Record<string, unknown>) {
 *   const text = await toolset.call(name, input)
 *   // text -> the content of a tool_result block, back to the model
 * }
 * ```
 */
export interface AnthropicTool {
  /** The name a model calls. */
  name: string
  /** What the operation is for. */
  description: string
  /** The JSON Schema for its arguments. */
  input_schema: ToolParameters
}

/**
 * The body of an {@link OpenAITool}.
 *
 * The same three things {@link AnthropicTool} carries, under the names that API
 * uses — the schema is `parameters` rather than `input_schema`. Built by
 * {@link Toolset.toOpenAI}; there is no reason to construct one by hand.
 */
export interface OpenAIFunction {
  /** The name a model calls. */
  name: string
  /** What the operation is for. */
  description: string
  /** The JSON Schema for its arguments. */
  parameters: ToolParameters
}

/**
 * One tool as the OpenAI Chat Completions API takes it.
 *
 * A definition only. {@link Toolset.toOpenAI} builds the array to pass as
 * `tools`, and every `tool_call` that comes back goes to {@link Toolset.call},
 * whose text becomes the `content` of the `{ role: 'tool' }` message you send
 * in reply.
 *
 * That API delivers `tool_call.function.arguments` as a JSON **string** rather
 * than an object. {@link Toolset.call} takes it as it arrives — it parses a
 * string and reports unparseable text the way it reports any other bad
 * argument, so there is nothing to unwrap first.
 *
 * @example
 * ```ts
 * const toolset = session.tools()
 * const tools: OpenAITool[] = toolset.toOpenAI()
 * // tools -> openai.chat.completions.create({ model, messages, tools })
 *
 * // Each tool_call carries a name and its arguments as JSON text:
 * async function called(name: string, args: string) {
 *   const text = await toolset.call(name, args)
 *   // text -> the content of a { role: 'tool', tool_call_id } message
 * }
 * ```
 */
export interface OpenAITool {
  /** Always `function`. */
  type: 'function'
  /** The operation itself. */
  function: OpenAIFunction
}

/**
 * Every result an offered operation returns, and everything {@link render}
 * takes.
 *
 * Most agent code never names this: {@link Tool.call} and {@link Toolset.call}
 * render what they get before returning it. Reach for it when calling an
 * operation directly and wanting the same text a tool would have produced — for
 * a prompt, a transcript, or a loop of your own.
 *
 * An import's result is not among these — `importMemories` is not one of the
 * offered operations — and {@link render} throws a `TypeError` on anything it
 * does not recognise rather than returning empty text.
 *
 * @example
 * ```ts
 * const result: Rendered = await session.search('how does X work')
 * console.log(render(result))
 * ```
 */
export type Rendered =
  SearchResult | Memory | WriteResult | FeedbackResult | RevertResult

// -- the shapes a model may send ------------------------------------------

/**
 * A scalar a model may send.
 *
 * Spelled as `typeof` reports it, so checking one is a comparison rather than a
 * table lookup — and as JSON Schema spells it, so the fragment needs no
 * translation either.
 */
type Scalar = 'string' | 'boolean'

/** How each scalar is named to the model that got it wrong. */
const SCALAR_NAMES: Readonly<Record<Scalar, string>> = {
  string: 'a string',
  boolean: 'true or false'
}

/** One field of an object a model may send. */
interface Field {
  /** The key it arrives under. */
  readonly name: string
  /** The scalar it must be. */
  readonly type: Scalar
  /** Whether the object is refused without it. */
  readonly required: boolean
  /** What it means, as a model should read it. */
  readonly description: string
}

/** The declared shape of one argument. */
type Shape =
  | { readonly kind: 'scalar'; readonly type: Scalar }
  | { readonly kind: 'list'; readonly type: Scalar }
  | { readonly kind: 'objects'; readonly fields: readonly Field[] }

/** {@link Tag} as a model sends it, described where the wire copy cannot be. */
const TAG_FIELDS: readonly Field[] = [
  {
    name: 'type',
    type: 'string',
    required: true,
    description: 'The tag category, such as language or framework.'
  },
  {
    name: 'value',
    type: 'string',
    required: true,
    description: 'The value within that category, such as typescript.'
  },
  {
    name: 'version',
    type: 'string',
    required: false,
    description:
      'Version of the thing named, where its type carries one. A version on ' +
      'a type that does not carry one is dropped by the service. Most tag ' +
      'types carry no version, so this is usually omitted.'
  }
]

/** {@link FeedbackRating} as a model sends it. */
const FEEDBACK_FIELDS: readonly Field[] = [
  {
    name: 'idx',
    type: 'string',
    required: true,
    description:
      'The idx of the result being rated, copied exactly as it appeared in a ' +
      "search response. An insight's idx rates that insight; a memory's own " +
      'idx rates every insight under it. It cannot be constructed by hand.'
  },
  {
    name: 'relevant',
    type: 'boolean',
    required: true,
    description: 'Whether the result was a good match for the query.'
  },
  {
    name: 'correct',
    type: 'boolean',
    required: true,
    description: 'Whether its content was accurate.'
  },
  {
    name: 'comment',
    type: 'string',
    required: false,
    description: 'An optional note about this result.'
  }
]

const STRING: Shape = { kind: 'scalar', type: 'string' }
const STRINGS: Shape = { kind: 'list', type: 'string' }
const TAGS: Shape = { kind: 'objects', fields: TAG_FIELDS }
const FEEDBACK: Shape = { kind: 'objects', fields: FEEDBACK_FIELDS }

/**
 * The copy for the two parameters the manifest describes in a shape this SDK
 * does not take.
 *
 * The wire form is a list of XML elements; this SDK takes objects and builds a
 * model an object schema from them, so the service's words would describe an
 * encoding the schema refuses. Everything else about them — when to supply
 * them and what the values mean — is the manifest's, kept.
 */
const RESHAPED: Readonly<Record<string, string>> = {
  tags:
    'Tags describing the subject and context, narrowing what this applies to. ' +
    'Each is an object with a type and a value, plus an optional version. ' +
    'Call list_domains for the tag types this domain uses and which of them ' +
    'take a version. Supply as many as you can determine for the best results.',
  feedback:
    '(Required) The ratings, one per search result. Each is an object naming ' +
    'the result being rated, whether it was a good match for the query, ' +
    'whether its content was accurate, and optionally why.'
}
// Neither string above quotes a cap. The manifest's tags copy quotes none
// either; its feedback copy quotes two, and they are dropped here on purpose.
// The service owns those numbers and reports them on a listDomains response, so
// one written into this file would go stale the moment the service changed it
// and would then be telling a model something untrue.

// -- the operations offered -----------------------------------------------

/** One argument a model may send to one operation. */
interface Argument {
  /** The name it arrives under, as this SDK spells it. */
  readonly name: string
  /** The shape it must take. */
  readonly shape: Shape
  /** Whether the operation cannot be called without it. */
  readonly required: boolean
}

/** One offered operation: its arguments, and how to reach it on a session. */
interface Operation {
  /** The manifest's name for it, which is also the key into `TOOL_COPY`. */
  readonly name: (typeof OFFERED)[number]
  /** What a model may send, in the order the schema lists it. */
  readonly arguments: readonly Argument[]
  /**
   * Call it on the session the tools are bound to.
   *
   * The arguments have already been checked against `arguments` above, which
   * is what makes the casts here safe.
   */
  readonly invoke: (
    session: SessionScope,
    args: Record<string, unknown>
  ) => Promise<Rendered>
}

/**
 * Every operation offered to a model, in the order a task uses them.
 *
 * Written out rather than read off the scope. Everything a tool exposes crosses
 * into an untrusted model's reach, and "every public method" would put a helper
 * added to the scope there without anyone deciding to. `tests/agent.test.ts`
 * asserts this covers `OFFERED` exactly, so an operation added to that list and
 * not described here is a failure rather than an omission.
 */
const OPERATIONS: readonly Operation[] = [
  {
    name: 'search',
    arguments: [
      { name: 'query', shape: STRING, required: true },
      { name: 'tags', shape: TAGS, required: false }
    ],
    invoke: (session, args) =>
      session.search(args.query as string, {
        tags: args.tags as readonly Tag[] | undefined
      })
  },
  {
    name: 'get_memory',
    arguments: [{ name: 'idx', shape: STRING, required: true }],
    invoke: (session, args) => session.getMemory(args.idx as string)
  },
  {
    name: 'create_memory',
    arguments: [
      { name: 'query', shape: STRING, required: true },
      { name: 'title', shape: STRING, required: true },
      { name: 'content', shape: STRING, required: true },
      { name: 'tags', shape: TAGS, required: false }
    ],
    invoke: (session, args) =>
      session.createMemory({
        query: args.query as string,
        title: args.title as string,
        content: args.content as string,
        tags: args.tags as readonly Tag[] | undefined
      })
  },
  {
    name: 'enrich_memory',
    arguments: [
      { name: 'memoryIdx', shape: STRING, required: true },
      { name: 'title', shape: STRING, required: true },
      { name: 'content', shape: STRING, required: true },
      { name: 'tags', shape: TAGS, required: false },
      { name: 'sources', shape: STRINGS, required: false }
    ],
    invoke: (session, args) =>
      session.enrichMemory({
        memoryIdx: args.memoryIdx as string,
        title: args.title as string,
        content: args.content as string,
        tags: args.tags as readonly Tag[] | undefined,
        sources: args.sources as readonly string[] | undefined
      })
  },
  {
    name: 'share_feedback',
    arguments: [{ name: 'feedback', shape: FEEDBACK, required: true }],
    invoke: (session, args) =>
      session.shareFeedback({
        feedback: args.feedback as readonly FeedbackRating[]
      })
  },
  {
    name: 'revert_memory',
    arguments: [{ name: 'operationId', shape: STRING, required: true }],
    invoke: (session, args) => session.revertMemory(args.operationId as string)
  }
]

// -- schemas --------------------------------------------------------------

/**
 * Name the description of one argument, from whichever source owns it.
 *
 * `RESHAPED` first, because a parameter this SDK reshaped is one the generator
 * left out of `TOOL_COPY` on purpose.
 */
function describes(operation: Operation, argument: Argument): string {
  // Widened from its literal type so a name can be looked up rather than
  // spelled: the interface declares an index signature, the literal does not.
  const copy: ToolCopy = TOOL_COPY[operation.name]
  return RESHAPED[argument.name] ?? copy.parameters[argument.name]
}

/** Describe one object a model may send as a JSON Schema object. */
function objectSchema(fields: readonly Field[]): JsonSchema {
  const properties: Record<string, JsonSchema> = {}
  for (const field of fields) {
    properties[field.name] = {
      type: field.type,
      description: field.description
    }
  }
  // additionalProperties tells a model exactly what it may send. What arrives
  // anyway is ignored rather than refused: an invented key costs nothing, a
  // rejected call costs a turn. This is a statement of the shape, not a claim
  // to satisfy any vendor's strict mode — the top-level schema makes no such
  // declaration, so neither level would qualify.
  return {
    type: 'object',
    properties,
    required: fields.filter(field => field.required).map(field => field.name),
    additionalProperties: false
  }
}

/** Describe one argument as JSON Schema, carrying its description. */
function argumentSchema(shape: Shape, description: string): JsonSchema {
  if (shape.kind === 'scalar') {
    return { type: shape.type, description }
  }
  const items: JsonSchema =
    shape.kind === 'list' ? { type: shape.type } : objectSchema(shape.fields)
  return { type: 'array', items, description }
}

/** Describe one operation's arguments as a JSON Schema object. */
function parametersFor(operation: Operation): ToolParameters {
  const properties: Record<string, JsonSchema> = {}
  const required: string[] = []
  for (const argument of operation.arguments) {
    properties[argument.name] = argumentSchema(
      argument.shape,
      describes(operation, argument)
    )
    if (argument.required) {
      required.push(argument.name)
    }
  }
  return { type: 'object', properties, required }
}

// -- what a model sent ----------------------------------------------------

/**
 * Name what arrived, for the model that sent it.
 *
 * `typeof` alone calls an array an object and null an object, which tells a
 * model nothing about what it did wrong.
 */
function jsonType(value: unknown): string {
  if (value === null) return 'null'
  if (Array.isArray(value)) return 'array'
  return typeof value
}

/** Whether a value is a JSON object rather than a list, a null or a scalar. */
function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * Check one value against the type the schema promised.
 *
 * A model that sends a number where the schema said string is making the same
 * kind of mistake as one that omits a field, and has to hear about it the same
 * way: unchecked, the wrong type reaches the request builder and ends the run
 * instead of the turn.
 *
 * @param type The scalar the schema declared.
 * @param value What arrived, which is untrusted.
 * @param where The argument or field it arrived under, for the message.
 * @returns The value, unchanged.
 */
function scalar(type: Scalar, value: unknown, where: string): unknown {
  if (typeof value !== type) {
    throw reject(
      `${where} must be ${SCALAR_NAMES[type]}, not ${jsonType(value)}`
    )
  }
  return value
}

/**
 * Turn one object a model sent into the shape the operation takes.
 *
 * @param fields The object's declared fields.
 * @param value What arrived, which is untrusted.
 * @param argument The parameter it arrived under, for the message.
 * @returns The object, with only the declared fields on it.
 */
function objectFrom(
  fields: readonly Field[],
  value: unknown,
  argument: string
): Record<string, unknown> {
  if (!isObject(value)) {
    throw reject(`${argument} must hold objects, not ${jsonType(value)}`)
  }
  // Object.hasOwn, not `in`: `in` walks the prototype chain, so a field named
  // for anything on Object.prototype would read as supplied on every object a
  // model sends.
  const missing = fields
    .filter(field => field.required && !Object.hasOwn(value, field.name))
    .map(field => field.name)
  if (missing.length > 0) {
    throw reject(`${argument} is missing ${missing.join(', ')}`)
  }
  const supplied: Record<string, unknown> = {}
  for (const field of fields) {
    if (!Object.hasOwn(value, field.name)) continue
    const item = value[field.name]
    // An optional field explicitly set to null is the same as leaving it out.
    if (item == null && !field.required) continue
    supplied[field.name] = scalar(field.type, item, `${argument}.${field.name}`)
  }
  return supplied
}

/**
 * Turn one argument a model sent into what the operation takes.
 *
 * @param shape The declared shape.
 * @param value What arrived, which is untrusted.
 * @param argument The parameter name, for the message.
 * @returns The value, converted where the operation takes this package's own
 *   types rather than a scalar.
 */
function coerced(shape: Shape, value: unknown, argument: string): unknown {
  if (shape.kind === 'scalar') {
    return scalar(shape.type, value, argument)
  }
  if (!Array.isArray(value)) {
    throw reject(`${argument} must be a list, not ${jsonType(value)}`)
  }
  if (shape.kind === 'list') {
    return value.map((entry, at) =>
      scalar(shape.type, entry, `${argument}[${at}]`)
    )
  }
  return value.map(entry => objectFrom(shape.fields, entry, argument))
}

/**
 * Convert what a model sent into what the operation takes.
 *
 * The schema says which arguments are required, and that is checked here rather
 * than trusted: a framework handed a plain JSON Schema may not enforce it, and
 * an operation called without one fails in a way that would escape as a crash
 * instead of reaching the model as a correction.
 *
 * @param operation The operation being called.
 * @param args The arguments as they arrived, which are untrusted.
 * @returns The arguments, converted.
 */
function argumentsFor(
  operation: Operation,
  args: Record<string, unknown>
): Record<string, unknown> {
  const declared = new Set(operation.arguments.map(one => one.name))
  const undeclared = Object.keys(args)
    .filter(name => !declared.has(name))
    .sort()
  if (undeclared.length > 0) {
    // Quoted for the same reason the invented-tool-name message is: these are
    // keys the model itself wrote, and they come back in the text it reads
    // next turn. Unescaped, a newline or an ANSI sequence in a key would let
    // it shape the punctuation of a sentence it is about to be handed.
    const named = undeclared.map(name => JSON.stringify(name)).join(', ')
    throw reject(`unknown argument(s): ${named}`)
  }
  // Read through Object.hasOwn for the reason given in objectFrom: what a model
  // did not send must not be answered by Object.prototype.
  const supplied = (name: string): unknown =>
    Object.hasOwn(args, name) ? args[name] : undefined
  const missing = operation.arguments
    .filter(one => one.required && supplied(one.name) == null)
    .map(one => one.name)
  if (missing.length > 0) {
    throw reject(`missing required argument(s): ${missing.join(', ')}`)
  }
  const ready: Record<string, unknown> = {}
  for (const one of operation.arguments) {
    const value = supplied(one.name)
    // An optional argument explicitly set to null is the same as leaving it
    // out. It is dropped rather than passed on, because an options object with
    // an explicit undefined on it reaches the same place a missing key does and
    // nothing downstream has to allow for two spellings.
    if (value == null) continue
    ready[one.name] = coerced(one.shape, value, one.name)
  }
  return ready
}

/**
 * Normalise the arguments a framework handed back from the model.
 *
 * OpenAI delivers `tool_call.function.arguments` as a **JSON string**, so a
 * caller following {@link Toolset.toOpenAI} passes one here; Anthropic delivers
 * an object. Both are accepted, and anything else is reported here rather than
 * read as arguments: `Object.keys` of a string is `0, 1, 2, …`, so a model that
 * sent one would be told its arguments were named after their own positions.
 *
 * Nothing checks that the keys are strings: a JavaScript object's keys already
 * are, and `JSON.parse` produces no others.
 */
function sent(args: Record<string, unknown> | string): Record<string, unknown> {
  let value: unknown = args
  if (typeof value === 'string') {
    try {
      value = JSON.parse(value)
    } catch (error) {
      throw reject(`arguments are not valid JSON: ${(error as Error).message}`)
    }
  }
  if (!isObject(value)) {
    throw reject(`arguments must be an object, not ${jsonType(value)}`)
  }
  return value
}

/** Whether a failure is one of {@link AGENT_RECOVERABLE}. */
function recoverable(
  error: unknown
): error is MemcoInvalidRequestError | MemcoNotFoundError {
  // By name rather than by instanceof: this package publishes an ESM build and
  // a CommonJS build, and a consumer that loads both holds two copies of every
  // class. See the note at the top of `errors.ts`. The cost is that a subclass
  // of either would not match — neither has one, and anyone adding one has to
  // name it here too.
  return (
    error instanceof Error &&
    AGENT_RECOVERABLE.some(one => one.name === error.name)
  )
}

/**
 * Word a recoverable failure for the model that caused it.
 *
 * `detail` rather than `message`: this SDK prefixes the status name onto the
 * message, and a model has no use for `INVALID_ARGUMENT` twice.
 */
function reported(
  error: MemcoInvalidRequestError | MemcoNotFoundError
): string {
  if (error.name === MemcoNotFoundError.name) {
    return `nothing found: ${error.detail}`
  }
  // Not "nothing was recorded": the SDK rejects some of these itself, but a
  // rejection from the service is the service's to describe, and what it did or
  // did not write is not something a client can assert on its behalf.
  return `invalid request: ${error.detail}`
}

// -- the toolset ----------------------------------------------------------

/**
 * The module `toLangChain` reaches for, named indirectly.
 *
 * Typed as `string` rather than left as a literal so the compiler treats the
 * `import()` below as dynamic: LangChain is not a dependency of this package,
 * and a literal specifier would be a missing-module error at build time.
 */
const LANGCHAIN_TOOLS: string = '@langchain/core/tools'

/**
 * One LangChain tool.
 *
 * `any` on purpose. LangChain is not a dependency, so there is no type here to
 * name, and `unknown` would leave the result unusable as
 * `createAgent({ tools })` without a cast at every call site.
 */
type LangChainTool = any

/**
 * Every memory operation as a tool, in the shape a framework wants it.
 *
 * The `to*` methods hand the same tools to a particular framework, and
 * {@link Toolset.call} runs the one a model named — which is what the
 * definition-only shapes need, since there the host dispatches rather than the
 * framework.
 *
 * @example
 * ```ts
 * const toolset = session.tools()
 *
 * // The tools go to the provider as plain data.
 * const response = await anthropic.messages.create({
 *   model,
 *   max_tokens: 1024,
 *   messages,
 *   tools: toolset.toAnthropic()
 * })
 *
 * // Hand a tool call straight back. Only a malformed request or a handle
 * // nothing answers to comes back as text; anything else throws.
 * const text = await toolset.call(block.name, block.input)
 * ```
 */
export class Toolset {
  /** The tools, in the order a task uses them. */
  readonly tools: readonly Tool[]

  /**
   * @param tools The tools this set holds.
   *
   * @internal Built by {@link SessionScope.tools}, never by a caller.
   */
  constructor(tools: readonly Tool[]) {
    this.tools = tools
  }

  /** Iterate the tools, so a toolset stands in for the array it holds. */
  [Symbol.iterator](): Iterator<Tool> {
    return this.tools[Symbol.iterator]()
  }

  /**
   * Describe these tools for the Anthropic Messages API.
   *
   * Definitions only — pair them with {@link Toolset.call} to run what the
   * model asks for.
   *
   * @returns One tool definition per operation.
   */
  toAnthropic(): AnthropicTool[] {
    return this.tools.map(tool => ({
      name: tool.name,
      description: tool.description,
      input_schema: tool.parameters
    }))
  }

  /**
   * Describe these tools for the OpenAI Chat Completions API.
   *
   * Definitions only — pair them with {@link Toolset.call} to run what the
   * model asks for, which also takes the JSON string that API hands back.
   * Anything speaking the same shape takes these too; the Responses API wants a
   * flatter one and is not what this emits.
   *
   * @returns One tool definition per operation.
   */
  toOpenAI(): OpenAITool[] {
    return this.tools.map(tool => ({
      type: 'function' as const,
      function: {
        name: tool.name,
        description: tool.description,
        parameters: tool.parameters
      }
    }))
  }

  /**
   * Hand these tools to LangChain.
   *
   * `@langchain/core` takes a JSON Schema wherever it takes a Zod schema — the
   * `tool()` overload set names `JsonSchema7Type` beside the Zod ones — so the
   * schema is passed through unchanged and this package needs no Zod
   * dependency to build one.
   *
   * @returns One LangChain tool per operation, ready for
   *   `createAgent({ tools })`.
   * @throws MemcoConfigError If LangChain is not installed.
   *
   * @example
   * ```ts
   * createAgent({ model, tools: await session.tools().toLangChain() })
   * ```
   */
  async toLangChain(): Promise<LangChainTool[]> {
    // Imported here on purpose: LangChain is not a dependency of this SDK, and
    // a caller using another framework must not be made to install it.
    let tool: (body: unknown, fields: unknown) => LangChainTool
    try {
      ;({ tool } = (await import(LANGCHAIN_TOOLS)) as {
        tool: (body: unknown, fields: unknown) => LangChainTool
      })
    } catch (cause) {
      const error = new MemcoConfigError(
        'toLangChain() needs LangChain: npm install @langchain/core'
      )
      error.cause = cause
      throw error
    }
    return this.tools.map(one =>
      tool((args: Record<string, unknown>) => one.call(args), {
        name: one.name,
        description: one.description,
        schema: one.parameters
      })
    )
  }

  /**
   * Run the tool a model named, and render what it returned.
   *
   * @param name The tool name the model used, which is untrusted.
   * @param args The arguments it sent, which are untrusted. An object, or the
   *   JSON text OpenAI hands back.
   * @returns The result as text, or what the model got wrong. Nothing a model
   *   can send reaches the caller as an exception.
   *
   * @example
   * ```ts
   * await toolset.call('memco_search', { query: 'how does X work' })
   * await toolset.call(call.function.name, call.function.arguments) // OpenAI
   * ```
   */
  async call(
    name: string,
    args: Record<string, unknown> | string
  ): Promise<string> {
    try {
      const tool = this.tools.find(one => one.name === name)
      if (tool === undefined) {
        const named = this.tools.map(one => one.name).sort()
        // Quoted through JSON.stringify: the name is a model's own text,
        // and echoing a quote in it unescaped would hand that model a sentence
        // it had written the punctuation of.
        const quoted = JSON.stringify(name)
        throw reject(`no tool named ${quoted}; there is ${named.join(', ')}`)
      }
      return await tool.call(sent(args))
    } catch (error) {
      if (recoverable(error)) return reported(error)
      throw error
    }
  }
}

/**
 * Build every memory operation as a tool bound to one open session.
 *
 * @param session The open session every call through these tools is recorded
 *   under.
 * @returns One tool per offered operation.
 *
 * @internal Reached as {@link SessionScope.tools}.
 */
export function toolset(session: SessionScope): Toolset {
  return new Toolset(
    OPERATIONS.map(operation => ({
      name: TOOL_PREFIX + operation.name,
      description: TOOL_COPY[operation.name].description,
      parameters: parametersFor(operation),
      call: async (args: Record<string, unknown>): Promise<string> => {
        try {
          return render(
            await operation.invoke(session, argumentsFor(operation, args))
          )
        } catch (error) {
          if (recoverable(error)) return reported(error)
          throw error
        }
      }
    }))
  )
}

// -- rendering ------------------------------------------------------------

/** Join rendered sections, dropping the ones that carried nothing. */
function joined(...parts: string[]): string {
  return parts.filter(part => part).join('\n\n')
}

/** Render the guidance a response carried. */
function renderedInstructions(instructions: Instructions): string {
  return joined(
    instructions.content,
    instructions.policy,
    instructions.adding,
    instructions.rating,
    instructions.next
  )
}

/** Render one memory and its insights. */
function renderedMemory(memory: Memory): string {
  if (memory.reference) {
    return (
      `${memory.idx}  already returned in this session as ${memory.reference}; ` +
      `fetch it again with ${TOOL_PREFIX}get_memory`
    )
  }
  let heading = `${memory.idx}  served ${memory.timesServed}x`
  if (memory.kind) {
    heading += `  (${memory.kind})`
  }
  const lines = [heading]
  if (memory.intents.length > 0) {
    lines.push('  retrieved before by: ' + memory.intents.join('; '))
  }
  for (const insight of memory.insights) {
    // endorsed and disputed are the reliability signal timesServed is not, so a
    // model that never sees them cannot discount a disputed insight.
    const marks = [`served ${insight.timesServed}x`]
    if (insight.endorsed) marks.push(`endorsed ${insight.endorsed}x`)
    if (insight.disputed) marks.push(`disputed ${insight.disputed}x`)
    const written = insight.updated
      ? `updated ${insight.updated}`
      : 'never updated'
    lines.push(
      `  ${insight.idx}  ${insight.title}\n` +
        `    [${written}, ${marks.join(', ')}]\n` +
        `    ${insight.content}`
    )
  }
  return lines.join('\n')
}

/** Render what a search selected. */
function renderedSearch(result: SearchResult): string {
  const found = result.memories.length
  return joined(
    joined(
      `${found} ${found === 1 ? 'memory' : 'memories'}`,
      result.notice ?? ''
    ),
    result.memories.map(renderedMemory).join('\n\n'),
    renderedInstructions(result.instructions)
  )
}

/** Render an accepted write. */
function renderedWrite(result: WriteResult): string {
  return joined(
    result.operationId
      ? `accepted as operation ${result.operationId}`
      : 'accepted; this write cannot be undone',
    renderedInstructions(result.instructions)
  )
}

/** Render what ratings were recorded. */
function renderedFeedback(result: FeedbackResult): string {
  return joined(
    result.entries
      .map(
        entry =>
          `${entry.idx}  relevant=${entry.relevant} correct=${entry.correct}` +
          (entry.advice ? `  ${entry.advice}` : '')
      )
      .join('\n'),
    renderedInstructions(result.instructions)
  )
}

/** Render what a revert removed. */
function renderedRevert(result: RevertResult): string {
  const outcome = RevertOutcome[result.outcome].toLowerCase().replace(/_/g, ' ')
  return joined(
    `${result.operationId}: ${outcome}`,
    renderedInstructions(result.instructions)
  )
}

/**
 * Render an operation's result as text a model can read.
 *
 * Every result carries {@link Instructions} — the service's own guidance on
 * what to do with what came back — and that is included, because a model that
 * never sees it is left to guess.
 *
 * These results are interfaces, which have no run-time identity, so the
 * dispatch here is on the field only one of them carries. `outcome` is tested
 * before `operationId` because a revert carries both.
 *
 * @param value Any result a memory operation returns.
 * @returns The result as text.
 * @throws TypeError If the value is not a result this renders. The parameter
 *   type stops a typed caller reaching this; the guard is for the untyped
 *   ones, which is most agent code.
 *
 * @example
 * ```ts
 * console.log(render(await session.search('how does X work')))
 * ```
 */
export function render(value: Rendered): string {
  const held: Record<string, unknown> = isObject(value) ? value : {}
  if (Array.isArray(held.memories)) {
    return renderedSearch(value as SearchResult)
  }
  if (Array.isArray(held.insights)) {
    return renderedMemory(value as Memory)
  }
  if (Array.isArray(held.entries)) {
    return renderedFeedback(value as FeedbackResult)
  }
  if (typeof held.outcome === 'number') {
    return renderedRevert(value as RevertResult)
  }
  if (Object.hasOwn(held, 'operationId')) {
    return renderedWrite(value as WriteResult)
  }
  throw new TypeError(`no rendering for ${jsonType(value)}`)
}

/**
 * Render what the service says about a domain, for a model to be told.
 *
 * The substance of this comes from `listDomains` and from opening the session —
 * what the domain holds, when to draw on it, what belongs in it, and the tag
 * vocabulary it uses. What this adds is the connective prose around them, and
 * the sentence naming the two operations a bound session has already answered. Supply it up front rather than leaving a model to ask:
 * guidance behind a tool only steers the models that reach for it.
 *
 * @param domain The domain the session was opened in, from `listDomains`.
 * @param instructions What the service said when the session was opened, as
 *   carried by the scope's `instructions`.
 * @returns The guidance as text, ready to be a system prompt or part of one.
 *
 * @example
 * ```ts
 * const { domains } = await client.memory.listDomains()
 * const entry = domains.find(one => one.slug === 'coding')!
 * await using session = await client.memory.withSession('coding')
 * console.log(briefing(entry, session.instructions))
 * ```
 */
export function briefing(
  domain: DomainEntry,
  instructions: Instructions
): string {
  const parts = [
    `You share a persistent memory with your team, in the '${domain.slug}' ` +
      `domain (${domain.title}). Search it before working anything out from ` +
      `scratch, and save what you learn so nobody has to learn it twice.`,
    domain.summary,
    domain.whenToSearch ? `When to search: ${domain.whenToSearch}` : '',
    domain.whenToSave ? `When to save: ${domain.whenToSave}` : '',
    domain.whatNotToSave ? `What not to save: ${domain.whatNotToSave}` : '',
    domain.tagsDescription
  ]
  if (domain.filterTagTypes.length > 0) {
    parts.push(
      'These tag types narrow the results rather than boosting them, so a ' +
        `wrong one returns nothing: ${domain.filterTagTypes.join(', ')}.`
    )
  }
  if (domain.versionTagTypes.length > 0) {
    parts.push(
      'These carry a version, and one on any other type is dropped: ' +
        `${domain.versionTagTypes.join(', ')}.`
    )
  }
  if (domain.maxTagsPerQuery) {
    parts.push(`At most ${domain.maxTagsPerQuery} tags per call.`)
  }
  parts.push(
    `The domain and the session are already chosen, so ${ANSWERED.join(' and ')} ` +
      "are not among your tools: where a tool's description names one, what it " +
      'would have told you is above.'
  )
  parts.push(
    `Pass '${NEW_MEMORY}' as memoryIdx to ${TOOL_PREFIX}enrich_memory to open ` +
      'a new memory.'
  )
  parts.push(instructions.content)
  return joined(...parts)
}
