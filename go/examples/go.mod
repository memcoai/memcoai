module github.com/memcoai/memcoai/go/examples

go 1.25.0

require (
	github.com/anthropics/anthropic-sdk-go v1.73.0
	github.com/memcoai/memcoai/go v0.0.0
	google.golang.org/grpc v1.83.2
	google.golang.org/protobuf v1.36.12
)

require (
	github.com/bahlo/generic-list-go v0.2.0 // indirect
	github.com/buger/jsonparser v1.1.2 // indirect
	github.com/invopop/jsonschema v0.14.0 // indirect
	github.com/pb33f/ordered-map/v2 v2.3.1 // indirect
	github.com/standard-webhooks/standard-webhooks/libraries v0.0.1 // indirect
	github.com/tidwall/gjson v1.18.0 // indirect
	github.com/tidwall/match v1.1.1 // indirect
	github.com/tidwall/pretty v1.2.1 // indirect
	github.com/tidwall/sjson v1.2.5 // indirect
	go.yaml.in/yaml/v4 v4.0.0-rc.2 // indirect
	golang.org/x/net v0.58.0 // indirect
	golang.org/x/sync v0.22.0 // indirect
	golang.org/x/sys v0.47.0 // indirect
	golang.org/x/text v0.41.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20260526163538-3dc84a4a5aaa // indirect
)

// The examples run against the SDK in this repository. Outside it, drop this
// line and require a released version instead.
replace github.com/memcoai/memcoai/go => ../
