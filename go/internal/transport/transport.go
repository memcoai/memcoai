// Package transport dials the service: TLS, the credential, the user agent and
// the retry policy each service declares.
package transport

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/backoff"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/metadata"

	"github.com/memcoai/memcoai/go/internal/config"
	"github.com/memcoai/memcoai/go/internal/fault"
)

const (
	AuthHeader            = "authorization"
	AuthScheme            = "Bearer "
	UnauthenticatedPrefix = "/grpc.health.v1."
)

// Service is one gRPC service and the methods safe to retry on it.
type Service struct {
	Name      string
	Retryable []string
}

type methodName struct {
	Service string `json:"service"`
	Method  string `json:"method"`
}

// retryPolicy retries UNAVAILABLE only: DEADLINE_EXCEEDED is the caller's own
// deadline, and RESOURCE_EXHAUSTED is the caller's to handle by its kind.
var retryPolicy = map[string]any{
	"maxAttempts":          3,
	"initialBackoff":       "0.1s",
	"maxBackoff":           "1s",
	"backoffMultiplier":    2,
	"retryableStatusCodes": []string{"UNAVAILABLE"},
}

// ServiceConfig is the retry policy for the given services, as gRPC's JSON.
// The health probe is retried too, so a blip while connecting is absorbed.
func ServiceConfig(services ...Service) string {
	var names []methodName
	for _, service := range services {
		for _, method := range service.Retryable {
			names = append(names, methodName{service.Name, method})
		}
	}
	names = append(names, methodName{grpc_health_v1.Health_ServiceDesc.ServiceName, "Check"})
	encoded, err := json.Marshal(map[string]any{
		"methodConfig": []map[string]any{{"name": names, "retryPolicy": retryPolicy}},
	})
	if err != nil {
		panic(err) // static data
	}
	return string(encoded)
}

// ConnectParams are the reconnect settings every connection uses. gRPC's
// default backoff caps at two minutes, which leaves a client idle through a
// short outage unusable long after the service is back.
func ConnectParams() grpc.ConnectParams {
	params := backoff.DefaultConfig
	params.BaseDelay = 200 * time.Millisecond
	params.MaxDelay = 5 * time.Second
	// Set explicitly: left at zero, a dial may take no longer than the backoff.
	return grpc.ConnectParams{Backoff: params, MinConnectTimeout: 20 * time.Second}
}

// Dial opens a connection without sending anything.
func Dial(cfg *config.Config, userAgent string, services ...Service) (*grpc.ClientConn, error) {
	creds := insecure.NewCredentials()
	if cfg.TLS {
		creds = credentials.NewTLS(&tls.Config{MinVersion: tls.VersionTLS12})
	}
	conn, err := grpc.NewClient(cfg.DialTarget(),
		grpc.WithTransportCredentials(creds),
		grpc.WithUserAgent(userAgent),
		grpc.WithUnaryInterceptor(authenticate(cfg.Credential)),
		// The SDK's policy, not one a resolver publishes: the dns resolver would
		// otherwise read a service config from TXT records.
		grpc.WithDisableServiceConfig(),
		grpc.WithDefaultServiceConfig(ServiceConfig(services...)),
		grpc.WithConnectParams(ConnectParams()),
	)
	if err != nil {
		return nil, fault.Config(fmt.Sprintf("cannot dial %s: %v", cfg.Target(), err))
	}
	return conn, nil
}

// authenticate attaches the credential to every call but the health probe,
// replacing any the caller's context carries.
func authenticate(credential *config.Credential) grpc.UnaryClientInterceptor {
	return func(ctx context.Context, method string, req, reply any, cc *grpc.ClientConn,
		invoker grpc.UnaryInvoker, opts ...grpc.CallOption) error {
		md, _ := metadata.FromOutgoingContext(ctx)
		md = md.Copy()
		if strings.HasPrefix(method, UnauthenticatedPrefix) {
			md.Delete(AuthHeader)
		} else {
			md.Set(AuthHeader, AuthScheme+credential.Reveal())
		}
		return invoker(metadata.NewOutgoingContext(ctx, md), method, req, reply, cc, opts...)
	}
}

// Health probes the server as a whole.
func Health(ctx context.Context, conn grpc.ClientConnInterface) (grpc_health_v1.HealthCheckResponse_ServingStatus, error) {
	response, err := grpc_health_v1.NewHealthClient(conn).Check(ctx, &grpc_health_v1.HealthCheckRequest{})
	if err != nil {
		return grpc_health_v1.HealthCheckResponse_UNKNOWN, fault.FromRPC(ctx, err)
	}
	return response.GetStatus(), nil
}

// StatusName spells a serving status, including one this build does not know.
func StatusName(s grpc_health_v1.HealthCheckResponse_ServingStatus) string {
	if name, ok := grpc_health_v1.HealthCheckResponse_ServingStatus_name[int32(s)]; ok {
		return name
	}
	return fmt.Sprintf("UNRECOGNIZED(%d)", s)
}
