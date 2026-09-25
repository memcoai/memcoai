/**
 * Conversion from generated protobuf messages to the SDK's public types.
 *
 * Kept in one place so the protobuf layer never leaks past the client methods.
 * Every function here takes a generated message and returns a plain object from
 * `src/types.ts`.
 *
 * Three conventions apply throughout:
 *
 * - An empty protobuf string becomes `null` where absence is meaningful — an
 *   un-minted operation id, a missing notice, reference or advice.
 * - An empty {@link Instructions} part stays an empty string, because the
 *   contract documents "nothing to say" as a real state there.
 * - A nested message ts-proto left `undefined` reads as the message's own
 *   defaults, which is what an unset message field means on the wire. The one
 *   exception is `limits`, where absent and all-zero mean opposite things.
 */

import { status } from '@grpc/grpc-js'

import { MemcoInternalError } from '../errors.js'
import {
  CreatedKey,
  importStatusFromWire,
  revertOutcomeFromWire,
  type DeletedNetwork,
  type DomainEntry,
  type ExternalUser,
  type ExternalUserKey,
  type ExternalUserList,
  type GroupList,
  type Member,
  type MemberList,
  type MemberPlacement,
  type Network,
  type NetworkList,
  type DomainList,
  type FeedbackEntry,
  type FeedbackRating,
  type FeedbackResult,
  type ImportOutcome,
  type ImportResult,
  type Insight,
  type Instructions,
  type Limits,
  type Memory,
  type MemoryFeedback,
  type RevertResult,
  type SearchResult,
  type ToolDescriptor,
  type WriteResult
} from '../types.js'
import * as pb from './gen.js'

/**
 * Map an empty protobuf string to `null`.
 *
 * @param value The string as it arrived on the wire.
 * @returns The string, or `null` when it is empty.
 */
function optional(value: string): string | null {
  return value === '' ? null : value
}

/** Days in each month of a non-leap year, indexed from January. */
const MONTH_LENGTHS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

/** `YYYY-MM-DD`, and nothing else. Anchored, with no `m` flag, so a trailing
 * newline does not slip past `$`. */
const ISO_DAY = /^(\d{4})-(\d{2})-(\d{2})$/

/**
 * Whether a proleptic Gregorian year has a 29th of February.
 *
 * @param year The year to test.
 * @returns Whether it is a leap year.
 */
function leapYear(year: number): boolean {
  return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0
}

/**
 * Keep a `YYYY-MM-DD` string when it names a real day, and drop it otherwise.
 *
 * The public types hold a date as the ISO string the service sent rather than
 * as a `Date`, so this validates rather than parses. `new Date(value)` is
 * deliberately not used: it accepts a far wider grammar, and it resolves a
 * date-only string against a time zone, which would land `updated` a day early
 * for every caller west of UTC.
 *
 * @param value The date string, which may be empty or malformed.
 * @returns The string, or `null` when it is empty or is not a real calendar
 *   day. A malformed date is never fatal: the rest of the insight is still
 *   useful.
 */
function toDay(value: string): string | null {
  const parts = ISO_DAY.exec(value)
  if (parts === null) {
    return null
  }
  const year = Number(parts[1])
  const month = Number(parts[2])
  const day = Number(parts[3])
  // Year zero is refused because the calendar has none: the year before 1 CE
  // is 1 BCE.
  if (year < 1 || month < 1 || month > 12) {
    return null
  }
  const length = month === 2 && leapYear(year) ? 29 : MONTH_LENGTHS[month - 1]
  if (day < 1 || day > length) {
    return null
  }
  return value
}

/**
 * Convert an `Instructions` message.
 *
 * @param message The generated message, or `undefined` when the response
 *   carried none. Both yield empty parts, which is the correct representation
 *   here.
 * @returns The public equivalent.
 */
function toInstructions(message: pb.Instructions | undefined): Instructions {
  return {
    content: message?.content ?? '',
    policy: message?.policy ?? '',
    adding: message?.adding ?? '',
    rating: message?.rating ?? '',
    next: message?.next ?? ''
  }
}

