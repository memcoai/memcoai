// Package fault is the SDK's one internal error type, and the translation of
// gRPC failures into it.
package fault

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"google.golang.org/genproto/googleapis/rpc/errdetails"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// Kind says which public error family an Error becomes.
type Kind int

const (
	KindAPI Kind = iota
	KindConfig
	KindUnhealthy
)

// Sunset and exhaustion kinds, spelled as the public API reports them.
const (
	SunsetClientVersion = "client_version"
	SunsetAPIVersion    = "api_version"

	ExhaustionRateLimit = "rate_limit"
	ExhaustionQuota     = "quota"
	ExhaustionUnknown   = "unknown"
)

// Error is what every internal package returns.
type Error struct {
	Kind       Kind
	Code       codes.Code
	Detail     string
	Sunset     string
	Exhaustion string
	// Cause is the caller's context error, when that context ended the call.
	Cause error
}

func (e *Error) Error() string {
	if e.Kind == KindConfig {
		return e.Detail
	}
	return CodeName(e.Code) + ": " + e.Detail
}

func (e *Error) Unwrap() error { return e.Cause }

func Config(message string) *Error { return &Error{Kind: KindConfig, Detail: message} }

func Invalid(detail string) *Error { return &Error{Code: codes.InvalidArgument, Detail: detail} }

func NotFound(detail string) *Error { return &Error{Code: codes.NotFound, Detail: detail} }

func Internal(detail string) *Error { return &Error{Code: codes.Internal, Detail: detail} }

func Unhealthy(detail string) *Error {
	return &Error{Kind: KindUnhealthy, Code: codes.Unavailable, Detail: detail}
}

// memcoDomain namespaces the ErrorInfo reasons this SDK reads.
const memcoDomain = "memco.ai"

var sunsets = map[string]string{
	"CLIENT_VERSION_SUNSET": SunsetClientVersion,
	"API_VERSION_SUNSET":    SunsetAPIVersion,
}

// FromRPC turns whatever a call returned into an Error.
func FromRPC(ctx context.Context, err error) *Error {
	var already *Error
	if errors.As(err, &already) {
		return already
	}
	// grpc-go wraps the last attempt's status once retries run out, so the
	// status is looked for rather than read off the outermost error.
	var carrier interface{ GRPCStatus() *status.Status }
	if !errors.As(err, &carrier) {
		return &Error{Code: codes.Unknown, Detail: err.Error()}
	}
	st := carrier.GRPCStatus()
	code := st.Code()
	if code > codes.Unauthenticated {
		code = codes.Unknown
	}
	translated := &Error{Code: code, Detail: st.Message()}
	switch code {
	case codes.FailedPrecondition:
		translated.Sunset = sunset(st)
	case codes.ResourceExhausted:
		translated.Exhaustion = exhaustion(st.Message())
	case codes.Canceled, codes.DeadlineExceeded:
		// Only when the caller's context is what ended the call: a server
		// that reports one of these codes has not cancelled anything of theirs.
		if ctx.Err() != nil {
			translated.Cause = ctx.Err()
		}
	}
	return translated
}

// FromContext is the error for a call whose context has already ended.
func FromContext(ctx context.Context) *Error {
	return FromRPC(ctx, status.FromContextError(ctx.Err()).Err())
}

// sunset reads the cause of a sunset from a memco ErrorInfo. A reason it does
// not recognise is not guessed at.
func sunset(st *status.Status) string {
	for _, detail := range st.Details() {
		info, ok := detail.(*errdetails.ErrorInfo)
		if !ok || info.GetDomain() != memcoDomain {
			continue
		}
		if kind, known := sunsets[info.GetReason()]; known {
			return kind
		}
	}
	return ""
}

// Quota markers are checked first: the longer window decides whether waiting
// can help.
var (
	quotaMarkers     = []string{"daily", "weekly", "monthly", "quota"}
	rateLimitMarkers = []string{"rate limit", "per-minute", "per minute", "per-second", "too many requests"}
)

func exhaustion(detail string) string {
	lower := strings.ToLower(detail)
	contains := func(markers []string) bool {
		for _, marker := range markers {
			if strings.Contains(lower, marker) {
				return true
			}
		}
		return false
	}
	switch {
	case contains(quotaMarkers):
		return ExhaustionQuota
	case contains(rateLimitMarkers):
		return ExhaustionRateLimit
	default:
		return ExhaustionUnknown
	}
}

var codeNames = map[codes.Code]string{
	codes.OK:                 "OK",
	codes.Canceled:           "CANCELLED",
	codes.Unknown:            "UNKNOWN",
	codes.InvalidArgument:    "INVALID_ARGUMENT",
	codes.DeadlineExceeded:   "DEADLINE_EXCEEDED",
	codes.NotFound:           "NOT_FOUND",
	codes.AlreadyExists:      "ALREADY_EXISTS",
	codes.PermissionDenied:   "PERMISSION_DENIED",
	codes.ResourceExhausted:  "RESOURCE_EXHAUSTED",
	codes.FailedPrecondition: "FAILED_PRECONDITION",
	codes.Aborted:            "ABORTED",
	codes.OutOfRange:         "OUT_OF_RANGE",
	codes.Unimplemented:      "UNIMPLEMENTED",
	codes.Internal:           "INTERNAL",
	codes.Unavailable:        "UNAVAILABLE",
	codes.DataLoss:           "DATA_LOSS",
	codes.Unauthenticated:    "UNAUTHENTICATED",
}

// CodeName is the canonical, protocol spelling of a status code;
// codes.Code.String is Go's own CamelCase.
func CodeName(code codes.Code) string {
	if name, ok := codeNames[code]; ok {
		return name
	}
	return fmt.Sprintf("UNRECOGNIZED(%d)", code)
}
