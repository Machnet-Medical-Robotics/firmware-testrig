"""
hardware_daemon/mock_daemon.py
Python mock of the Hardware Daemon gRPC server.

PURPOSE:
  Replaces the real C++ Hardware Daemon during development and CI.
  Implements the exact same gRPC service contract so the Worker, Controller
  and all tests work without physical hardware.

MOCK BEHAVIOUR:
  - Ping:               always alive
  - CheckDeviceReady:   simulates a short boot delay, then returns ready
                        with board_identity = configured identity
  - DiscoverCapabilities: returns hardcoded shuttle assembly channels/commands
  - SendCommand:        looks up command in COMMAND_RESPONSES table,
                        returns configured response string
  - WaitForChannel:     simulates a ramp to the midpoint of [min, max],
                        always returns condition_met=True within timeout
  - StreamTelemetry:    emits synthetic sine/ramp frames continuously
  - RebootDevice:       sleeps briefly, returns OK
  - ApplyBoardConfig:   returns CONFIG_APPLIED immediately

EXTENDING FOR NEW ASSEMBLIES:
  Add entries to COMMAND_RESPONSES and CHANNEL_REGISTRY at the bottom
  of this file. No structural changes needed.

RUNNING:
  python -m hardware_daemon.mock_daemon [--port 50051] [--board-identity shuttle]
"""

from __future__ import annotations

import argparse
import logging
import math
import time
import threading
from concurrent import futures
from typing import Iterator

import grpc

from shared.proto import hardware_daemon_pb2 as pb2
from shared.proto import hardware_daemon_pb2_grpc as pb2_grpc

logger = logging.getLogger("mock_daemon")


# ============================================================================
# Mock data registry
#
# To add a new assembly:
#   1. Add its commands to COMMAND_RESPONSES
#   2. Add its channels to CHANNEL_REGISTRY
#   3. No other changes needed
# ============================================================================

# command_name → response string the mock returns
# Covers the shuttle assembly + system-level commands
COMMAND_RESPONSES: dict[str, str] = {
    # System
    "com_enter_service_mode":     "cmd OK",
    "com_change_mode":            "cmd OK",

    # Shuttle leadscrew
    "on_demand_leadscrew":        "BIST SUCCESS",
    "com_leadscrew_go":           "cmd OK",

    # --- Add new assembly commands here ---
    # "on_demand_front_clamp":    "BIST SUCCESS",
    # "com_clamp_open":           "cmd OK",
    # "com_clamp_close":          "cmd OK",
}

# channel_name → (num_fields, description, mock_value_fn)
# mock_value_fn(offset, t_seconds) → float   (t = seconds since daemon start)
def _ramp(target: float):
    """Returns a function that ramps from 0 to target over 3 seconds."""
    def fn(offset: int, t: float) -> float:
        if offset == 0:  # position
            return min(target, (target / 3.0) * t)
        if offset == 1:  # speed — peaks then falls
            return max(0.0, target * 0.3 * (1.0 - abs(t - 1.5) / 1.5))
        return 0.0
    return fn

CHANNEL_REGISTRY: dict[str, tuple[int, str, callable]] = {
    # stepper1_controller: 6 fields
    #   [0]=position_rev [1]=speed_rps [2]=accel [3]=setpoint [4]=speed_sp [5]=encoder
    # stepper1_controller uses stateful position tracking (see _channel_position below)
    # The value_fn is replaced at runtime by MockHardwareDaemonServicer._get_channel_value
    "stepper1_controller": (
        6,
        "Shuttle leadscrew stepper position/velocity/accel",
        lambda offset, t: 0.0,   # placeholder — overridden by servicer
    ),
    # stepper1_ic: 2 fields  [0]=driver_status  [1]=global_status
    "stepper1_ic": (
        2,
        "Shuttle leadscrew stepper IC driver registers",
        lambda offset, t: 0.0,   # 0 = no faults
    ),

    # --- Add new assembly channels here ---
    # "front_clamp_state": (1, "Front clamp position sensor", lambda o, t: 1.0),
}