/**
 * Convert an `InsightResult` message.
 *
 * @param message The generated message.
 * @returns The public equivalent, with `updated` kept only if it is a real day.
 */
function toInsight(message: pb.InsightResult): Insight {
  return {
    idx: message.idx,
    title: message.title,
    content: message.content,
    updated: toDay(message.updated),
    timesServed: message.timesServed,
    endorsed: message.endorsed,
    disputed: message.disputed
  }
}

/**
 * What {@link feedbackSubmitter} needs to actually submit a rating —
 * {@link MemoryOperations.shareFeedback}, shaped without importing it: this
 * module must stay a pure function of the wire message, and `operations.ts`
 * is where a live namespace to call is built.
 */
export type FeedbackCaller = (options: {
  sessionId: string
  feedback: Iterable<FeedbackRating>
}) => Promise<FeedbackResult>

/** What a {@link Memory.feedback} closure is, once built. */
export type FeedbackSubmitter = (
  idx: string,
  rating: MemoryFeedback
) => Promise<FeedbackEntry>

/**
 * Build the closure {@link Memory.feedback} calls, bound to one session.
 *
 * @param sessionId The session to record a rating under. An empty string is
 *   what a memory fetched by `getMemory` carries — nothing recorded it under a
 *   session, so a rating made through it has nowhere to attach, and the empty
 *   id fails through the SDK's own `checkSessionId` exactly as it would for
 *   any other call.
 * @param call How to actually make the `shareFeedback` request.
 * @returns A function taking one memory's idx and rating, returning the single
 *   entry the service recorded for it.
 */
export function feedbackSubmitter(
  sessionId: string,
  call: FeedbackCaller
): FeedbackSubmitter {
  return async (idx, rating) => {
    const result = await call({
      sessionId,
      feedback: [
        {
          idx,
          relevant: rating.relevant,
          correct: rating.correct,
          comment: rating.comment
        }
      ]
    })
    // Unlike toImportResult's answered[0] (an array the SDK itself built and
    // knows is non-empty), entries comes from the service, and the contract
    // documents no guarantee it holds one entry per rating submitted. Refuse
    // to hand back undefined from a Promise<FeedbackEntry>.
    const entry = result.entries[0]
    if (entry === undefined) {
      throw new MemcoInternalError(
        status.INTERNAL,
        `the service recorded no feedback entry for ${JSON.stringify(idx)}`
      )
    }
    return entry
  }
}

/**
 * Convert a `MemoryResult` message.
 *
 * @param message The generated message.
 * @param submit The closure this memory's `feedback()` calls.
 * @returns The public equivalent, with nested insights converted and an empty
 *   reference mapped to `null`.
 */
export function toMemory(
  message: pb.MemoryResult,
  submit: FeedbackSubmitter
): Memory {
  return {
    idx: message.idx,
    kind: message.kind,
    timesServed: message.timesServed,
    intents: [...message.intents],
    insights: message.insights.map(toInsight),
    reference: optional(message.reference),
    feedback: rating => submit(message.idx, rating)
  }
}

/**
 * Convert a `DomainEntry` message.
 *
 * @param message The generated message.
 * @returns The public equivalent, with repeated fields copied rather than
 *   aliased, so a caller holding the result cannot see it change.
 */
function toDomainEntry(message: pb.DomainEntry): DomainEntry {
  return {
    slug: message.slug,
    title: message.title,
    summary: message.summary,
    whenToSearch: message.whenToSearch,
    whenToSave: message.whenToSave,
    whatNotToSave: message.whatNotToSave,
    tagsDescription: message.tagsDescription,
    filterTagTypes: [...message.filterTagTypes],
    versionTagTypes: [...message.versionTagTypes],
    maxTagsPerQuery: message.maxTagsPerQuery
  }
}

/**
 * Convert the limits a response carries, if it carries any.
 *
 * @param message The generated response.
 * @returns The limits, or `null` when the service reported none. `null` means
 *   "validate nothing": reading an absent message as zeros would reject every
 *   call before it was sent.
 */
