package fault

import (
	"context"
	"errors"
	"fmt"
	"maps"
	"testing"

	"google.golang.org/genproto/googleapis/rpc/errdetails"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func withInfo(t *testing.T, code codes.Code, message string, infos ...*errdetails.ErrorInfo) error {
	t.Helper()
	st := status.New(code, message)
	for _, info := range infos {
		var err error
		if st, err = st.WithDetails(info); err != nil {
			t.Fatal(err)
		}
	}
	return st.Err()
}

func TestEveryCodeKeepsItsCode(t *testing.T) {
	for code := codes.Canceled; code <= codes.Unauthenticated; code++ {
		got := FromRPC(context.Background(), status.Error(code, "detail"))
		if got.Kind != KindAPI || got.Code != code || got.Detail != "detail" {
			t.Errorf("%v: got %+v", code, got)
		}
	}
}

func TestAnUnknownCodeFoldsToUnknown(t *testing.T) {
	got := FromRPC(context.Background(), status.Error(codes.Code(42), "odd"))
	if got.Code != codes.Unknown || got.Detail != "odd" {
		t.Fatalf("got %+v", got)
	}
}

func TestCodeNamesAreTheProtocolsSpelling(t *testing.T) {
	cases := map[codes.Code]string{
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
		codes.Code(99):           "UNRECOGNIZED(99)",
	}
	for code, want := range cases {
		if got := CodeName(code); got != want {
			t.Errorf("CodeName(%d) = %q, want %q", code, got, want)
		}
	}
}

func TestTheMessageNamesTheCodeThenTheDetail(t *testing.T) {
	if got := Invalid("query must not be empty").Error(); got != "INVALID_ARGUMENT: query must not be empty" {
		t.Errorf("Invalid: %q", got)
	}
	if got := Config("no API token").Error(); got != "no API token" {
		t.Errorf("Config: %q", got)
	}
}

func TestConstructorsSayWhatTheyAre(t *testing.T) {
	cases := []struct {
		got  *Error
		kind Kind
		code codes.Code
	}{
		{Config("c"), KindConfig, codes.OK},
		{Invalid("i"), KindAPI, codes.InvalidArgument},
		{NotFound("n"), KindAPI, codes.NotFound},
		{Internal("x"), KindAPI, codes.Internal},
		{Unhealthy("u"), KindUnhealthy, codes.Unavailable},
	}
	for _, c := range cases {
		if c.got.Kind != c.kind || c.got.Code != c.code || c.got.Detail == "" {
			t.Errorf("got %+v, want kind %v code %v", c.got, c.kind, c.code)
		}
	}
}

func TestASunsetIsReadFromAMemcoErrorInfo(t *testing.T) {
	cases := []struct {
		name  string
		infos []*errdetails.ErrorInfo
		want  string
	}{
		{"client", []*errdetails.ErrorInfo{{Reason: "CLIENT_VERSION_SUNSET", Domain: "memco.ai"}}, SunsetClientVersion},
		{"api", []*errdetails.ErrorInfo{{Reason: "API_VERSION_SUNSET", Domain: "memco.ai"}}, SunsetAPIVersion},
		{"another domain", []*errdetails.ErrorInfo{{Reason: "CLIENT_VERSION_SUNSET", Domain: "example.com"}}, ""},
		{"unknown reason", []*errdetails.ErrorInfo{{Reason: "SOMETHING_ELSE", Domain: "memco.ai"}}, ""},
		// Node keeps scanning past a memco detail it does not recognise.
		{"unknown then known", []*errdetails.ErrorInfo{
			{Reason: "SOMETHING_ELSE", Domain: "memco.ai"},
			{Reason: "API_VERSION_SUNSET", Domain: "memco.ai"},
		}, SunsetAPIVersion},
		{"no details", nil, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := FromRPC(context.Background(), withInfo(t, codes.FailedPrecondition, "too old", c.infos...))
			if got.Code != codes.FailedPrecondition || got.Detail != "too old" || got.Sunset != c.want {
				t.Fatalf("got %+v, want sunset %q", got, c.want)
			}
		})
	}
}

func TestAPreconditionKeepsTheReasonAndMetadataItWasNamedBy(t *testing.T) {
	assigned := map[string]string{"current_network_id": "network-a", "current_network_name": "Acme"}
	cases := []struct {
		name     string
		infos    []*errdetails.ErrorInfo
		reason   string
		metadata map[string]string
	}{
		{"known", []*errdetails.ErrorInfo{{Reason: ReasonUserAlreadyAssignedNetwork, Domain: "memco.ai", Metadata: assigned}},
			ReasonUserAlreadyAssignedNetwork, assigned},
		// An unknown reason is kept with its detail, for a caller newer than this SDK.
		{"unknown", []*errdetails.ErrorInfo{{Reason: "NEWER_REASON", Domain: "memco.ai", Metadata: map[string]string{"k": "v"}}},
			"NEWER_REASON", map[string]string{"k": "v"}},
		{"unknown then known", []*errdetails.ErrorInfo{
			{Reason: "NEWER_REASON", Domain: "memco.ai"},
			{Reason: ReasonExternalUserNeedsCustomerNetwork, Domain: "memco.ai", Metadata: map[string]string{"required_network_scope": "customer"}},
		}, ReasonExternalUserNeedsCustomerNetwork, map[string]string{"required_network_scope": "customer"}},
		{"another domain", []*errdetails.ErrorInfo{{Reason: ReasonUserAlreadyAssignedNetwork, Domain: "example.com", Metadata: assigned}},
			"", nil},
		{"no details", nil, "", nil},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := FromRPC(context.Background(), withInfo(t, codes.FailedPrecondition, "network_move_required: taken", c.infos...))
			if got.Reason != c.reason || !maps.Equal(got.Metadata, c.metadata) || got.Sunset != "" {
				t.Fatalf("got %+v, want %q %v", got, c.reason, c.metadata)
			}
		})
	}
}

