package memory

import (
	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/transport"
)

// Service is the memory service and the methods safe to retry on it.
//
// Only reads that mint nothing are retried. CreateMemory, EnrichMemory,
// ShareFeedback and RevertMemory each write; ImportMemories would report a
// replayed memory as DUPLICATE rather than QUEUED; StartSession mints a
// session; and Search mints one when unscoped, while a scoped replay comes back
// with bare references in place of the insights already delivered. GetMemory
// only counts one more delivery, and ListTools has no effect at all.
var Service = transport.Service{
	Name:      memoryv1.MemoryService_ServiceDesc.ServiceName,
	Retryable: []string{"ListDomains", "GetMemory", "ListTools"},
}
