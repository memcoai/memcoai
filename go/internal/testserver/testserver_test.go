package testserver_test

import (
	"regexp"
	"slices"
	"strings"
	"testing"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/testserver"
)

// The suites that exercise every call iterate Methods, so a method the
// contract gains and the list misses would go untested without failing.
func TestMethodsAreTheContractsInOrder(t *testing.T) {
	var contract []string
	for _, method := range memoryv1.MemoryService_ServiceDesc.Methods {
		contract = append(contract, method.MethodName)
	}
	if !slices.Equal(testserver.Methods, contract) {
		t.Fatalf("Methods %v\ncontract %v", testserver.Methods, contract)
	}
}

// The real ListTools reports every method the contract declares; a fake that
// missed one would hide it from every test relying on the default catalog.
func TestToolsAreEveryMethodTheContractDeclares(t *testing.T) {
	word := regexp.MustCompile(`([a-z])([A-Z])`)
	var declared []string
	for _, method := range memoryv1.MemoryService_ServiceDesc.Methods {
		declared = append(declared, strings.ToLower(word.ReplaceAllString(method.MethodName, "${1}_${2}")))
	}
	if !slices.Equal(testserver.Tools, declared) {
		t.Fatalf("Tools %v\ncontract %v", testserver.Tools, declared)
	}
}
