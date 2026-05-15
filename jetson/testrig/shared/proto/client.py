"""
shared/proto/client.py
Typed wrapper around the raw gRPC stubs.

The Worker and Controller import from here — never from pb2 directly.
This keeps proto internals isolated: if the proto changes, only this
file and the mock need updating, not every caller.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Iterator, List, Optional

import grpc
from shared.proto import hardware_daemon_pb2 as pb2
from shared.proto import hardware_daemon_pb2_grpc as pb2_grpc


# ---------------------------------------------------------------------------
# Result dataclasses — typed returns from each RPC
# ---------------------------------------------------------------------------

@dataclass
class PingResult:
    alive: bool
    daemon_version: str


@dataclass
class DeviceReadyResult:
    ready: bool
    status: int           # DeviceStatus enum value
    detail: str
    board_identity: str   # e.g. "shuttle"


@dataclass
class DiscoveredChannel:
    name: str
    num_fields: int
    description: str = ""


@dataclass
class DiscoveredCommand:
    name: str
    has_params: bool = False
    description: str = ""


@dataclass
class DiscoverResult:
    status: int
    detail: str
    channels: List[DiscoveredChannel] = field(default_factory=list)
    commands: List[DiscoveredCommand] = field(default_factory=list)
    raw_response: str = ""

    def has_channel(self, name: str) -> bool:
        return any(c.name == name for c in self.channels)

    def has_command(self, name: str) -> bool:
        return any(c.name == name for c in self.commands)

    def channel_num_fields(self, name: str) -> Optional[int]:
        for c in self.channels:
            if c.name == name:
                return c.num_fields
        return None


@dataclass
class CommandResult:
    status: int
    matched: bool
    actual_response: str
    duration_ms: int
    detail: str


@dataclass
class ChannelWaitResult:
    status: int
    condition_met: bool
    last_value: float
    duration_ms: int
    detail: str


@dataclass
class RebootResult:
    status: int
    detail: str


@dataclass
class BoardConfigResult:
    status: int
    detail: str


# ---------------------------------------------------------------------------
# DeviceStatus constants (mirrors proto enum)
# Import these instead of magic ints throughout the codebase.
# ---------------------------------------------------------------------------

class DeviceStatus:
    UNKNOWN   = 0
    OK        = 1
    ERROR     = 2
    TIMEOUT   = 3
    NOT_READY = 4


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class DaemonClient:
    """
    Typed gRPC client for the Hardware Daemon.

    Usage:
        client = DaemonClient("localhost:50051")
        client.connect()
        result = client.ping()
        client.close()

    Or as a context manager:
        with DaemonClient("localhost:50051") as client:
            result = client.ping()
    """

    def __init__(self, address: str = "localhost:50051"):
        self._address = address
        self._channel: Optional[grpc.Channel] = None
        self._stub: Optional[pb2_grpc.HardwareDaemonStub] = None

    def connect(self) -> None:
        self._channel = grpc.insecure_channel(self._address)
        self._stub = pb2_grpc.HardwareDaemonStub(self._channel)

    def close(self) -> None:
        if self._channel:
            self._channel.close()

    def __enter__(self) -> "DaemonClient":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def _stub_or_raise(self):
        if self._stub is None:
            raise RuntimeError("DaemonClient not connected. Call connect() first.")
        return self._stub

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def ping(self) -> PingResult:
        resp = self._stub_or_raise().Ping(pb2.PingRequest())
        return PingResult(alive=resp.alive, daemon_version=resp.daemon_version)

    def check_device_ready(
        self, device_id: str, timeout_ms: int = 5000
    ) -> DeviceReadyResult:
        resp = self._stub_or_raise().CheckDeviceReady(
            pb2.DeviceReadyRequest(device_id=device_id, timeout_ms=timeout_ms)
        )
        return DeviceReadyResult(
            ready=resp.ready,
            status=resp.status,
            detail=resp.detail,
            board_identity=resp.board_identity,
        )

    # -----------------------------------------------------------------------
    # Discovery
    # -----------------------------------------------------------------------

    def discover_capabilities(self, device_id: str) -> DiscoverResult:
        resp = self._stub_or_raise().DiscoverCapabilities(
            pb2.DiscoverRequest(device_id=device_id)
        )
        return DiscoverResult(
            status=resp.status,
            detail=resp.detail,
            channels=[
                DiscoveredChannel(
                    name=c.name,
                    num_fields=c.num_fields,
                    description=c.description,
                )
                for c in resp.channels
            ],
            commands=[
                DiscoveredCommand(
                    name=c.name,
                    has_params=c.has_params,
                    description=c.description,
                )
                for c in resp.commands
            ],
            raw_response=resp.raw_response,
        )

    # -----------------------------------------------------------------------
    # Command execution
    # -----------------------------------------------------------------------

    def send_command(
        self,
        device_id: str,
        command: str,
        command_param: str = "",
        return_string_match: str = "",
        timeout_ms: int = 5000,
    ) -> CommandResult:
        resp = self._stub_or_raise().SendCommand(
            pb2.CommandRequest(
                device_id=device_id,
                command=command,
                command_param=command_param,
                return_string_match=return_string_match,
                timeout_ms=timeout_ms,
            )
        )
        return CommandResult(
            status=resp.status,
            matched=resp.matched,
            actual_response=resp.actual_response,
            duration_ms=resp.duration_ms,
            detail=resp.detail,
        )

    # -----------------------------------------------------------------------
    # Channel monitoring
    # -----------------------------------------------------------------------

    def wait_for_channel(
        self,
        device_id: str,
        channel_name: str,
        channel_offset: int,
        min_value: float,
        max_value: float,
        timeout_ms: int = 60000,
    ) -> ChannelWaitResult:
        resp = self._stub_or_raise().WaitForChannel(
            pb2.ChannelWaitRequest(
                device_id=device_id,
                channel_name=channel_name,
                channel_offset=channel_offset,
                min_value=min_value,
                max_value=max_value,
                timeout_ms=timeout_ms,
            )
        )
        return ChannelWaitResult(
            status=resp.status,
            condition_met=resp.condition_met,
            last_value=resp.last_value,
            duration_ms=resp.duration_ms,
            detail=resp.detail,
        )

    def stream_telemetry(
        self, device_id: str, channel_name: str
    ) -> Iterator[pb2.TelemetryFrame]:
        return self._stub_or_raise().StreamTelemetry(
            pb2.TelemetryRequest(device_id=device_id, channel_name=channel_name)
        )

    # -----------------------------------------------------------------------
    # Board management
    # -----------------------------------------------------------------------

    def reboot_device(
        self, device_id: str, method: str = ""
    ) -> RebootResult:
        resp = self._stub_or_raise().RebootDevice(
            pb2.RebootRequest(device_id=device_id, method=method)
        )
        return RebootResult(status=resp.status, detail=resp.detail)

    def apply_board_config(
        self, config_byte: int, timeout_ms: int = 5000
    ) -> BoardConfigResult:
        resp = self._stub_or_raise().ApplyBoardConfig(
            pb2.BoardConfigRequest(config_byte=config_byte, timeout_ms=timeout_ms)
        )
        return BoardConfigResult(status=resp.status, detail=resp.detail)