# Channels available on each device (device_id → list of channel names)
DEVICE_CHANNELS: dict[str, list[str]] = {
    "monitoring_pcb": list(CHANNEL_REGISTRY.keys()),
    "robot_pcb":      list(CHANNEL_REGISTRY.keys()),  # direct UART verification
}

# Commands available on each device
DEVICE_COMMANDS: dict[str, list[str]] = {
    "monitoring_pcb": list(COMMAND_RESPONSES.keys()),
    "robot_pcb":      list(COMMAND_RESPONSES.keys()),
}


# ============================================================================
# Service implementation
# ============================================================================

class MockHardwareDaemonServicer(pb2_grpc.HardwareDaemonServicer):

    def __init__(self, board_identity: str = "shuttle", boot_delay_ms: int = 500):
        self._board_identity    = board_identity
        self._boot_delay_ms     = boot_delay_ms
        self._start_time        = time.monotonic()
        self._reboot_events:    dict[str, threading.Event] = {}
        # Stateful position simulation per channel
        # When a com_leadscrew_go command is received, we update the target
        # and record the move start time so the ramp goes to the new target.
        self._move_start_time:  float = time.monotonic()
        self._move_from:        float = 0.0
        self._move_target:      float = 0.0
        logger.info(
            "MockDaemon initialised | identity=%s boot_delay=%dms",
            board_identity, boot_delay_ms,
        )

    def _elapsed_s(self) -> float:
        return time.monotonic() - self._start_time

    def _get_channel_value(self, channel: str, offset: int) -> float:
        """
        Stateful channel value simulation.
        stepper1_controller[0] ramps from _move_from to _move_target
        over ~5 seconds from _move_start_time.
        All other offsets/channels use simple static values.
        """
        if channel == "stepper1_controller":
            elapsed = time.monotonic() - self._move_start_time
            ramp_s  = 5.0
            progress = min(1.0, elapsed / ramp_s)
            position = self._move_from + (self._move_target - self._move_from) * progress
            if offset == 0:   return position           # position
            if offset == 1:   return abs(self._move_target - self._move_from) * 0.2 * (1.0 - abs(progress - 0.5) * 2)  # speed bell
            if offset == 2:   return 0.5               # accel
            if offset == 3:   return self._move_target # setpoint
            if offset == 4:   return 2.0               # speed setpoint
            if offset == 5:   return position          # encoder
        return 0.0

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def Ping(self, request, context):
        logger.debug("Ping")
        return pb2.PingResponse(alive=True, daemon_version="mock-0.1")

    def CheckDeviceReady(self, request, context):
        device   = request.device_id
        wait_ms  = self._boot_delay_ms
        logger.info("CheckDeviceReady | device=%s | simulating %dms boot delay", device, wait_ms)

        time.sleep(wait_ms / 1000.0)

        return pb2.DeviceReadyResponse(
            ready=True,
            status=pb2.DEVICE_STATUS_OK,
            detail=f"Device {device} ready (mock)",
            board_identity=self._board_identity,
        )

    # -----------------------------------------------------------------------
    # Discovery
    # -----------------------------------------------------------------------

    def DiscoverCapabilities(self, request, context):
        device = request.device_id
        logger.info("DiscoverCapabilities | device=%s", device)

        channel_names = DEVICE_CHANNELS.get(device, [])
        command_names = DEVICE_COMMANDS.get(device, [])

        channels = [
            pb2.DiscoveredChannel(
                name=name,
                num_fields=CHANNEL_REGISTRY[name][0],
                description=CHANNEL_REGISTRY[name][1],
            )
            for name in channel_names
            if name in CHANNEL_REGISTRY
        ]

        commands = [
            pb2.DiscoveredCommand(
                name=name,
                has_params=(name in ("com_leadscrew_go", "com_change_mode")),
                description=f"GScope command: {name}",
            )
            for name in command_names
            if name in COMMAND_RESPONSES
        ]

        raw = (
            f"CHANNELS: {','.join(c.name for c in channels)}\n"
            f"COMMANDS: {','.join(c.name for c in commands)}"
        )

        logger.info(
            "DiscoverCapabilities | found %d channels, %d commands",
            len(channels), len(commands),
        )

        return pb2.DiscoverResponse(
            status=pb2.DEVICE_STATUS_OK,
            detail="Discovery complete (mock)",
            channels=channels,
            commands=commands,
            raw_response=raw,
        )

    # -----------------------------------------------------------------------
    # Command execution
    # -----------------------------------------------------------------------

    def SendCommand(self, request, context):
        cmd    = request.command
        param  = request.command_param
        match  = request.return_string_match
        t_ms   = request.timeout_ms

        full_cmd = f"{cmd} {param}".strip()
        logger.info(
            "SendCommand | device=%s cmd='%s' match='%s' timeout=%dms",
            request.device_id, full_cmd, match, t_ms,
        )

        # Simulate a short execution time
        time.sleep(0.05)

        response = COMMAND_RESPONSES.get(cmd)

        if response is None:
            logger.warning("SendCommand | unknown command '%s'", cmd)
            return pb2.CommandResponse(
                status=pb2.DEVICE_STATUS_ERROR,
                matched=False,
                actual_response="",
                duration_ms=50,
                detail=f"Unknown command '{cmd}'",
            )

        # Intercept motion commands to update stateful position simulation
        if cmd == "com_leadscrew_go" and param:
            try:
                parts  = param.strip().split()
                target = float(parts[0])
                # Current position becomes new move_from
                current = self._get_channel_value("stepper1_controller", 0)
                self._move_from       = current
                self._move_target     = target
                self._move_start_time = time.monotonic()
                logger.info(
                    "SendCommand | leadscrew move: %.1f → %.1f",
                    current, target,
                )
            except (ValueError, IndexError):
                pass  # malformed param — ignore, let the normal response flow

        matched = (match == "") or (match in response)
        # IMPORTANT: status is always OK here — the command executed and the device
        # responded. A response mismatch (matched=False) is a TEST failure, not an
        # infrastructure error. status=ERROR is reserved for UART/daemon failures.

        logger.info(
            "SendCommand | response='%s' matched=%s", response, matched
        )

        return pb2.CommandResponse(
            status=pb2.DEVICE_STATUS_OK,
            matched=matched,
            actual_response=response,
            duration_ms=50,
            detail="" if matched else f"Expected '{match}' not in '{response}'",
        )

    # -----------------------------------------------------------------------
    # Channel monitoring
    # -----------------------------------------------------------------------

    def WaitForChannel(self, request, context):
        channel = request.channel_name
        offset  = request.channel_offset
        lo      = request.min_value
        hi      = request.max_value
        t_ms    = request.timeout_ms

        logger.info(
            "WaitForChannel | device=%s channel=%s[%d] range=[%.3f, %.3f] timeout=%dms",
            request.device_id, channel, offset, lo, hi, t_ms,
        )

        if channel not in CHANNEL_REGISTRY:
            logger.error("WaitForChannel | unknown channel '%s'", channel)
            return pb2.ChannelWaitResponse(
                status=pb2.DEVICE_STATUS_ERROR,
                condition_met=False,
                last_value=0.0,
                duration_ms=0,
                detail=f"Unknown channel '{channel}'",
            )

        _, _, value_fn = CHANNEL_REGISTRY[channel]

        # Poll until condition met or timeout
        deadline    = time.monotonic() + (t_ms / 1000.0)
        poll_interval = 0.05   # 50ms polling
        last_value  = 0.0
        elapsed_ms  = 0

        while time.monotonic() < deadline:
            last_value = self._get_channel_value(channel, offset)

            if lo <= last_value <= hi:
                elapsed_ms = int((time.monotonic() - (deadline - t_ms / 1000.0)) * 1000)
                logger.info(
                    "WaitForChannel | PASSED channel=%s[%d] value=%.3f in [%.3f, %.3f] after %dms",
                    channel, offset, last_value, lo, hi, elapsed_ms,
                )
                return pb2.ChannelWaitResponse(
                    status=pb2.DEVICE_STATUS_OK,
                    condition_met=True,
                    last_value=last_value,
                    duration_ms=elapsed_ms,
                    detail="Condition met",
                )

            time.sleep(poll_interval)

        elapsed_ms = t_ms
        logger.warning(
            "WaitForChannel | TIMEOUT channel=%s[%d] last_value=%.3f range=[%.3f, %.3f]",
            channel, offset, last_value, lo, hi,
        )
        return pb2.ChannelWaitResponse(
            status=pb2.DEVICE_STATUS_TIMEOUT,
            condition_met=False,
            last_value=last_value,
            duration_ms=elapsed_ms,
            detail=f"Timeout: last value {last_value:.3f} not in [{lo}, {hi}]",
        )

    # -----------------------------------------------------------------------
    # Telemetry streaming
    # -----------------------------------------------------------------------

    def StreamTelemetry(self, request, context):
        channel = request.channel_name
        logger.info(
            "StreamTelemetry | device=%s channel=%s", request.device_id, channel
        )

        if channel not in CHANNEL_REGISTRY:
            logger.warning("StreamTelemetry | unknown channel '%s'", channel)
            return

        num_fields, _, value_fn = CHANNEL_REGISTRY[channel]

        while context.is_active():
            vals  = [self._get_channel_value(channel, i) for i in range(num_fields)]
            ts_ms = int(self._elapsed_s() * 1000)

            yield pb2.TelemetryFrame(
                channel_name=channel,
                values=vals,
                timestamp_ms=ts_ms,
            )
            time.sleep(0.01)   # 100 Hz

    # -----------------------------------------------------------------------
    # Board management
    # -----------------------------------------------------------------------

    def RebootDevice(self, request, context):
        device = request.device_id
        method = request.method or "power_cycle"
        logger.info("RebootDevice | device=%s method=%s", device, method)

        # Simulate reboot command being issued
        time.sleep(0.1)

        # Reset position state to 0 (board comes back up at home position)
        self._move_from       = 0.0
        self._move_target     = 0.0
        self._move_start_time = time.monotonic() + (self._boot_delay_ms / 1000.0)
        self._start_time      = time.monotonic() + (self._boot_delay_ms / 1000.0)

        logger.info("RebootDevice | command issued, board will be ready in ~%dms", self._boot_delay_ms)
        return pb2.RebootResponse(
            status=pb2.DEVICE_STATUS_OK,
            detail=f"Reboot command issued via {method} (mock)",
        )

    def ApplyBoardConfig(self, request, context):
        byte = request.config_byte
        logger.info("ApplyBoardConfig | config_byte=0x%02X (%d)", byte, byte)

        # Simulate ESP32 ACK delay
        time.sleep(0.05)

        return pb2.BoardConfigResponse(
            status=pb2.DEVICE_STATUS_OK,
            detail="CONFIG_APPLIED",
        )


# ============================================================================
# Server runner
# ============================================================================

def serve(port: int = 50051, board_identity: str = "shuttle", boot_delay_ms: int = 500):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    pb2_grpc.add_HardwareDaemonServicer_to_server(
        MockHardwareDaemonServicer(
            board_identity=board_identity,
            boot_delay_ms=boot_delay_ms,
        ),
        server,
    )
    address = f"[::]:{port}"
    server.add_insecure_port(address)
    server.start()
    logger.info("MockDaemon listening on %s | identity=%s", address, board_identity)
    return server


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(description="TestRig Mock Hardware Daemon")
    parser.add_argument("--port",           type=int,   default=50051)
    parser.add_argument("--board-identity", type=str,   default="shuttle")
    parser.add_argument("--boot-delay-ms",  type=int,   default=500)
    args = parser.parse_args()

    server = serve(
        port=args.port,
        board_identity=args.board_identity,
        boot_delay_ms=args.boot_delay_ms,
    )
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down MockDaemon")
        server.stop(grace=1)
