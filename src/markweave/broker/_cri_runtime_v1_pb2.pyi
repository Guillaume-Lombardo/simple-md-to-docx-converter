# ruff: noqa
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

PodSandboxState: _enum_type_wrapper.EnumTypeWrapper
ContainerState: _enum_type_wrapper.EnumTypeWrapper
SANDBOX_READY: int
SANDBOX_NOTREADY: int
CONTAINER_CREATED: int
CONTAINER_RUNNING: int
CONTAINER_EXITED: int
CONTAINER_UNKNOWN: int

class VersionRequest(_message.Message):
    __slots__ = ("version",)
    VERSION_FIELD_NUMBER: _ClassVar[int]
    version: str
    def __init__(self, version: _Optional[str] = ...) -> None: ...

class VersionResponse(_message.Message):
    __slots__ = ("version", "runtime_name", "runtime_version", "runtime_api_version")
    VERSION_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_NAME_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_VERSION_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_API_VERSION_FIELD_NUMBER: _ClassVar[int]
    version: str
    runtime_name: str
    runtime_version: str
    runtime_api_version: str
    def __init__(
        self,
        version: _Optional[str] = ...,
        runtime_name: _Optional[str] = ...,
        runtime_version: _Optional[str] = ...,
        runtime_api_version: _Optional[str] = ...,
    ) -> None: ...

class PodSandboxMetadata(_message.Message):
    __slots__ = ("name", "uid", "namespace", "attempt")
    NAME_FIELD_NUMBER: _ClassVar[int]
    UID_FIELD_NUMBER: _ClassVar[int]
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    ATTEMPT_FIELD_NUMBER: _ClassVar[int]
    name: str
    uid: str
    namespace: str
    attempt: int
    def __init__(
        self,
        name: _Optional[str] = ...,
        uid: _Optional[str] = ...,
        namespace: _Optional[str] = ...,
        attempt: _Optional[int] = ...,
    ) -> None: ...

class PodSandboxStatusRequest(_message.Message):
    __slots__ = ("pod_sandbox_id", "verbose")
    POD_SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    VERBOSE_FIELD_NUMBER: _ClassVar[int]
    pod_sandbox_id: str
    verbose: bool
    def __init__(
        self, pod_sandbox_id: _Optional[str] = ..., verbose: bool = ...
    ) -> None: ...

class PodSandboxStatus(_message.Message):
    __slots__ = ("id", "metadata", "state", "labels")
    class LabelsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(
            self, key: _Optional[str] = ..., value: _Optional[str] = ...
        ) -> None: ...

    ID_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    id: str
    metadata: PodSandboxMetadata
    state: int
    labels: _containers.ScalarMap[str, str]
    def __init__(
        self,
        id: _Optional[str] = ...,
        metadata: _Optional[_Union[PodSandboxMetadata, _Mapping]] = ...,
        state: _Optional[_Union[int, str]] = ...,
        labels: _Optional[_Mapping[str, str]] = ...,
    ) -> None: ...

class PodSandboxStatusResponse(_message.Message):
    __slots__ = ("status", "info")
    class InfoEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(
            self, key: _Optional[str] = ..., value: _Optional[str] = ...
        ) -> None: ...

    STATUS_FIELD_NUMBER: _ClassVar[int]
    INFO_FIELD_NUMBER: _ClassVar[int]
    status: PodSandboxStatus
    info: _containers.ScalarMap[str, str]
    def __init__(
        self,
        status: _Optional[_Union[PodSandboxStatus, _Mapping]] = ...,
        info: _Optional[_Mapping[str, str]] = ...,
    ) -> None: ...

class PodSandboxFilter(_message.Message):
    __slots__ = ("id", "label_selector")
    class LabelSelectorEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(
            self, key: _Optional[str] = ..., value: _Optional[str] = ...
        ) -> None: ...

    ID_FIELD_NUMBER: _ClassVar[int]
    LABEL_SELECTOR_FIELD_NUMBER: _ClassVar[int]
    id: str
    label_selector: _containers.ScalarMap[str, str]
    def __init__(
        self,
        id: _Optional[str] = ...,
        label_selector: _Optional[_Mapping[str, str]] = ...,
    ) -> None: ...

class ListPodSandboxRequest(_message.Message):
    __slots__ = ("filter",)
    FILTER_FIELD_NUMBER: _ClassVar[int]
    filter: PodSandboxFilter
    def __init__(
        self, filter: _Optional[_Union[PodSandboxFilter, _Mapping]] = ...
    ) -> None: ...

