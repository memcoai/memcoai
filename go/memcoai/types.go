package memcoai

import (
	"context"
	"fmt"
	"strings"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/fault"
)

// Tag is one selector on a search or a write. The service lowercases each
// field and folds hyphens to underscores, so "Go" and "go" name the same tag.
// Which tag types a domain uses, and which of them narrow results rather than
// boost them, is in its [DomainEntry].
type Tag struct {
	// Type is the tag's category, such as "language". Required.
	Type string
	// Value is the value within that category, such as "go". Required.
	Value string
	// Version is the version of the thing named, where its type carries one.
	// Empty sends no version, which is the common case.
	Version string
}

// Instructions is the service's guidance accompanying a result, already
// written for its domain. A part with nothing to say is empty.
type Instructions struct {
	// Content states what the result is, or what happened.
	Content string
	// Policy explains how a policy result outranks the rest.
	Policy string
	// Adding says how to contribute back to what came back.
	Adding string
	// Rating says how to rate what came back.
	Rating string
	// Next names the follow-up call the result enables.
	Next string
}

// DomainEntry describes one memory domain: what it holds, when to draw on it,
// what belongs in it, and the tag vocabulary it uses.
type DomainEntry struct {
	// Slug is the value a caller passes as a domain.
	Slug string
	// Title is the domain's display name.
	Title string
	// Summary says what the domain holds.
	Summary string
	// WhenToSearch says when to draw on the domain.
	WhenToSearch string
	// WhenToSave says what belongs in it.
	WhenToSave string
	// WhatNotToSave says what does not.
	WhatNotToSave string
	// TagsDescription describes the tag vocabulary.
	TagsDescription string
	// FilterTagTypes are the tag types that narrow results rather than boost
	// them, so a wrong one returns nothing.
	FilterTagTypes []string
	// VersionTagTypes are the tag types that carry a version; a version on any
	// other type is dropped.
	VersionTagTypes []string
	// MaxTagsPerQuery is how many tags a call naming this domain keeps; the SDK
	// trims the rest, as the service would. Zero means no cap.
	MaxTagsPerQuery int
}

// Limits are the caps the service enforces, reported by
// [MemoryOperations.ListDomains]. Once they are known, the SDK refuses a call
// that would exceed one before sending it. Zero means the service reported no
// cap.
type Limits struct {
	// MaxQueryCharacters bounds a search or memory query, in characters.
	MaxQueryCharacters int
	// MaxTextCharacters bounds a title and its content together.
	MaxTextCharacters int
	// MaxIdxCharacters bounds every handle: idx, memory idx, source, operation id.
	MaxIdxCharacters int
	// MaxSources is how many sources an enrichment keeps; the SDK trims the rest.
	MaxSources int
	// MaxFeedbackEntries bounds the ratings in one call.
	MaxFeedbackEntries int
	// MaxImportMemories is how many memories one import call carries; the SDK
	// splits a larger batch into several calls.
	MaxImportMemories int
	// MaxImportQueriesPerMemory bounds one imported memory's queries.
	MaxImportQueriesPerMemory int
	// MaxImportInsightsPerMemory bounds one imported memory's insights.
	MaxImportInsightsPerMemory int
	// MaxImportTagsPerMemory bounds one imported memory's tags.
	MaxImportTagsPerMemory int
}

// DomainList is what [MemoryOperations.ListDomains] returns.
type DomainList struct {
	// Domains are the domains the credential may name.
	Domains []DomainEntry
	// Instructions is the service's guidance on choosing a domain.
	Instructions Instructions
	// Limits are the caps the service enforces, or nil when it reported none.
	Limits *Limits
	// Deprecated reports that what the caller uses is superseded. It does not
	// say whether that is this SDK or the API version; DeprecationMessage does.
	Deprecated bool
	// DeprecationMessage is the service's remedy, empty when not deprecated.
	DeprecationMessage string
	// SunsetDate is when what the caller uses stops working, as YYYY-MM-DD, or
	// empty when the service named no date.
	SunsetDate string
	// ServerCommit names the build that answered.
	ServerCommit string
}

// ToolDescriptor is one method the contract declares, and whether the
// credential's role permits it.
type ToolDescriptor struct {
	// Name is the operation's snake_case name, such as "create_memory".
	Name string
	// Description is the service's description of the method.
	Description string
	// Available reports whether the role permits the method. A permitted method
	// can still be refused for another reason.
	Available bool
}

