from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class IssueTokenRequest(_message.Message):
    __slots__ = ("grant_type", "client_id", "client_secret", "scope", "ttl_seconds")
    GRANT_TYPE_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    CLIENT_SECRET_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    TTL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    grant_type: str
    client_id: str
    client_secret: str
    scope: _containers.RepeatedScalarFieldContainer[str]
    ttl_seconds: int
    def __init__(self, grant_type: _Optional[str] = ..., client_id: _Optional[str] = ..., client_secret: _Optional[str] = ..., scope: _Optional[_Iterable[str]] = ..., ttl_seconds: _Optional[int] = ...) -> None: ...

class IssueTokenResponse(_message.Message):
    __slots__ = ("access_token", "token_type", "expires_in", "scope")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    TOKEN_TYPE_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_IN_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    token_type: str
    expires_in: int
    scope: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, access_token: _Optional[str] = ..., token_type: _Optional[str] = ..., expires_in: _Optional[int] = ..., scope: _Optional[_Iterable[str]] = ...) -> None: ...
