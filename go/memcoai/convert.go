package memcoai

import (
	"slices"
	"time"

	adminv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/admin/v1"
	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/fault"
	"github.com/memcoai/memcoai/go/internal/memory"
)

// importPart is one group's response and where the group started.
type importPart struct {
	offset   int
	response *memoryv1.ImportMemoriesResponse
}

func toInstructions(m *memoryv1.Instructions) Instructions {
	return Instructions{
		Content: m.GetContent(),
		Policy:  m.GetPolicy(),
		Adding:  m.GetAdding(),
		Rating:  m.GetRating(),
		Next:    m.GetNext(),
	}
}

func toDomainList(m *memoryv1.ListDomainsResponse) *DomainList {
	domains := make([]DomainEntry, 0, len(m.GetDomains()))
	for _, d := range m.GetDomains() {
		domains = append(domains, DomainEntry{
			Slug:            d.GetSlug(),
			Title:           d.GetTitle(),
			Summary:         d.GetSummary(),
			WhenToSearch:    d.GetWhenToSearch(),
			WhenToSave:      d.GetWhenToSave(),
			WhatNotToSave:   d.GetWhatNotToSave(),
			TagsDescription: d.GetTagsDescription(),
			FilterTagTypes:  copied(d.GetFilterTagTypes()),
			VersionTagTypes: copied(d.GetVersionTagTypes()),
			MaxTagsPerQuery: int(d.GetMaxTagsPerQuery()),
		})
	}
	return &DomainList{
		Domains:            domains,
		Instructions:       toInstructions(m.GetInstructions()),
		Limits:             toLimits(m.GetLimits()),
		Deprecated:         m.GetDeprecated(),
		DeprecationMessage: m.GetDeprecationMessage(),
		SunsetDate:         memory.Day(m.GetSunsetDate()),
		ServerCommit:       m.GetServerCommit(),
	}
}

// toLimits keeps absence apart from zeros: a service that reports no limits
// has asked for nothing to be checked.
func toLimits(m *memoryv1.Limits) *Limits {
	if m == nil {
		return nil
	}
	return &Limits{
		MaxQueryCharacters:         int(m.GetMaxQueryCharacters()),
		MaxTextCharacters:          int(m.GetMaxTextCharacters()),
		MaxIdxCharacters:           int(m.GetMaxIdxCharacters()),
		MaxSources:                 int(m.GetMaxSources()),
		MaxFeedbackEntries:         int(m.GetMaxFeedbackEntries()),
		MaxImportMemories:          int(m.GetMaxImportMemories()),
		MaxImportQueriesPerMemory:  int(m.GetMaxImportQueriesPerMemory()),
		MaxImportInsightsPerMemory: int(m.GetMaxImportInsightsPerMemory()),
		MaxImportTagsPerMemory:     int(m.GetMaxImportTagsPerMemory()),
	}
}

func toTools(m *memoryv1.ListToolsResponse) []ToolDescriptor {
	tools := make([]ToolDescriptor, 0, len(m.GetTools()))
	for _, tool := range m.GetTools() {
		tools = append(tools, ToolDescriptor{Name: tool.GetName(), Description: tool.GetDescription(), Available: tool.GetAvailable()})
	}
	return tools
}

func toMemory(m *memoryv1.MemoryResult, ops *MemoryOperations, sessionID string) Memory {
	insights := make([]Insight, 0, len(m.GetInsights()))
	for _, i := range m.GetInsights() {
		insights = append(insights, Insight{
			Idx:         i.GetIdx(),
			Title:       i.GetTitle(),
			Content:     i.GetContent(),
			Updated:     memory.Day(i.GetUpdated()),
			TimesServed: i.GetTimesServed(),
			Endorsed:    i.GetEndorsed(),
			Disputed:    i.GetDisputed(),
		})
	}
	return Memory{
		Idx:         m.GetIdx(),
		Kind:        m.GetKind(),
		TimesServed: m.GetTimesServed(),
		Intents:     copied(m.GetIntents()),
		Insights:    insights,
		Reference:   m.GetReference(),
		ops:         ops,
		sessionID:   sessionID,
	}
}

func toSearchResult(m *memoryv1.SearchResponse, ops *MemoryOperations) *SearchResult {
	memories := make([]Memory, 0, len(m.GetMemories()))
	for _, result := range m.GetMemories() {
		memories = append(memories, toMemory(result, ops, m.GetSessionId()))
	}
	return &SearchResult{
		SessionID:    m.GetSessionId(),
		Memories:     memories,
		Notice:       m.GetNotice(),
		Instructions: toInstructions(m.GetInstructions()),
	}
}

func toWriteResult(operationID string, instructions *memoryv1.Instructions) *WriteResult {
	return &WriteResult{OperationID: operationID, Instructions: toInstructions(instructions)}
}

func toFeedbackResult(m *memoryv1.ShareFeedbackResponse) *FeedbackResult {
	entries := make([]FeedbackEntry, 0, len(m.GetEntries()))
	for _, e := range m.GetEntries() {
		entries = append(entries, FeedbackEntry{Idx: e.GetIdx(), Relevant: e.GetRelevant(), Correct: e.GetCorrect(), Advice: e.GetAdvice()})
	}
	return &FeedbackResult{SessionID: m.GetSessionId(), Entries: entries, Instructions: toInstructions(m.GetInstructions())}
}

func toRevertResult(m *memoryv1.RevertMemoryResponse) *RevertResult {
	return &RevertResult{
		OperationID:  m.GetOperationId(),
		Outcome:      RevertOutcome(memory.FoldRevertOutcome(m.GetOutcome())),
		Instructions: toInstructions(m.GetInstructions()),
	}
}