class PodSandbox(_message.Message):
    __slots__ = ("id", "metadata", "state", "labels")
    class LabelsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(
            self, key: _Optional[str] = ..., value: _Optional[str] = ...
        ) -> None: ...

    ID_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    id: str
    metadata: PodSandboxMetadata
    state: int
    labels: _containers.ScalarMap[str, str]
    def __init__(
        self,
        id: _Optional[str] = ...,
        metadata: _Optional[_Union[PodSandboxMetadata, _Mapping]] = ...,
        state: _Optional[_Union[int, str]] = ...,
        labels: _Optional[_Mapping[str, str]] = ...,
    ) -> None: ...

class ListPodSandboxResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[PodSandbox]
    def __init__(
        self, items: _Optional[_Iterable[_Union[PodSandbox, _Mapping]]] = ...
    ) -> None: ...

class ContainerFilter(_message.Message):
    __slots__ = ("id", "pod_sandbox_id", "label_selector")
    class LabelSelectorEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(
            self, key: _Optional[str] = ..., value: _Optional[str] = ...
        ) -> None: ...

    ID_FIELD_NUMBER: _ClassVar[int]
    POD_SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    LABEL_SELECTOR_FIELD_NUMBER: _ClassVar[int]
    id: str
    pod_sandbox_id: str
    label_selector: _containers.ScalarMap[str, str]
    def __init__(
        self,
        id: _Optional[str] = ...,
        pod_sandbox_id: _Optional[str] = ...,
        label_selector: _Optional[_Mapping[str, str]] = ...,
    ) -> None: ...

class ListContainersRequest(_message.Message):
    __slots__ = ("filter",)
    FILTER_FIELD_NUMBER: _ClassVar[int]
    filter: ContainerFilter
    def __init__(
        self, filter: _Optional[_Union[ContainerFilter, _Mapping]] = ...
    ) -> None: ...

class Container(_message.Message):
    __slots__ = ("id", "pod_sandbox_id", "state", "labels")
    class LabelsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(
            self, key: _Optional[str] = ..., value: _Optional[str] = ...
        ) -> None: ...

    ID_FIELD_NUMBER: _ClassVar[int]
    POD_SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    id: str
    pod_sandbox_id: str
    state: int
    labels: _containers.ScalarMap[str, str]
    def __init__(
        self,
        id: _Optional[str] = ...,
        pod_sandbox_id: _Optional[str] = ...,
        state: _Optional[_Union[int, str]] = ...,
        labels: _Optional[_Mapping[str, str]] = ...,
    ) -> None: ...

class ListContainersResponse(_message.Message):
    __slots__ = ("containers",)
    CONTAINERS_FIELD_NUMBER: _ClassVar[int]
    containers: _containers.RepeatedCompositeFieldContainer[Container]
    def __init__(
        self, containers: _Optional[_Iterable[_Union[Container, _Mapping]]] = ...
    ) -> None: ...

class ContainerStatusRequest(_message.Message):
    __slots__ = ("container_id", "verbose")
    CONTAINER_ID_FIELD_NUMBER: _ClassVar[int]
    VERBOSE_FIELD_NUMBER: _ClassVar[int]
    container_id: str
    verbose: bool
    def __init__(
        self, container_id: _Optional[str] = ..., verbose: bool = ...
    ) -> None: ...

class ContainerStatus(_message.Message):
    __slots__ = ("id", "state", "labels")
    class LabelsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(
            self, key: _Optional[str] = ..., value: _Optional[str] = ...
        ) -> None: ...

    ID_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    id: str
    state: int
    labels: _containers.ScalarMap[str, str]
    def __init__(
        self,
        id: _Optional[str] = ...,
        state: _Optional[_Union[int, str]] = ...,
        labels: _Optional[_Mapping[str, str]] = ...,
    ) -> None: ...

class ContainerStatusResponse(_message.Message):
    __slots__ = ("status", "info")
    class InfoEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(
            self, key: _Optional[str] = ..., value: _Optional[str] = ...
        ) -> None: ...

    STATUS_FIELD_NUMBER: _ClassVar[int]
    INFO_FIELD_NUMBER: _ClassVar[int]
    status: ContainerStatus
    info: _containers.ScalarMap[str, str]
    def __init__(
        self,
        status: _Optional[_Union[ContainerStatus, _Mapping]] = ...,
        info: _Optional[_Mapping[str, str]] = ...,
    ) -> None: ...
