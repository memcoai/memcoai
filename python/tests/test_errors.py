"""Status-code translation and the RESOURCE_EXHAUSTED discriminator."""

import grpc
import pytest

from memco import errors


class FakeRpcError(grpc.RpcError):  # type: ignore[misc]
    """Stands in for a real RpcError; grpc's own type cannot be built by hand."""

    def __init__(self, code: grpc.StatusCode, details: str) -> None:
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details

    def debug_error_string(self) -> str:
        return "debug-string"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (grpc.StatusCode.UNAUTHENTICATED, errors.MemcoAuthenticationError),
        (grpc.StatusCode.PERMISSION_DENIED, errors.MemcoPermissionError),
        (grpc.StatusCode.INVALID_ARGUMENT, errors.MemcoInvalidRequestError),
        (grpc.StatusCode.NOT_FOUND, errors.MemcoNotFoundError),
        (grpc.StatusCode.RESOURCE_EXHAUSTED, errors.MemcoResourceExhaustedError),
        (grpc.StatusCode.UNAVAILABLE, errors.MemcoUnavailableError),
        (grpc.StatusCode.DEADLINE_EXCEEDED, errors.MemcoTimeoutError),
        (grpc.StatusCode.INTERNAL, errors.MemcoInternalError),
        (grpc.StatusCode.UNKNOWN, errors.MemcoInternalError),
        (grpc.StatusCode.DATA_LOSS, errors.MemcoInternalError),
    ],
)
def test_each_status_code_maps_to_its_class(code, expected):
    err = errors.from_rpc_error(FakeRpcError(code, "boom"))
    assert type(err) is expected
    assert err.code is code
    assert err.message == "boom"
    assert err.debug_error_string == "debug-string"


def test_every_api_error_is_a_memco_error():
    err = errors.from_rpc_error(FakeRpcError(grpc.StatusCode.INTERNAL, "x"))
    assert isinstance(err, errors.MemcoAPIError)
    assert isinstance(err, errors.MemcoError)


def test_unhealthy_is_catchable_as_unavailable():
    assert issubclass(errors.MemcoUnhealthyError, errors.MemcoUnavailableError)


@pytest.mark.parametrize(
    ("details", "kind"),
    [
        ("rate limit exceeded", errors.ResourceExhaustedKind.RATE_LIMIT),
        ("RATE LIMIT EXCEEDED", errors.ResourceExhaustedKind.RATE_LIMIT),
        (
            "You've reached your daily search limit, try again tomorrow",
            errors.ResourceExhaustedKind.QUOTA,
        ),
        ("something else entirely", errors.ResourceExhaustedKind.UNKNOWN),
    ],
)
def test_resource_exhausted_kind_heuristic(details, kind):
    err = errors.from_rpc_error(FakeRpcError(grpc.StatusCode.RESOURCE_EXHAUSTED, details))
    # from_rpc_error is declared as returning the base class; catching or
    # asserting the subclass is what makes .kind visible to a type checker.
    assert isinstance(err, errors.MemcoResourceExhaustedError)
    assert err.kind is kind


def test_only_resource_exhausted_carries_a_kind():
    err = errors.from_rpc_error(FakeRpcError(grpc.StatusCode.NOT_FOUND, "nope"))
    assert not hasattr(err, "kind")


def test_config_error_is_not_an_api_error():
    assert issubclass(errors.MemcoConfigError, errors.MemcoError)
    assert not issubclass(errors.MemcoConfigError, errors.MemcoAPIError)
