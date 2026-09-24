from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Network(_message.Message):
    __slots__ = ("id", "name", "parent_id", "domain", "region", "scope", "owner", "description")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    PARENT_ID_FIELD_NUMBER: _ClassVar[int]
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    REGION_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    parent_id: str
    domain: str
    region: str
    scope: str
    owner: str
    description: str
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., parent_id: _Optional[str] = ..., domain: _Optional[str] = ..., region: _Optional[str] = ..., scope: _Optional[str] = ..., owner: _Optional[str] = ..., description: _Optional[str] = ...) -> None: ...

class ListNetworksRequest(_message.Message):
    __slots__ = ("name", "scope", "owner", "page", "page_size", "domain", "parent_id", "ids")
    NAME_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    PARENT_ID_FIELD_NUMBER: _ClassVar[int]
    IDS_FIELD_NUMBER: _ClassVar[int]
    name: str
    scope: str
    owner: str
    page: int
    page_size: int
    domain: str
    parent_id: str
    ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, name: _Optional[str] = ..., scope: _Optional[str] = ..., owner: _Optional[str] = ..., page: _Optional[int] = ..., page_size: _Optional[int] = ..., domain: _Optional[str] = ..., parent_id: _Optional[str] = ..., ids: _Optional[_Iterable[str]] = ...) -> None: ...

class ListNetworksResponse(_message.Message):
    __slots__ = ("networks", "total_count")
    NETWORKS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_COUNT_FIELD_NUMBER: _ClassVar[int]
    networks: _containers.RepeatedCompositeFieldContainer[Network]
    total_count: int
    def __init__(self, networks: _Optional[_Iterable[_Union[Network, _Mapping]]] = ..., total_count: _Optional[int] = ...) -> None: ...

class CreateNetworkRequest(_message.Message):
    __slots__ = ("name", "parent_id", "domain", "region", "scope", "owner", "description")
    NAME_FIELD_NUMBER: _ClassVar[int]
    PARENT_ID_FIELD_NUMBER: _ClassVar[int]
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    REGION_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    name: str
    parent_id: str
    domain: str
    region: str
    scope: str
    owner: str
    description: str
    def __init__(self, name: _Optional[str] = ..., parent_id: _Optional[str] = ..., domain: _Optional[str] = ..., region: _Optional[str] = ..., scope: _Optional[str] = ..., owner: _Optional[str] = ..., description: _Optional[str] = ...) -> None: ...

class UpdateNetworkRequest(_message.Message):
    __slots__ = ("id", "name", "parent_id", "scope", "owner", "description")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    PARENT_ID_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    OWNER_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    parent_id: str
    scope: str
    owner: str
    description: str
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., parent_id: _Optional[str] = ..., scope: _Optional[str] = ..., owner: _Optional[str] = ..., description: _Optional[str] = ...) -> None: ...

class DeleteNetworkRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class DeleteNetworkResponse(_message.Message):
    __slots__ = ("id", "removed")
    class RemovedEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    ID_FIELD_NUMBER: _ClassVar[int]
    REMOVED_FIELD_NUMBER: _ClassVar[int]
    id: str
    removed: _containers.ScalarMap[str, int]
    def __init__(self, id: _Optional[str] = ..., removed: _Optional[_Mapping[str, int]] = ...) -> None: ...

class Member(_message.Message):
    __slots__ = ("user_id", "email", "name")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    email: str
    name: str
    def __init__(self, user_id: _Optional[str] = ..., email: _Optional[str] = ..., name: _Optional[str] = ...) -> None: ...

class ListNetworkMembersRequest(_message.Message):
    __slots__ = ("id", "search", "page", "page_size")
    ID_FIELD_NUMBER: _ClassVar[int]
    SEARCH_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    id: str
    search: str
    page: int
    page_size: int
    def __init__(self, id: _Optional[str] = ..., search: _Optional[str] = ..., page: _Optional[int] = ..., page_size: _Optional[int] = ...) -> None: ...