func TestAPlacementReasonOnAnotherCodeIsIgnored(t *testing.T) {
	err := withInfo(t, codes.PermissionDenied, "no", &errdetails.ErrorInfo{Reason: ReasonUserAlreadyAssignedNetwork, Domain: "memco.ai"})
	if got := FromRPC(context.Background(), err); got.Reason != "" || got.Metadata != nil {
		t.Fatalf("got %+v", got)
	}
}

func TestASunsetDetailOnAnotherCodeIsIgnored(t *testing.T) {
	err := withInfo(t, codes.Unavailable, "down", &errdetails.ErrorInfo{Reason: "CLIENT_VERSION_SUNSET", Domain: "memco.ai"})
	if got := FromRPC(context.Background(), err); got.Sunset != "" {
		t.Fatalf("got %+v", got)
	}
}

func TestExhaustionIsClassifiedByItsWording(t *testing.T) {
	cases := map[string]string{
		"daily limit reached":           ExhaustionQuota,
		"weekly allowance spent":        ExhaustionQuota,
		"Monthly usage exceeded":        ExhaustionQuota,
		"QUOTA exceeded":                ExhaustionQuota,
		"daily rate limit reached":      ExhaustionQuota,
		"rate limit exceeded":           ExhaustionRateLimit,
		"too many per-minute requests":  ExhaustionRateLimit,
		"30 per minute":                 ExhaustionRateLimit,
		"5 per-second max":              ExhaustionRateLimit,
		"Too Many Requests":             ExhaustionRateLimit,
		"resource exhausted, try later": ExhaustionUnknown,
		"":                              ExhaustionUnknown,
	}
	for detail, want := range cases {
		got := FromRPC(context.Background(), status.Error(codes.ResourceExhausted, detail))
		if got.Exhaustion != want {
			t.Errorf("%q: got %q, want %q", detail, got.Exhaustion, want)
		}
	}
	if got := FromRPC(context.Background(), status.Error(codes.Internal, "quota")); got.Exhaustion != "" {
		t.Errorf("a non-exhaustion code carries an exhaustion kind: %+v", got)
	}
}

func TestAWrappedStatusKeepsTheServersDetail(t *testing.T) {
	// grpc-go wraps the last attempt's status when retries run out.
	wrapped := fmt.Errorf("max retries exhausted: failed after 3 attempts: %w", status.Error(codes.Unavailable, "try again"))
	got := FromRPC(context.Background(), wrapped)
	if got.Code != codes.Unavailable || got.Detail != "try again" {
		t.Fatalf("got %+v", got)
	}
}

func TestAnErrorAlreadyTranslatedPassesThrough(t *testing.T) {
	original := Invalid("x")
	if got := FromRPC(context.Background(), fmt.Errorf("wrapped: %w", original)); got != original {
		t.Fatalf("got %+v", got)
	}
}

func TestANonStatusErrorIsUnknown(t *testing.T) {
	got := FromRPC(context.Background(), errors.New("boom"))
	if got.Kind != KindAPI || got.Code != codes.Unknown || got.Detail != "boom" {
		t.Fatalf("got %+v", got)
	}
}

func TestTheContextIsTheCauseOnlyWhenItEnded(t *testing.T) {
	live := context.Background()
	if got := FromRPC(live, status.Error(codes.DeadlineExceeded, "server said so")); got.Cause != nil || errors.Is(got, context.DeadlineExceeded) {
		t.Errorf("a server-sent deadline is not the caller's: %+v", got)
	}

	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	got := FromRPC(cancelled, status.FromContextError(cancelled.Err()).Err())
	if got.Code != codes.Canceled || !errors.Is(got, context.Canceled) {
		t.Errorf("cancelled: %+v", got)
	}

	expired, stop := context.WithTimeout(context.Background(), 0)
	defer stop()
	<-expired.Done()
	got = FromRPC(expired, status.FromContextError(expired.Err()).Err())
	if got.Code != codes.DeadlineExceeded || !errors.Is(got, context.DeadlineExceeded) {
		t.Errorf("expired: %+v", got)
	}

	// An ended context does not claim a failure that has another code.
	if got := FromRPC(cancelled, status.Error(codes.NotFound, "gone")); got.Cause != nil {
		t.Errorf("not-found claimed the context: %+v", got)
	}
}

func TestFromContextNamesHowTheContextEnded(t *testing.T) {
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	if got := FromContext(cancelled); got.Code != codes.Canceled || !errors.Is(got, context.Canceled) {
		t.Errorf("cancelled: %+v", got)
	}
	expired, stop := context.WithTimeout(context.Background(), 0)
	defer stop()
	<-expired.Done()
	if got := FromContext(expired); got.Code != codes.DeadlineExceeded || !errors.Is(got, context.DeadlineExceeded) {
		t.Errorf("expired: %+v", got)
	}
}
