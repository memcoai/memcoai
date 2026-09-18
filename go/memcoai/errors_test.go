package memcoai

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"testing"

	"google.golang.org/grpc/codes"

	"github.com/memcoai/memcoai/go/internal/fault"
	"github.com/memcoai/memcoai/go/internal/testserver"
)

func TestEveryFaultBecomesTheErrorThatSaysWhatHappened(t *testing.T) {
	api := func(code codes.Code) APIError { return APIError{Code: code, Detail: "d"} }
	cases := []struct {
		fault *fault.Error
		want  error
	}{
		{fault.Config("d"), &ConfigError{Message: "d"}},
		{&fault.Error{Code: codes.Unauthenticated, Detail: "d"}, &AuthenticationError{api(codes.Unauthenticated)}},
		{&fault.Error{Code: codes.PermissionDenied, Detail: "d"}, &PermissionError{api(codes.PermissionDenied)}},
		{&fault.Error{Code: codes.InvalidArgument, Detail: "d"}, &InvalidRequestError{api(codes.InvalidArgument)}},
		{&fault.Error{Code: codes.NotFound, Detail: "d"}, &NotFoundError{api(codes.NotFound)}},
		{&fault.Error{Code: codes.FailedPrecondition, Detail: "d"}, &PreconditionFailedError{api(codes.FailedPrecondition)}},
		{
			&fault.Error{Code: codes.FailedPrecondition, Detail: "d", Sunset: fault.SunsetAPIVersion},
			&SunsetError{PreconditionFailedError{api(codes.FailedPrecondition)}, SunsetAPIVersion},
		},
		{
			&fault.Error{Code: codes.ResourceExhausted, Detail: "d", Exhaustion: fault.ExhaustionQuota},
			&ResourceExhaustedError{api(codes.ResourceExhausted), ResourceExhaustedQuota},
		},
		{&fault.Error{Code: codes.Unavailable, Detail: "d"}, &UnavailableError{api(codes.Unavailable)}},
		{fault.Unhealthy("d"), &UnhealthyError{UnavailableError{api(codes.Unavailable)}}},
		{&fault.Error{Code: codes.DeadlineExceeded, Detail: "d"}, &TimeoutError{api(codes.DeadlineExceeded)}},
	}
	for _, code := range []codes.Code{codes.OK, codes.Canceled, codes.Unknown, codes.AlreadyExists, codes.Aborted,
		codes.OutOfRange, codes.Unimplemented, codes.Internal, codes.DataLoss} {
		cases = append(cases, struct {
			fault *fault.Error
			want  error
		}{&fault.Error{Code: code, Detail: "d"}, &InternalError{api(code)}})
	}
	for _, c := range cases {
		// Compared whole, so the concrete type and every field must match.
		if got := public(c.fault); !reflect.DeepEqual(got, c.want) {
			t.Errorf("%+v became %#v, want %#v", c.fault, got, c.want)
		}
	}
}

func TestAnErrorThatIsNotAFaultIsInternal(t *testing.T) {
	got := as[*InternalError](t, public(errors.New("boom")))
	if got.Code != codes.Unknown || got.Detail != "boom" {
		t.Fatalf("got %+v", got)
	}
	if public(nil) != nil {
		t.Fatal("nil became an error")
	}
	already := &NotFoundError{APIError{Code: codes.NotFound, Detail: "x"}}
	if got := public(fmt.Errorf("wrapped: %w", already)); !reflect.DeepEqual(got, error(already)) {
		t.Fatalf("a public error came back as %#v", got)
	}
}

func TestTheMessageUsesTheProtocolsNames(t *testing.T) {
	cases := map[error]string{
		public(fault.Invalid("query must not be empty")):           "INVALID_ARGUMENT: query must not be empty",
		public(&fault.Error{Code: codes.Canceled, Detail: "gone"}): "CANCELLED: gone",
		public(&fault.Error{Code: codes.Code(99), Detail: "?"}):    "UNRECOGNIZED(99): ?",
		public(fault.Config("no API token")):                       "no API token",
	}
	for err, want := range cases {
		if err.Error() != want {
			t.Errorf("got %q, want %q", err.Error(), want)
		}
	}
}

func TestErrorsAsReachesEveryAncestor(t *testing.T) {
	sunset := public(&fault.Error{Code: codes.FailedPrecondition, Sunset: fault.SunsetClientVersion})
	expect[*SunsetError](t, sunset)
	expect[*PreconditionFailedError](t, sunset)
	expect[*APIError](t, sunset)
	expect[Error](t, sunset)
	var unavailable *UnavailableError
	if errors.As(sunset, &unavailable) {
		t.Error("a sunset is an unavailable error")
	}

	unhealthy := public(fault.Unhealthy("down"))
	expect[*UnhealthyError](t, unhealthy)
	expect[*UnavailableError](t, unhealthy)
	expect[*APIError](t, unhealthy)
	var precondition *PreconditionFailedError
	if errors.As(unhealthy, &precondition) {
		t.Error("an unhealthy service is a failed precondition")
	}

	for _, err := range []error{
		public(&fault.Error{Code: codes.Unauthenticated}),
		public(&fault.Error{Code: codes.PermissionDenied}),
		public(fault.Invalid("x")),
		public(fault.NotFound("x")),
		public(&fault.Error{Code: codes.ResourceExhausted}),
		public(&fault.Error{Code: codes.DeadlineExceeded}),
		public(fault.Internal("x")),
	} {
		expect[*APIError](t, err)
		expect[Error](t, err)
	}

	config := public(fault.Config("x"))
	expect[Error](t, config)
	var api *APIError
	if errors.As(config, &api) {
		t.Error("a config error is an API error")
	}
}

func TestTheCallersOwnContextIsReachableWithErrorsIs(t *testing.T) {
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	err := public(fault.FromContext(cancelled))
	if as[*InternalError](t, err).Code != codes.Canceled || !errors.Is(err, context.Canceled) {
		t.Errorf("cancelled: %v", err)
	}
	expired, stop := context.WithTimeout(context.Background(), 0)
	defer stop()
	<-expired.Done()
	err = public(fault.FromContext(expired))
	expect[*TimeoutError](t, err)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Errorf("expired: %v", err)
	}
}

func TestExhaustedRetriesKeepTheServicesDetail(t *testing.T) {
	f := connected(t)
	f.server.Memory.FailNext("GetMemory",
		testserver.Failure{Code: codes.Unavailable, Details: "try again"},
		testserver.Failure{Code: codes.Unavailable, Details: "try again"},
		testserver.Failure{Code: codes.Unavailable, Details: "try again"})
	_, err := f.client.Memory.GetMemory(ctx, "memory-a-1")
	if got := as[*UnavailableError](t, err); got.Detail != "try again" {
		t.Fatalf("got %q", got.Detail)
	}
	if n := len(f.server.Memory.Calls()); n != 3 {
		t.Fatalf("%d attempts", n)
	}
}

func TestAServerSentDeadlineIsNotTheCallers(t *testing.T) {
	f := connected(t)
	f.server.Memory.Fail(&testserver.Failure{Code: codes.DeadlineExceeded, Details: "server ran out of time"})
	_, err := f.client.Memory.ListDomains(ctx)
	expect[*TimeoutError](t, err)
	if errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("a server's deadline claims the caller's context")
	}
}