function toLimits(message: pb.ListDomainsResponse): Limits | null {
  const limits = message.limits
  if (limits === undefined) {
    return null
  }
  return {
    maxQueryCharacters: limits.maxQueryCharacters,
    maxTextCharacters: limits.maxTextCharacters,
    maxIdxCharacters: limits.maxIdxCharacters,
    maxSources: limits.maxSources,
    maxFeedbackEntries: limits.maxFeedbackEntries,
    maxImportMemories: limits.maxImportMemories,
    maxImportQueriesPerMemory: limits.maxImportQueriesPerMemory,
    maxImportInsightsPerMemory: limits.maxImportInsightsPerMemory,
    maxImportTagsPerMemory: limits.maxImportTagsPerMemory
  }
}

/**
 * Convert a `ListDomainsResponse`.
 *
 * @param message The generated response.
 * @returns The public equivalent.
 */
export function toDomainList(message: pb.ListDomainsResponse): DomainList {
  return {
    domains: message.domains.map(toDomainEntry),
    instructions: toInstructions(message.instructions),
    limits: toLimits(message),
    deprecated: message.deprecated,
    deprecationMessage: message.deprecationMessage,
    sunsetDate: toDay(message.sunsetDate),
    serverCommit: message.serverCommit
  }
}

/**
 * Convert a `ToolDescriptor` message.
 *
 * @param message The generated message.
 * @returns The public equivalent.
 */
function toToolDescriptor(message: pb.ToolDescriptor): ToolDescriptor {
  return {
    name: message.name,
    description: message.description,
    available: message.available
  }
}

/**
 * Convert a `ListToolsResponse`.
 *
 * @param message The generated response.
 * @returns One descriptor per method the contract declares.
 */
export function toToolList(
  message: pb.ListToolsResponse
): readonly ToolDescriptor[] {
  return message.tools.map(toToolDescriptor)
}

/** The fields decoded off a `StartSessionResponse`, before `operations.ts` wraps them in a `Session`. */
export interface SessionFields {
  readonly id: string
  readonly instructions: Instructions
}

/**
 * Convert a `StartSessionResponse`.
 *
 * Returns the fields rather than a {@link Session}: that class holds a live
 * reference to the namespace that opened it, and this module must stay a pure
 * function of the wire message — `operations.ts` is where the rich object is
 * assembled.
 *
 * @param message The generated response.
 * @returns The session id, and the guidance that came with opening it.
 */
export function toSessionFields(
  message: pb.StartSessionResponse
): SessionFields {
  return {
    id: message.sessionId,
    instructions: toInstructions(message.instructions)
  }
}

/**
 * Convert a `SearchResponse`.
 *
 * @param message The generated response.
 * @param shareFeedback How to submit a rating for one of this result's
 *   memories, bound to this response's own session id and shared by every
 *   memory in it.
 * @returns The public equivalent, with an empty notice mapped to `null`.
 */
export function toSearchResult(
  message: pb.SearchResponse,
  shareFeedback: FeedbackCaller
): SearchResult {
  const submit = feedbackSubmitter(message.sessionId, shareFeedback)
  return {
    sessionId: message.sessionId,
    memories: message.memories.map(memory => toMemory(memory, submit)),
    notice: optional(message.notice),
    instructions: toInstructions(message.instructions)
  }
}

/**
 * Convert a `CreateMemoryResponse` or an `EnrichMemoryResponse`.
 *
 * The two messages are structurally identical, so one converter serves both.
 *
 * @param message The generated response.
 * @returns The public equivalent, with an empty operation id mapped to `null`
 *   to mark an un-revertible write.
 */
export function toWriteResult(
  message: pb.CreateMemoryResponse | pb.EnrichMemoryResponse
): WriteResult {
  return {
    operationId: optional(message.operationId),
    instructions: toInstructions(message.instructions)
  }
}

/**
 * Convert a `FeedbackEntry` message.
 *
 * @param message The generated message.
 * @returns The public equivalent, with empty advice mapped to `null`.
 */
function toFeedbackEntry(message: pb.FeedbackEntry): FeedbackEntry {
  return {
    idx: message.idx,
    relevant: message.relevant,
    correct: message.correct,
    advice: optional(message.advice)
  }
}

/**
 * Convert a `ShareFeedbackResponse`.
 *
 * @param message The generated response.
 * @returns The public equivalent.
 */
