package memory

import (
	"testing"

	"google.golang.org/protobuf/proto"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
)

func TestNothingIsKnownAtFirst(t *testing.T) {
	var known Known
	if s := known.Snapshot("coding"); s.Limits != nil || s.MaxTags != 0 {
		t.Fatalf("got %+v", s)
	}
}

func TestReportedLimitsAndTagCapsAreLearned(t *testing.T) {
	var known Known
	known.Update(&memoryv1.Limits{MaxQueryCharacters: 10}, []*memoryv1.DomainEntry{
		{Slug: "coding", MaxTagsPerQuery: 3},
		{Slug: "open"},
	})
	s := known.Snapshot("coding")
	if s.Limits.GetMaxQueryCharacters() != 10 || s.MaxTags != 3 {
		t.Fatalf("got %+v", s)
	}
	if s := known.Snapshot("open"); s.MaxTags != 0 {
		t.Fatalf("a domain with no cap: %+v", s)
	}
	if s := known.Snapshot("unlisted"); s.MaxTags != 0 || s.Limits == nil {
		t.Fatalf("an unknown domain: %+v", s)
	}
	if s := known.Snapshot(""); s.MaxTags != 0 {
		t.Fatalf("a session-only call: %+v", s)
	}
}

func TestSilenceIsNotARetraction(t *testing.T) {
	var known Known
	known.Update(&memoryv1.Limits{MaxQueryCharacters: 10}, nil)
	known.Update(nil, nil)
	if known.Snapshot("").Limits.GetMaxQueryCharacters() != 10 {
		t.Fatal("a response without limits cleared the known ones")
	}
	known.Update(&memoryv1.Limits{MaxQueryCharacters: 20}, nil)
	if known.Snapshot("").Limits.GetMaxQueryCharacters() != 20 {
		t.Fatal("a new report did not replace the old one")
	}
}

func TestTagCapsAccumulateAcrossListings(t *testing.T) {
	var known Known
	known.Update(nil, []*memoryv1.DomainEntry{{Slug: "a", MaxTagsPerQuery: 1}})
	known.Update(nil, []*memoryv1.DomainEntry{{Slug: "b", MaxTagsPerQuery: 2}})
	known.Update(nil, []*memoryv1.DomainEntry{{Slug: "b", MaxTagsPerQuery: 5}})
	if known.Snapshot("a").MaxTags != 1 || known.Snapshot("b").MaxTags != 5 {
		t.Fatalf("a=%d b=%d", known.Snapshot("a").MaxTags, known.Snapshot("b").MaxTags)
	}
}

func TestNeitherTheReportNorASnapshotSharesMemoryWithWhatIsKnown(t *testing.T) {
	var known Known
	reported := &memoryv1.Limits{MaxQueryCharacters: 10}
	known.Update(reported, nil)
	reported.MaxQueryCharacters = 99
	first := known.Snapshot("")
	first.Limits.MaxQueryCharacters = 77
	if got := known.Snapshot("").Limits; !proto.Equal(got, &memoryv1.Limits{MaxQueryCharacters: 10}) {
		t.Fatalf("got %v", got)
	}
}