// toImportResult renumbers each group's outcomes to the caller's positions;
// the instructions are the first group's.
func toImportResult(parts []importPart) *ImportResult {
	result := &ImportResult{Results: []ImportOutcome{}}
	for i, part := range parts {
		if i == 0 {
			result.Instructions = toInstructions(part.response.GetInstructions())
		}
		for _, outcome := range part.response.GetResults() {
			result.Results = append(result.Results, ImportOutcome{
				Index:  part.offset + int(outcome.GetIndex()),
				Status: ImportStatus(memory.FoldImportStatus(outcome.GetStatus())),
				Errors: copied(outcome.GetErrors()),
			})
		}
	}
	return result
}

// optional is the proto3 optional form of s: nil when s is empty.
func optional(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

func wireTags(tags []Tag) []*memoryv1.Tag {
	if tags == nil {
		return nil
	}
	out := make([]*memoryv1.Tag, 0, len(tags))
	for _, tag := range tags {
		out = append(out, &memoryv1.Tag{Type: tag.Type, Value: tag.Value, Version: optional(tag.Version)})
	}
	return out
}

func wireRatings(ratings []FeedbackRating) []*memoryv1.FeedbackRating {
	out := make([]*memoryv1.FeedbackRating, 0, len(ratings))
	for _, rating := range ratings {
		out = append(out, &memoryv1.FeedbackRating{
			Idx:      rating.Idx,
			Relevant: rating.Relevant,
			Correct:  rating.Correct,
			Comment:  optional(rating.Comment),
		})
	}
	return out
}

// wireSource sends an unset source as the agent, which is what the service
// would read it as anyway.
func wireSource(source DataSource) memoryv1.DataSource {
	if source == DataSourceUnspecified {
		return memoryv1.DataSource_DATA_SOURCE_AGENT
	}
	return memoryv1.DataSource(source)
}

func wireMemories(memories []ImportedMemory) []*memoryv1.ImportedMemory {
	out := make([]*memoryv1.ImportedMemory, 0, len(memories))
	for _, m := range memories {
		insights := make([]*memoryv1.ImportedInsight, 0, len(m.Insights))
		for _, insight := range m.Insights {
			insights = append(insights, &memoryv1.ImportedInsight{Title: insight.Title, Content: insight.Content})
		}
		out = append(out, &memoryv1.ImportedMemory{Queries: copied(m.Queries), Insights: insights, Tags: wireTags(m.Tags)})
	}
	return out
}

// copied never shares a slice with a message, and never returns nil.
func copied(values []string) []string {
	return append(make([]string, 0, len(values)), values...)
}

func toNetwork(m *adminv1.Network) Network {
	return Network{
		ID: m.GetId(), Name: m.GetName(), ParentID: m.GetParentId(), Domain: m.GetDomain(),
		Region: m.GetRegion(), Scope: m.GetScope(), Owner: m.GetOwner(), Description: m.GetDescription(),
	}
}

func toNetworkList(m *adminv1.ListNetworksResponse) *NetworkList {
	list := &NetworkList{TotalCount: m.GetTotalCount()}
	for _, network := range m.GetNetworks() {
		list.Networks = append(list.Networks, toNetwork(network))
	}
	return list
}

func toMembers(m []*adminv1.Member) []Member {
	var members []Member
	for _, member := range m {
		members = append(members, Member{UserID: member.GetUserId(), Email: member.GetEmail(), Name: member.GetName()})
	}
	return members
}

func toGroupList(m *adminv1.ListGroupsResponse) *GroupList {
	list := &GroupList{TotalCount: m.GetTotalCount()}
	for _, group := range m.GetGroups() {
		list.Groups = append(list.Groups, Group{
			ID: group.GetId(), Name: group.GetName(), MemoryNetworkID: group.GetMemoryNetworkId(), MemberCount: group.GetMemberCount(),
		})
	}
	return list
}

func toExternalUser(m *adminv1.ExternalUser) ExternalUser {
	return ExternalUser{
		ID: m.GetId(), ExternalID: m.GetExternalId(), Name: m.GetName(), Email: m.GetEmail(),
		Roles: slices.Clone(m.GetRoles()), Active: m.GetActive(),
	}
}

func toExternalUserList(m *adminv1.ListExternalUsersResponse) *ExternalUserList {
	list := &ExternalUserList{TotalCount: m.GetTotalCount()}
	for _, user := range m.GetExternalUsers() {
		list.ExternalUsers = append(list.ExternalUsers, toExternalUser(user))
	}
	return list
}

func toExternalUserKey(m *adminv1.ExternalUserKey) ExternalUserKey {
	key := ExternalUserKey{
		ID: m.GetId(), Name: m.GetName(), ValuePrefix: m.GetValuePrefix(),
		Roles: slices.Clone(m.GetRoles()), Scopes: slices.Clone(m.GetScopes()),
	}
	// Zero on the wire is a key that never expires, which Go spells as the
	// zero time rather than as 1970.
	if m.GetValidUntil() != 0 {
		key.ValidUntil = time.Unix(m.GetValidUntil(), 0).UTC()
	}
	return key
}

// toCreatedKey refuses a value that arrives without its key: a message field
// has no presence at the accessor, so converting it anyway would describe a
// key with every field empty, as if it were real. The value is left out of
// the error, since it is a working credential.
func toCreatedKey(m *adminv1.CreateExternalUserKeyResponse) (*CreatedKey, error) {
	if m.GetKey() == nil {
		return nil, public(fault.Internal("the service returned a key value but no key; list the user's keys to see whether one was created"))
	}
	value := m.GetValue()
	return &CreatedKey{Key: toExternalUserKey(m.GetKey()), value: &value}, nil
}

// cloned is s, never shared with a message.
func cloned(s *string) *string {
	if s == nil {
		return nil
	}
	copied := *s
	return &copied
}