class ListNetworkMembersResponse(_message.Message):
    __slots__ = ("members", "total_count")
    MEMBERS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_COUNT_FIELD_NUMBER: _ClassVar[int]
    members: _containers.RepeatedCompositeFieldContainer[Member]
    total_count: int
    def __init__(self, members: _Optional[_Iterable[_Union[Member, _Mapping]]] = ..., total_count: _Optional[int] = ...) -> None: ...

class AddNetworkMemberRequest(_message.Message):
    __slots__ = ("id", "user_id", "force")
    ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    FORCE_FIELD_NUMBER: _ClassVar[int]
    id: str
    user_id: str
    force: bool
    def __init__(self, id: _Optional[str] = ..., user_id: _Optional[str] = ..., force: bool = ...) -> None: ...

class AddNetworkMemberResponse(_message.Message):
    __slots__ = ("id", "user_id", "moved_from")
    ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    MOVED_FROM_FIELD_NUMBER: _ClassVar[int]
    id: str
    user_id: str
    moved_from: str
    def __init__(self, id: _Optional[str] = ..., user_id: _Optional[str] = ..., moved_from: _Optional[str] = ...) -> None: ...

class RemoveNetworkMemberRequest(_message.Message):
    __slots__ = ("id", "user_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    user_id: str
    def __init__(self, id: _Optional[str] = ..., user_id: _Optional[str] = ...) -> None: ...

class RemoveNetworkMemberResponse(_message.Message):
    __slots__ = ("id", "user_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    user_id: str
    def __init__(self, id: _Optional[str] = ..., user_id: _Optional[str] = ...) -> None: ...

class Group(_message.Message):
    __slots__ = ("id", "name", "memory_network_id", "member_count")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    MEMORY_NETWORK_ID_FIELD_NUMBER: _ClassVar[int]
    MEMBER_COUNT_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    memory_network_id: str
    member_count: int
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., memory_network_id: _Optional[str] = ..., member_count: _Optional[int] = ...) -> None: ...

class ListGroupsRequest(_message.Message):
    __slots__ = ("name", "network_id", "ids", "page", "page_size")
    NAME_FIELD_NUMBER: _ClassVar[int]
    NETWORK_ID_FIELD_NUMBER: _ClassVar[int]
    IDS_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    name: str
    network_id: str
    ids: _containers.RepeatedScalarFieldContainer[str]
    page: int
    page_size: int
    def __init__(self, name: _Optional[str] = ..., network_id: _Optional[str] = ..., ids: _Optional[_Iterable[str]] = ..., page: _Optional[int] = ..., page_size: _Optional[int] = ...) -> None: ...

class ListGroupsResponse(_message.Message):
    __slots__ = ("groups", "total_count")
    GROUPS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_COUNT_FIELD_NUMBER: _ClassVar[int]
    groups: _containers.RepeatedCompositeFieldContainer[Group]
    total_count: int
    def __init__(self, groups: _Optional[_Iterable[_Union[Group, _Mapping]]] = ..., total_count: _Optional[int] = ...) -> None: ...

class ListGroupMembersRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class ListGroupMembersResponse(_message.Message):
    __slots__ = ("members",)
    MEMBERS_FIELD_NUMBER: _ClassVar[int]
    members: _containers.RepeatedCompositeFieldContainer[Member]
    def __init__(self, members: _Optional[_Iterable[_Union[Member, _Mapping]]] = ...) -> None: ...

class AddNetworkGroupRequest(_message.Message):
    __slots__ = ("id", "group_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    GROUP_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    group_id: str
    def __init__(self, id: _Optional[str] = ..., group_id: _Optional[str] = ...) -> None: ...

class AddNetworkGroupResponse(_message.Message):
    __slots__ = ("id", "group_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    GROUP_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    group_id: str
    def __init__(self, id: _Optional[str] = ..., group_id: _Optional[str] = ...) -> None: ...

class RemoveNetworkGroupRequest(_message.Message):
    __slots__ = ("id", "group_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    GROUP_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    group_id: str
    def __init__(self, id: _Optional[str] = ..., group_id: _Optional[str] = ...) -> None: ...

class RemoveNetworkGroupResponse(_message.Message):
    __slots__ = ("id", "group_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    GROUP_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    group_id: str
    def __init__(self, id: _Optional[str] = ..., group_id: _Optional[str] = ...) -> None: ...

class ExternalUser(_message.Message):
    __slots__ = ("id", "external_id", "name", "email", "roles", "active")
    ID_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    ROLES_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_FIELD_NUMBER: _ClassVar[int]
    id: str
    external_id: str
    name: str
    email: str
    roles: _containers.RepeatedScalarFieldContainer[str]
    active: bool
    def __init__(self, id: _Optional[str] = ..., external_id: _Optional[str] = ..., name: _Optional[str] = ..., email: _Optional[str] = ..., roles: _Optional[_Iterable[str]] = ..., active: bool = ...) -> None: ...

class ListExternalUsersRequest(_message.Message):
    __slots__ = ("search", "page", "page_size")
    SEARCH_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    search: str
    page: int
    page_size: int
    def __init__(self, search: _Optional[str] = ..., page: _Optional[int] = ..., page_size: _Optional[int] = ...) -> None: ...

class ListExternalUsersResponse(_message.Message):
    __slots__ = ("external_users", "total_count")
    EXTERNAL_USERS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_COUNT_FIELD_NUMBER: _ClassVar[int]
    external_users: _containers.RepeatedCompositeFieldContainer[ExternalUser]
    total_count: int
    def __init__(self, external_users: _Optional[_Iterable[_Union[ExternalUser, _Mapping]]] = ..., total_count: _Optional[int] = ...) -> None: ...

class GetExternalUserRequest(_message.Message):
    __slots__ = ("external_id",)
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    external_id: str
    def __init__(self, external_id: _Optional[str] = ...) -> None: ...

class CreateExternalUserRequest(_message.Message):
    __slots__ = ("external_id", "name", "email", "roles")
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    ROLES_FIELD_NUMBER: _ClassVar[int]
    external_id: str
    name: str
    email: str
    roles: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, external_id: _Optional[str] = ..., name: _Optional[str] = ..., email: _Optional[str] = ..., roles: _Optional[_Iterable[str]] = ...) -> None: ...

class UpdateExternalUserRequest(_message.Message):
    __slots__ = ("external_id", "name", "email", "roles")
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    ROLES_FIELD_NUMBER: _ClassVar[int]
    external_id: str
    name: str
    email: str
    roles: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, external_id: _Optional[str] = ..., name: _Optional[str] = ..., email: _Optional[str] = ..., roles: _Optional[_Iterable[str]] = ...) -> None: ...

class DeleteExternalUserRequest(_message.Message):
    __slots__ = ("external_id",)
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    external_id: str
    def __init__(self, external_id: _Optional[str] = ...) -> None: ...

class DeleteExternalUserResponse(_message.Message):
    __slots__ = ("external_id",)
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    external_id: str
    def __init__(self, external_id: _Optional[str] = ...) -> None: ...

class ExternalUserKey(_message.Message):
    __slots__ = ("id", "name", "value_prefix", "roles", "scopes", "valid_until")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    VALUE_PREFIX_FIELD_NUMBER: _ClassVar[int]
    ROLES_FIELD_NUMBER: _ClassVar[int]
    SCOPES_FIELD_NUMBER: _ClassVar[int]
    VALID_UNTIL_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    value_prefix: str
    roles: _containers.RepeatedScalarFieldContainer[str]
    scopes: _containers.RepeatedScalarFieldContainer[str]
    valid_until: int
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., value_prefix: _Optional[str] = ..., roles: _Optional[_Iterable[str]] = ..., scopes: _Optional[_Iterable[str]] = ..., valid_until: _Optional[int] = ...) -> None: ...

class ListExternalUserKeysRequest(_message.Message):
    __slots__ = ("external_id",)
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    external_id: str
    def __init__(self, external_id: _Optional[str] = ...) -> None: ...

class ListExternalUserKeysResponse(_message.Message):
    __slots__ = ("keys",)
    KEYS_FIELD_NUMBER: _ClassVar[int]
    keys: _containers.RepeatedCompositeFieldContainer[ExternalUserKey]
    def __init__(self, keys: _Optional[_Iterable[_Union[ExternalUserKey, _Mapping]]] = ...) -> None: ...

class CreateExternalUserKeyRequest(_message.Message):
    __slots__ = ("external_id", "name", "preset", "valid_until")
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    PRESET_FIELD_NUMBER: _ClassVar[int]
    VALID_UNTIL_FIELD_NUMBER: _ClassVar[int]
    external_id: str
    name: str
    preset: str
    valid_until: int
    def __init__(self, external_id: _Optional[str] = ..., name: _Optional[str] = ..., preset: _Optional[str] = ..., valid_until: _Optional[int] = ...) -> None: ...

class CreateExternalUserKeyResponse(_message.Message):
    __slots__ = ("key", "value")
    KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    key: ExternalUserKey
    value: str
    def __init__(self, key: _Optional[_Union[ExternalUserKey, _Mapping]] = ..., value: _Optional[str] = ...) -> None: ...

class DeleteExternalUserKeyRequest(_message.Message):
    __slots__ = ("external_id", "key_id")
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    KEY_ID_FIELD_NUMBER: _ClassVar[int]
    external_id: str
    key_id: str
    def __init__(self, external_id: _Optional[str] = ..., key_id: _Optional[str] = ...) -> None: ...

class DeleteExternalUserKeyResponse(_message.Message):
    __slots__ = ("external_id", "key_id")
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    KEY_ID_FIELD_NUMBER: _ClassVar[int]
    external_id: str
    key_id: str
    def __init__(self, external_id: _Optional[str] = ..., key_id: _Optional[str] = ...) -> None: ...

class ImpersonateExternalUserRequest(_message.Message):
    __slots__ = ("external_id", "ttl_minutes")
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    TTL_MINUTES_FIELD_NUMBER: _ClassVar[int]
    external_id: str
    ttl_minutes: int
    def __init__(self, external_id: _Optional[str] = ..., ttl_minutes: _Optional[int] = ...) -> None: ...

class ImpersonationKey(_message.Message):
    __slots__ = ("value", "expires_at", "roles", "scopes", "key_id")
    VALUE_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    ROLES_FIELD_NUMBER: _ClassVar[int]
    SCOPES_FIELD_NUMBER: _ClassVar[int]
    KEY_ID_FIELD_NUMBER: _ClassVar[int]
    value: str
    expires_at: int
    roles: _containers.RepeatedScalarFieldContainer[str]
    scopes: _containers.RepeatedScalarFieldContainer[str]
    key_id: str
    def __init__(self, value: _Optional[str] = ..., expires_at: _Optional[int] = ..., roles: _Optional[_Iterable[str]] = ..., scopes: _Optional[_Iterable[str]] = ..., key_id: _Optional[str] = ...) -> None: ...

class EndImpersonationRequest(_message.Message):
    __slots__ = ("external_id", "key_id")
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    KEY_ID_FIELD_NUMBER: _ClassVar[int]
    external_id: str
    key_id: str
    def __init__(self, external_id: _Optional[str] = ..., key_id: _Optional[str] = ...) -> None: ...

class EndImpersonationResponse(_message.Message):
    __slots__ = ("external_id", "key_id")
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    KEY_ID_FIELD_NUMBER: _ClassVar[int]
    external_id: str
    key_id: str
    def __init__(self, external_id: _Optional[str] = ..., key_id: _Optional[str] = ...) -> None: ...
