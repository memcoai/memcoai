package memory

import (
	"sync"

	"google.golang.org/protobuf/proto"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
)

// Known holds the caps the service has reported so far.
type Known struct {
	mu     sync.RWMutex
	limits *memoryv1.Limits
	tags   map[string]int32
}

// Snapshot is one consistent view of the caps for one request.
type Snapshot struct {
	// Limits is nil until the service reports any.
	Limits  *memoryv1.Limits
	MaxTags int32
}

// Update records what a ListDomains response reported. A response without
// limits clears nothing, and a domain's tag cap is kept once seen.
func (k *Known) Update(limits *memoryv1.Limits, domains []*memoryv1.DomainEntry) {
	k.mu.Lock()
	defer k.mu.Unlock()
	if limits != nil {
		k.limits = proto.Clone(limits).(*memoryv1.Limits)
	}
	if k.tags == nil {
		k.tags = map[string]int32{}
	}
	for _, domain := range domains {
		k.tags[domain.GetSlug()] = domain.GetMaxTagsPerQuery()
	}
}

// Snapshot returns the caps for a request naming domain; a request naming
// none gets no tag cap.
func (k *Known) Snapshot(domain string) Snapshot {
	k.mu.RLock()
	defer k.mu.RUnlock()
	var snapshot Snapshot
	if k.limits != nil {
		snapshot.Limits = proto.Clone(k.limits).(*memoryv1.Limits)
	}
	if domain != "" {
		snapshot.MaxTags = k.tags[domain]
	}
	return snapshot
}
