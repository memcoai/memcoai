// Package internal holds what the SDK's internal packages read from the
// exported client tree.
package internal

import _ "embed"

// Descriptor is the export's SDK_PROVENANCE.yaml, byte for byte.
//
//go:embed client/SDK_PROVENANCE.yaml
var Descriptor []byte