export function toFeedbackResult(
  message: pb.ShareFeedbackResponse
): FeedbackResult {
  return {
    sessionId: message.sessionId,
    entries: message.entries.map(toFeedbackEntry),
    instructions: toInstructions(message.instructions)
  }
}

/**
 * Convert a `RevertMemoryResponse`.
 *
 * @param message The generated response.
 * @returns The public equivalent, with the outcome as a typed value. An outcome
 *   this SDK does not recognise folds to `RevertOutcome.UNSPECIFIED`.
 */
export function toRevertResult(message: pb.RevertMemoryResponse): RevertResult {
  return {
    operationId: optional(message.operationId),
    outcome: revertOutcomeFromWire(message.outcome),
    instructions: toInstructions(message.instructions)
  }
}

/**
 * Convert one entry of an import response.
 *
 * @param message The generated outcome.
 * @param offset The position the group's first memory held in the whole batch.
 * @returns The public equivalent, with the status as a typed value and the
 *   index moved from the group's own numbering to the caller's. A status this
 *   SDK does not recognise folds to `ImportStatus.UNSPECIFIED`.
 */
function toImportOutcome(
  message: pb.ImportOutcome,
  offset: number
): ImportOutcome {
  return {
    index: offset + message.index,
    status: importStatusFromWire(message.status),
    errors: [...message.errors]
  }
}

/**
 * Convert the responses to the calls one import took.
 *
 * A batch above the service's per-call cap is sent as several calls, and each
 * numbers its own results from zero. Re-numbering them against the batch the
 * caller submitted is what keeps {@link ImportOutcome.index} meaning what it
 * says — without it a split batch reports position 0 once per group and
 * identifies nothing.
 *
 * @param answered One `[offset, response]` per call made, in order. Never
 *   empty: an empty batch is refused before any call is built.
 * @returns The public equivalent, one outcome per memory submitted, in the
 *   order they were sent.
 */
export function toImportResult(
  answered: readonly (readonly [number, pb.ImportMemoriesResponse])[]
): ImportResult {
  const results: ImportOutcome[] = []
  for (const [offset, message] of answered) {
    for (const outcome of message.results) {
      results.push(toImportOutcome(outcome, offset))
    }
  }
  return {
    results,
    // The calls are one operation in one domain, so their guidance is the same;
    // the first is as good as any and there is always one.
    instructions: toInstructions(answered[0][1].instructions)
  }
}

/**
 * Convert Unix seconds into a `Date`.
 *
 * @param seconds The instant as it arrived on the wire.
 * @returns The instant, or `null` for zero, which is how the wire says there is
 *   none.
 */
function toInstant(seconds: number): Date | null {
  return seconds === 0 ? null : new Date(seconds * 1000)
}

/**
 * Convert a `Network` message.
 *
 * @param message The generated message.
 * @returns The public equivalent, with an empty parent, scope or owner mapped
 *   to `null`: each is absent rather than blank.
 */
export function toNetwork(message: pb.admin.Network): Network {
  return {
    id: message.id,
    name: message.name,
    parentId: optional(message.parentId),
    domain: message.domain,
    region: message.region,
    scope: optional(message.scope),
    owner: optional(message.owner),
    description: message.description
  }
}

/**
 * Convert a `ListNetworksResponse`.
 *
 * @param message The generated response.
 * @returns The public equivalent.
 */
export function toNetworkList(
  message: pb.admin.ListNetworksResponse
): NetworkList {
  return {
    networks: message.networks.map(toNetwork),
    totalCount: message.totalCount
  }
}

/**
 * Convert a `DeleteNetworkResponse`.
 *
 * @param message The generated response.
 * @returns The public equivalent. The removed counts arrive as a map, whose
 *   order the wire does not keep, so they are keyed in table order to read the
 *   same way every time.
 */
export function toDeletedNetwork(
  message: pb.admin.DeleteNetworkResponse
): DeletedNetwork {
  const tables = Object.entries(message.removed).sort(([a], [b]) =>
    a < b ? -1 : a > b ? 1 : 0
  )
  return { id: message.id, removed: Object.fromEntries(tables) }
}

