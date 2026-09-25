package memcoai

import (
	"context"
	"errors"

	"google.golang.org/grpc/codes"

	"github.com/memcoai/memcoai/go/internal/fault"
)

// Error is implemented by every error this package returns. See the package
// documentation for the concrete types and when each is returned.
type Error interface {
	error
	memcoError()
}

// ConfigError reports a setting, or a closed client, that stopped a call
// before anything was sent.
//
// One case is the exception: this machine's clock, running so far ahead of
// the service's that the key [MemoryOperations.StartSession] minted with
// [ExternalID] had already expired by it. There the mint was sent; the key it
// returned is ended at once. It arises only from a service that does not say
// how many seconds a key has left.
type ConfigError struct {
	// Message says what is wrong.
	Message string
}

// Error returns the message.
func (e *ConfigError) Error() string { return e.Message }
func (*ConfigError) memcoError()     {}

// APIError is a failure the service reported, or a request the SDK refused
// on the service's behalf. Every other API error type embeds it.
type APIError struct {
	// Code is the gRPC status code.
	Code codes.Code
	// Detail is the service's explanation, or the SDK's for a local refusal.
	Detail string

	cause error
}

// Error returns the protocol's name for the code, then the detail, as in
// "INVALID_ARGUMENT: query must not be empty".
func (e *APIError) Error() string { return fault.CodeName(e.Code) + ": " + e.Detail }

// Unwrap returns the context error that ended the call, if a context did.
func (e *APIError) Unwrap() error { return e.cause }
func (*APIError) memcoError()     {}

// AuthenticationError reports a rejected credential. Retrying will not help.
type AuthenticationError struct{ APIError }

// Unwrap returns the embedded [APIError].
func (e *AuthenticationError) Unwrap() error { return &e.APIError }

// PermissionError reports a credential not allowed to do what was asked.
type PermissionError struct{ APIError }

// Unwrap returns the embedded [APIError].
func (e *PermissionError) Unwrap() error { return &e.APIError }

// InvalidRequestError reports a malformed request. The SDK raises many itself,
// before anything is sent.
type InvalidRequestError struct{ APIError }

// Unwrap returns the embedded [APIError].
func (e *InvalidRequestError) Unwrap() error { return &e.APIError }

// NotFoundError reports a handle that resolves to nothing.
type NotFoundError struct{ APIError }

// Unwrap returns the embedded [APIError].
func (e *NotFoundError) Unwrap() error { return &e.APIError }

// AlreadyExistsError reports that what the call would create already exists,
// such as a network name or an external user id already taken. Retrying will
// not help: use what exists, or create it under another name.
type AlreadyExistsError struct{ APIError }

// Unwrap returns the embedded [APIError].
func (e *AlreadyExistsError) Unwrap() error { return &e.APIError }

// PreconditionFailedError reports an account or request in a state that does
// not allow the call, such as disabled billing. Retrying will not help until
// something outside the request changes. The refusals the service names by a
// reason this SDK knows are subtypes: [*SunsetError],
// [*UserAlreadyAssignedNetworkError] and
// [*ExternalUserNeedsCustomerNetworkError].
type PreconditionFailedError struct {
	APIError
	// Reason is the name the service gave the refusal, such as
	// "USER_ALREADY_ASSIGNED_NETWORK", or empty when it gave none.
	Reason string
	// Metadata is the detail the service attached to the refusal, key to
	// value, such as the network a user is already in; nil when it attached
	// none. A key this SDK does not name is kept too.
	Metadata map[string]string
}

// Unwrap returns the embedded [APIError].
func (e *PreconditionFailedError) Unwrap() error { return &e.APIError }

// UserAlreadyAssignedNetworkError reports a user already placed in another
// network of the same memory domain. A user holds one network per domain, so
// [NetworkOperations.AddMember] refuses a second placement rather than moving
// them silently; pass [AddMemberParams].Force to move them. It is also a
// [*PreconditionFailedError].
type UserAlreadyAssignedNetworkError struct {
	PreconditionFailedError
	// CurrentNetworkID is the network the user is in, or empty if the service
	// did not say.
	CurrentNetworkID string
	// CurrentNetworkName is that network's name, or empty if the service did
	// not say.
	CurrentNetworkName string
}

// Unwrap returns the embedded [PreconditionFailedError].
func (e *UserAlreadyAssignedNetworkError) Unwrap() error { return &e.PreconditionFailedError }

// ExternalUserNeedsCustomerNetworkError reports an external user placed in a
// network that is not a customer network, a domain's root included. Place the
// user in a customer network instead. It is also a
// [*PreconditionFailedError].
type ExternalUserNeedsCustomerNetworkError struct {
	PreconditionFailedError
	// RequiredNetworkScope is the scope a network needs to take the user,
	// "customer", or empty if the service did not say.
	RequiredNetworkScope string
}

