// Package transport dials the service: TLS, the user agent and the retry
// policy each service declares; and holds the credential each call carries.
package transport

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
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
	AuthHeader = "authorization"
	AuthScheme = "Bearer "
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

// Bearer returns ctx carrying exactly one credential, whatever ctx carried.
// The credential is an argument of each call rather than state of the
// connection: a client holds several at once, its own and a key per
// impersonated session, and a call made without one carries none.
func Bearer(ctx context.Context, credential *config.Credential) context.Context {
	md, _ := metadata.FromOutgoingContext(ctx)
	md = md.Copy()
	md.Set(AuthHeader, AuthScheme+credential.Reveal())
	return metadata.NewOutgoingContext(ctx, md)
}

// Anonymous returns ctx carrying no credential, for the health probe and the
// token exchange.
func Anonymous(ctx context.Context) context.Context {
	md, _ := metadata.FromOutgoingContext(ctx)
	md = md.Copy()
	md.Delete(AuthHeader)
	return metadata.NewOutgoingContext(ctx, md)
}

// Health probes the server as a whole.
func Health(ctx context.Context, conn grpc.ClientConnInterface) (grpc_health_v1.HealthCheckResponse_ServingStatus, error) {
	response, err := grpc_health_v1.NewHealthClient(conn).Check(Anonymous(ctx), &grpc_health_v1.HealthCheckRequest{})
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