// Insight is one insight held under a memory.
type Insight struct {
	// Idx is the insight's handle; rating it rates this insight alone.
	Idx string
	// Title is the insight's title.
	Title string
	// Content is what the insight says.
	Content string
	// Updated is the day the insight was last written, as YYYY-MM-DD, or empty.
	Updated string
	// TimesServed counts deliveries, not correctness.
	TimesServed int64
	// Endorsed counts ratings that found the insight correct.
	Endorsed int64
	// Disputed counts ratings that found it wrong.
	Disputed int64
}

// Memory is one memory a search or a fetch returned.
type Memory struct {
	// Idx is the memory's handle.
	Idx string
	// Kind is the memory's kind, such as "policy", or empty.
	Kind string
	// TimesServed counts deliveries, this one included.
	TimesServed int64
	// Intents are the questions the memory has been retrieved by, oldest first.
	Intents []string
	// Insights are what the memory holds. Empty when Reference is set.
	Insights []Insight
	// Reference names the idx an earlier search in this session returned the
	// memory under, when it was returned there; [Session.GetMemory] fetches it
	// again.
	Reference string

	ops       *MemoryOperations
	sessionID string
}

// MemoryFeedback is one rating of a memory, given through [Memory.Feedback].
type MemoryFeedback struct {
	// Relevant reports whether the memory was a good match for the query.
	Relevant bool
	// Correct reports whether its content was accurate.
	Correct bool
	// Comment is an optional note about the memory.
	Comment string
}

// Feedback rates this memory under the session that returned it, and returns
// the rating the service recorded.
//
// Parameters:
//   - ctx: bounds the call; see the package documentation on contexts.
//   - feedback: the rating to give.
//
// It returns the recorded rating, with any advice it earned.
//
// Errors: [*InvalidRequestError] when the memory came from no session — one
// built by hand, or fetched with [MemoryOperations.GetMemory]; use
// [Session.GetMemory] instead. [*InternalError] when the service recorded no
// rating. Otherwise as [MemoryOperations.ShareFeedback].
func (m Memory) Feedback(ctx context.Context, feedback MemoryFeedback) (*FeedbackEntry, error) {
	if m.ops == nil {
		return nil, public(fault.Invalid("this memory has no session to record feedback against"))
	}
	result, err := m.ops.ShareFeedback(ctx, m.sessionID, []FeedbackRating{{
		Idx:      m.Idx,
		Relevant: feedback.Relevant,
		Correct:  feedback.Correct,
		Comment:  feedback.Comment,
	}})
	if err != nil {
		return nil, err
	}
	if len(result.Entries) == 0 {
		return nil, public(fault.Internal(fmt.Sprintf("the service recorded no feedback entry for %q", m.Idx)))
	}
	return &result.Entries[0], nil
}

// SearchResult is what a search returned.
type SearchResult struct {
	// SessionID is the session the search was recorded under: the one named,
	// or a new one when the search named only a domain.
	SessionID string
	// Memories are what the search selected, in rendered order.
	Memories []Memory
	// Notice is a remark about the query itself, or empty.
	Notice string
	// Instructions is the service's guidance on the results.
	Instructions Instructions
}

// WriteResult is what an accepted write returned. The write is processed
// asynchronously, so the memory is not searchable straight away.
type WriteResult struct {
	// OperationID undoes the write through [MemoryOperations.RevertMemory], or
	// is empty when the service could not mint one.
	OperationID string
	// Instructions is the service's guidance on the write.
	Instructions Instructions
}

// FeedbackRating is one rating given to a search result.
type FeedbackRating struct {
	// Idx is the result's handle, copied from a search. A memory's idx rates
	// every insight under it. Required.
	Idx string
	// Relevant reports whether the result was a good match for the query.
	Relevant bool
	// Correct reports whether its content was accurate.
	Correct bool
	// Comment is an optional note about the result.
	Comment string
}

// FeedbackEntry is one rating the service recorded.
type FeedbackEntry struct {
	// Idx is the rated result's handle.
	Idx string
	// Relevant is the relevance recorded.
	Relevant bool
	// Correct is the correctness recorded.
	Correct bool
	// Advice is the suggestion this rating earned, or empty.
	Advice string
}

// FeedbackResult is what a rating call returned.
type FeedbackResult struct {
	// SessionID is the session the ratings were recorded under.
	SessionID string
	// Entries are the recorded ratings.
	Entries []FeedbackEntry
	// Instructions is the service's guidance on the ratings.
	Instructions Instructions
}

// RevertResult is what [MemoryOperations.RevertMemory] returned.
type RevertResult struct {
	// OperationID is the operation that was reverted.
	OperationID string
	// Outcome is what the revert removed, or why it removed nothing.
	Outcome RevertOutcome
	// Instructions is the service's guidance on the revert.
	Instructions Instructions
}