// Unwrap returns the embedded [PreconditionFailedError].
func (e *ExternalUserNeedsCustomerNetworkError) Unwrap() error { return &e.PreconditionFailedError }

// SunsetKind says what is past its sunset date.
type SunsetKind string

// The kinds of sunset.
const (
	// SunsetClientVersion: this SDK release no longer works; upgrade it.
	SunsetClientVersion SunsetKind = "client_version"
	// SunsetAPIVersion: the API version no longer works; migrate to the
	// current one.
	SunsetAPIVersion SunsetKind = "api_version"
)

// SunsetError reports that what the caller uses has stopped working. Detail
// is the service's remedy. It is also a [*PreconditionFailedError].
type SunsetError struct {
	PreconditionFailedError
	// Kind says what is past its sunset.
	Kind SunsetKind
}

// Unwrap returns the embedded [PreconditionFailedError].
func (e *SunsetError) Unwrap() error { return &e.PreconditionFailedError }

// ResourceExhaustedKind says which allowance ran out.
type ResourceExhaustedKind string

// The kinds of exhaustion, read from the service's wording.
const (
	// ResourceExhaustedRateLimit: a short window; retrying after a pause helps.
	ResourceExhaustedRateLimit ResourceExhaustedKind = "rate_limit"
	// ResourceExhaustedQuota: a daily, weekly or monthly allowance; retrying
	// will not help until it resets.
	ResourceExhaustedQuota ResourceExhaustedKind = "quota"
	// ResourceExhaustedUnknown: the wording named neither; treat it as a rate
	// limit.
	ResourceExhaustedUnknown ResourceExhaustedKind = "unknown"
)

// ResourceExhaustedError reports a spent rate limit or quota. The SDK does not
// retry it.
type ResourceExhaustedError struct {
	APIError
	// Kind says which allowance ran out.
	Kind ResourceExhaustedKind
}

// Unwrap returns the embedded [APIError].
func (e *ResourceExhaustedError) Unwrap() error { return &e.APIError }

// UnavailableError reports a service that could not be reached. Reads have
// already been retried; backing off and retrying is the right answer.
type UnavailableError struct{ APIError }

// Unwrap returns the embedded [APIError].
func (e *UnavailableError) Unwrap() error { return &e.APIError }

// UnhealthyError reports a service that answered [Client.Connect]'s probe but
// is not serving. It is also an [*UnavailableError].
type UnhealthyError struct{ UnavailableError }

// Unwrap returns the embedded [UnavailableError].
func (e *UnhealthyError) Unwrap() error { return &e.UnavailableError }

// TimeoutError reports a call that ran out of time. It is not retried: a write
// may have happened.
type TimeoutError struct{ APIError }

// Unwrap returns the embedded [APIError].
func (e *TimeoutError) Unwrap() error { return &e.APIError }

// InternalError reports anything else, including a call cancelled through its
// context.
type InternalError struct{ APIError }

// Unwrap returns the embedded [APIError].
func (e *InternalError) Unwrap() error { return &e.APIError }

// public maps an internal error to the concrete public type. Every error this
// package returns goes through it, so none reaches a caller untyped.
func public(err error) error {
	if err == nil {
		return nil
	}
	var already Error
	if errors.As(err, &already) {
		return already
	}
	f := fault.FromRPC(context.Background(), err)
	api := APIError{Code: f.Code, Detail: f.Detail, cause: f.Cause}
	switch f.Kind {
	case fault.KindConfig:
		return &ConfigError{Message: f.Detail}
	case fault.KindUnhealthy:
		return &UnhealthyError{UnavailableError{api}}
	}
	switch f.Code {
	case codes.Unauthenticated:
		return &AuthenticationError{api}
	case codes.PermissionDenied:
		return &PermissionError{api}
	case codes.InvalidArgument:
		return &InvalidRequestError{api}
	case codes.NotFound:
		return &NotFoundError{api}
	case codes.AlreadyExists:
		return &AlreadyExistsError{api}
	case codes.FailedPrecondition:
		precondition := PreconditionFailedError{APIError: api, Reason: f.Reason, Metadata: f.Metadata}
		switch f.Reason {
		case fault.ReasonUserAlreadyAssignedNetwork:
			return &UserAlreadyAssignedNetworkError{
				precondition, f.Metadata["current_network_id"], f.Metadata["current_network_name"],
			}
		case fault.ReasonExternalUserNeedsCustomerNetwork:
			return &ExternalUserNeedsCustomerNetworkError{precondition, f.Metadata["required_network_scope"]}
		}
		if f.Sunset != "" {
			return &SunsetError{precondition, SunsetKind(f.Sunset)}
		}
		return &precondition
	case codes.ResourceExhausted:
		return &ResourceExhaustedError{api, ResourceExhaustedKind(f.Exhaustion)}
	case codes.Unavailable:
		return &UnavailableError{api}
	case codes.DeadlineExceeded:
		return &TimeoutError{api}
	default:
		return &InternalError{api}
	}
}
