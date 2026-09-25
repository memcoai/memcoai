/**
 * Node.js SDK for Memco Shared Memory.
 *
 * Memco Shared Memory is a persistent, searchable memory that a team and its
 * agents share. This package is the typed client for it.
 *
 * Configuration is read from the environment unless it is passed explicitly:
 *
 * | Variable | What it sets |
 * |---|---|
 * | `MEMCO_API_TOKEN` | the credential, unless an API client's are set. |
 * | `MEMCO_CLIENT_ID`, `MEMCO_CLIENT_SECRET` | an API client's credentials, exchanged for a token. Set together, they win over `MEMCO_API_TOKEN`. |
 * | `MEMCO_API_HOST` | the endpoint. Defaults to `grpc.memco.ai:443`. |
 * | `MEMCO_API_TLS` | `false` to dial a plaintext endpoint, such as a local server. Defaults to `true`. |
 * | `MEMCO_LOG` | the log level: `critical`, `error`, `warning`, `info`, `debug`, or `none` to silence. Defaults to `info`. |
 *
 * `MEMCO_API_KEY` is still read as a fallback for `MEMCO_API_TOKEN`, and warns
 * once when it is what supplied the credential.
 *
 * No credential — token, client secret, or the key a session acting for one
 * of your users holds — is ever written to a log record.
 *
 * @packageDocumentation
 */

import { configureLogging } from './internal/logging.js'

// Applied when the package loads, so MEMCO_LOG is in force before a caller has
// a chance to construct anything.
configureLogging()

export { Memco, type MemcoOptions } from './client.js'

export {
  MemoryOperations,
  Session,
  SessionOpener,
  type SessionOptions,
  type CreateMemoryOptions,
  type EnrichMemoryOptions,
  type ImportMemoriesOptions,
  type ScopedCreateMemoryOptions,
  type ScopedEnrichMemoryOptions,
  type ScopedSearchOptions,
  type ScopedShareFeedbackOptions,
  type SearchOptions,
  type ShareFeedbackOptions,
  type TimeoutOptions
} from './operations.js'

export {
  NetworkOperations,
  UserOperations,
  type AddMemberOptions,
  type CreateKeyOptions,
  type CreateNetworkOptions,
  type CreateUserOptions,
  type DeleteKeyOptions,
  type ListGroupsOptions,
  type ListMembersOptions,
  type ListNetworksOptions,
  type ListUsersOptions,
  type NetworkGroupOptions,
  type RemoveMemberOptions,
  type UpdateNetworkOptions,
  type UpdateUserOptions
} from './administration.js'

export {
  DEFAULT_HOST,
  DEFAULT_PORT,
  DEFAULT_TIMEOUT
} from './internal/config.js'

export { LOG_ENV, setLevel } from './internal/logging.js'

/**
 * The name `process.emitWarning` tags a deprecation notice from the service
 * with, so an application can filter on it without hard-coding the string.
 */
export { DEPRECATION_WARNING_NAME } from './internal/deprecation.js'

/**
 * Pass as `memoryIdx` to {@link MemoryOperations.enrichMemory} to open a new
 * memory rather than extend one a search returned.
 */
export { NEW_MEMORY } from './internal/validate.js'

export {
  MemcoAPIError,
  MemcoAlreadyExistsError,
  MemcoAuthenticationError,
  MemcoConfigError,
  MemcoError,
  MemcoExternalUserNeedsCustomerNetworkError,
  MemcoInternalError,
  MemcoInvalidRequestError,
  MemcoNotFoundError,
  MemcoPermissionError,
  MemcoPreconditionFailedError,
  MemcoResourceExhaustedError,
  MemcoSunsetError,
  MemcoTimeoutError,
  MemcoUnavailableError,
  MemcoUnhealthyError,
  MemcoUserAlreadyAssignedNetworkError,
  ResourceExhaustedKind,
  SunsetKind,
  fromServiceError
} from './errors.js'

export {
  CreatedKey,
  DataSource,
  ImportStatus,
  RevertOutcome,
  dataSourceFromWire,
  importStatusFromWire,
  revertOutcomeFromWire
} from './types.js'

export type {
  DeletedNetwork,
  DomainEntry,
  DomainList,
  ExternalUser,
  ExternalUserKey,
  ExternalUserList,
  FeedbackEntry,
  FeedbackRating,
  FeedbackResult,
  Group,
  GroupList,
  ImportOutcome,
  ImportResult,
  ImportedInsight,
  ImportedMemory,
  Insight,
  Instructions,
  Limits,
  Member,
  MemberList,
  MemberPlacement,
  Memory,
  MemoryFeedback,
  Network,
  NetworkList,
  ProtoRecord,
  Provenance,
  RevertResult,
  SearchResult,
  Tag,
  ToolDescriptor,
  WriteResult
} from './types.js'

export { provenance } from './internal/provenance.js'

export {
  AGENT_RECOVERABLE,
  Toolset,
  briefing,
  render,
  type AnthropicTool,
  type JsonSchema,
  type OpenAIFunction,
  type OpenAITool,
  type Rendered,
  type Tool,
  type ToolParameters
} from './agent.js'