/**
 * Convert a `Member` message.
 *
 * @param message The generated message.
 * @returns The public equivalent.
 */
function toMember(message: pb.admin.Member): Member {
  return { userId: message.userId, email: message.email, name: message.name }
}

/**
 * Convert a `ListNetworkMembersResponse`.
 *
 * @param message The generated response.
 * @returns The public equivalent.
 */
export function toMemberList(
  message: pb.admin.ListNetworkMembersResponse
): MemberList {
  return {
    members: message.members.map(toMember),
    totalCount: message.totalCount
  }
}

/**
 * Convert an `AddNetworkMemberResponse`.
 *
 * @param message The generated response.
 * @returns The public equivalent, with an empty `movedFrom` mapped to `null`:
 *   nobody was moved.
 */
export function toMemberPlacement(
  message: pb.admin.AddNetworkMemberResponse
): MemberPlacement {
  return {
    networkId: message.id,
    userId: message.userId,
    movedFrom: optional(message.movedFrom)
  }
}

/**
 * Convert a `ListGroupsResponse`.
 *
 * @param message The generated response.
 * @returns The public equivalent, with an empty network mapped to `null`: the
 *   group is assigned to none.
 */
export function toGroupList(message: pb.admin.ListGroupsResponse): GroupList {
  return {
    groups: message.groups.map(group => ({
      id: group.id,
      name: group.name,
      memoryNetworkId: optional(group.memoryNetworkId),
      memberCount: group.memberCount
    })),
    totalCount: message.totalCount
  }
}

/**
 * Convert a `ListGroupMembersResponse`.
 *
 * @param message The generated response.
 * @returns The group's members.
 */
export function toGroupMembers(
  message: pb.admin.ListGroupMembersResponse
): readonly Member[] {
  return message.members.map(toMember)
}

/**
 * Convert an `ExternalUser` message.
 *
 * @param message The generated message.
 * @returns The public equivalent, with the roles copied rather than aliased.
 */
export function toExternalUser(message: pb.admin.ExternalUser): ExternalUser {
  return {
    id: message.id,
    externalId: message.externalId,
    name: message.name,
    email: message.email,
    roles: [...message.roles],
    active: message.active
  }
}

/**
 * Convert a `ListExternalUsersResponse`.
 *
 * @param message The generated response.
 * @returns The public equivalent.
 */
export function toExternalUserList(
  message: pb.admin.ListExternalUsersResponse
): ExternalUserList {
  return {
    externalUsers: message.externalUsers.map(toExternalUser),
    totalCount: message.totalCount
  }
}

/**
 * Convert an `ExternalUserKey` message.
 *
 * @param message The generated message.
 * @returns The public equivalent, with the expiry as a `Date`.
 */
function toExternalUserKey(message: pb.admin.ExternalUserKey): ExternalUserKey {
  return {
    id: message.id,
    name: message.name,
    valuePrefix: message.valuePrefix,
    roles: [...message.roles],
    scopes: [...message.scopes],
    validUntil: toInstant(message.validUntil)
  }
}

/**
 * Convert a `ListExternalUserKeysResponse`.
 *
 * @param message The generated response.
 * @returns The user's keys, described without their values.
 */
export function toExternalUserKeys(
  message: pb.admin.ListExternalUserKeysResponse
): readonly ExternalUserKey[] {
  return message.keys.map(toExternalUserKey)
}

/**
 * Convert a `CreateExternalUserKeyResponse`.
 *
 * @param message The generated response.
 * @returns The key's description, and its value.
 * @throws A {@link MemcoInternalError} if the response carries no key: a value
 *   with nothing describing it would be a key with every field empty, as if it
 *   were real. The value is left out of the message, since it is a working
 *   credential and an error's text is exactly what reaches logs.
 */
export function toCreatedKey(
  message: pb.admin.CreateExternalUserKeyResponse
): CreatedKey {
  if (message.key === undefined) {
    throw new MemcoInternalError(
      status.INTERNAL,
      "the service returned a key value but no key; list the user's keys to " +
        'see whether one was created'
    )
  }
  return new CreatedKey(toExternalUserKey(message.key), message.value)
}