// ImportedInsight is one insight of a memory to import.
type ImportedInsight struct {
	// Title is the insight's title. Required.
	Title string
	// Content is what the insight says. Required.
	Content string
}

// ImportedMemory is one memory to import.
type ImportedMemory struct {
	// Queries are what someone would search to find the memory; at least one.
	Queries []string
	// Insights are what the memory holds; at least one.
	Insights []ImportedInsight
	// Tags describe the memory.
	Tags []Tag
}

// ImportOutcome is what became of one imported memory.
type ImportOutcome struct {
	// Index is the memory's position in the batch the caller passed.
	Index int
	// Status is what became of it.
	Status ImportStatus
	// Errors say what was wrong with a memory that was not queued.
	Errors []string
}

// ImportResult is what [MemoryOperations.ImportMemories] returned.
type ImportResult struct {
	// Results hold one outcome per memory, in the order they were passed.
	Results []ImportOutcome
	// Instructions is the service's guidance on the import.
	Instructions Instructions
}

// ProtoRecord is one contract file this SDK was generated from.
type ProtoRecord struct {
	// Path is the file's path in the contract.
	Path string
	// SHA256 is the file's checksum.
	SHA256 string
}

// Provenance names the contract this SDK was generated from, for a bug report.
type Provenance struct {
	// ServerCommit is the service build the contract was exported from.
	ServerCommit string
	// Protos are the contract files.
	Protos []ProtoRecord
}

// DataSource names who produced the content of a write.
type DataSource int32

// The sources a write may name. The zero value is sent as DataSourceAgent.
const (
	DataSourceUnspecified DataSource = 0
	DataSourceUser        DataSource = 1
	DataSourceAgent       DataSource = 2
)

// String names the source as the contract does, without its prefix.
func (s DataSource) String() string {
	return enumName(memoryv1.DataSource_name, "DATA_SOURCE_", "DataSource", int32(s))
}

// RevertOutcome reports what a revert removed. Every outcome is a successful
// call: not found, expired and refused describe a state, not a failure.
type RevertOutcome int32

// The outcomes a revert reports.
const (
	RevertOutcomeUnspecified RevertOutcome = 0
	// RevertOutcomeMemoryRemoved: the insight was its memory's last, so the
	// memory went with it.
	RevertOutcomeMemoryRemoved RevertOutcome = 1
	// RevertOutcomeAdditionRemoved: only the caller's insight was removed.
	RevertOutcomeAdditionRemoved RevertOutcome = 2
	// RevertOutcomeEntryRemoved: the insight was removed, and no memory was
	// involved.
	RevertOutcomeEntryRemoved RevertOutcome = 3
	// RevertOutcomeMerged: the write had been folded into an existing insight;
	// the duplicate was removed.
	RevertOutcomeMerged RevertOutcome = 4
	// RevertOutcomeNotFound: no write carries the id, or it is still being
	// processed.
	RevertOutcomeNotFound RevertOutcome = 5
	// RevertOutcomeExpired: the write is older than the revert window.
	RevertOutcomeExpired RevertOutcome = 6
	// RevertOutcomeRefused: the content is under moderation.
	RevertOutcomeRefused RevertOutcome = 7
)

// String names the outcome as the contract does, without its prefix.
func (o RevertOutcome) String() string {
	return enumName(memoryv1.RevertOutcome_name, "REVERT_OUTCOME_", "RevertOutcome", int32(o))
}

// ImportStatus is what became of one memory of an import.
type ImportStatus int32

// The statuses an imported memory reports.
const (
	ImportStatusUnspecified ImportStatus = 0
	// ImportStatusQueued: accepted and queued for writing.
	ImportStatusQueued ImportStatus = 1
	// ImportStatusRejected: the memory was not usable; its Errors say why.
	ImportStatusRejected ImportStatus = 2
	// ImportStatusError: usable but not queued; sending it again is safe.
	ImportStatusError ImportStatus = 3
	// ImportStatusDuplicate: already in memory, so nothing was written.
	ImportStatusDuplicate ImportStatus = 4
)

// String names the status as the contract does, without its prefix.
func (s ImportStatus) String() string {
	return enumName(memoryv1.ImportStatus_name, "IMPORT_STATUS_", "ImportStatus", int32(s))
}

func enumName(names map[int32]string, prefix, typ string, value int32) string {
	if name, ok := names[value]; ok {
		return strings.TrimPrefix(name, prefix)
	}
	return fmt.Sprintf("%s(%d)", typ, value)
}
