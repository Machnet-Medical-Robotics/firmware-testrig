"""
tests/test_phase2_proto.py
Phase 2 smoke test — validates:
  1. Proto compiled cleanly and all enums/messages importable
  2. Client wrapper builds correct proto messages
  3. Every RPC method reachable against a minimal in-process server stub
  4. Return type dataclasses populate correctly

Run:
    cd testrig
    PYTHONPATH=. .venv/bin/python3 tests/test_phase2_proto.py
"""

import sys
import time
import threading
from concurrent import futures

import grpc

from shared.proto import hardware_daemon_pb2 as pb2
from shared.proto import hardware_daemon_pb2_grpc as pb2_grpc
from shared.proto.client import HardwareDaemonClient
from shared.enums import DeviceTarget

TEST_PORT = 50099   # Use non-standard port so it doesn't clash with real daemon


# ---------------------------------------------------------------------------
# Minimal in-process server — returns hardcoded responses for every RPC.
# This is NOT the mock daemon (that's Phase 3). This just validates the
# proto contract and client wrapper are wired up correctly.
# ---------------------------------------------------------------------------

class _MinimalServicer(pb2_grpc.HardwareDaemonServicer):

    def GetDaemonStatus(self, request, context):
        return pb2.GetDaemonStatusResponse(
            status=pb2.DAEMON_OK,
            connected_devices=["monitoring_pcb"],
            detail="smoke test daemon",
        )

    def CheckDeviceReady(self, request, context):
        return pb2.CheckDeviceReadyResponse(
            status=pb2.DEVICE_READY,
            detail="boot complete, HB stable",
        )

    def DiscoverBoard(self, request, context):
        return pb2.DiscoverBoardResponse(
            board_identity="shuttle",
            channels=[
                pb2.DiscoveredChannel(name="stepper1_controller", num_fields=6,
                                      description="pos/vel/accel controller data"),
                pb2.DiscoveredChannel(name="stepper1_ic",         num_fields=2,
                                      description="IC driver status registers"),
            ],
            commands=[
                pb2.DiscoveredCommand(name="on_demand_leadscrew",    has_params=False),
                pb2.DiscoveredCommand(name="com_leadscrew_go",       has_params=True),
                pb2.DiscoveredCommand(name="com_change_mode",        has_params=True),
                pb2.DiscoveredCommand(name="com_enter_service_mode", has_params=False),
            ],
            raw_response="[SMOKE] discovery response",
        )

    def SendCommand(self, request, context):
        matched = (not request.return_string_match or
                   request.return_string_match in "cmd OK")
        return pb2.SendCommandResponse(
            status=pb2.COMMAND_OK if matched else pb2.COMMAND_NO_MATCH,
            actual_response="cmd OK",
            elapsed_ms=12,
        )

    def ChannelWait(self, request, context):
        return pb2.ChannelWaitResponse(
            status=pb2.CHANNEL_CONDITION_MET,
            final_value=(request.min_value + request.max_value) / 2.0,
            elapsed_ms=250,
        )

    def ReadChannel(self, request, context):
        return pb2.ReadChannelResponse(
            success=True,
            value=100.0,
        )

    def StreamTelemetry(self, request, context):
        for i in range(3):
            yield pb2.TelemetryFrame(
                channel_name=request.channel_name,
                values=[float(i), float(i) * 0.1],
                timestamp_ms=int(time.time() * 1000) + i * 10,
            )


def _start_server() -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    pb2_grpc.add_HardwareDaemonServicer_to_server(_MinimalServicer(), server)
    server.add_insecure_port(f"[::]:{TEST_PORT}")
    server.start()
    return server


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def run_tests():
    ok = 0
    fail = 0

    def check(label: str, condition: bool, detail: str = ""):
        nonlocal ok, fail
        if condition:
            print(f"  [OK]   {label}")
            ok += 1
        else:
            print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
            fail += 1

    print("\n=== Phase 2 — Proto + Client smoke test ===\n")

    # 1. Proto imports
    print("1. Proto compilation")
    check("pb2 module importable",          pb2 is not None)
    check("DeviceTarget enum exists",       hasattr(pb2, "MONITORING_PCB"))
    check("CommandStatus enum exists",      hasattr(pb2, "COMMAND_OK"))
    check("ChannelStatus enum exists",      hasattr(pb2, "CHANNEL_CONDITION_MET"))
    check("ReadyStatus enum exists",        hasattr(pb2, "DEVICE_READY"))
    check("DaemonStatus enum exists",       hasattr(pb2, "DAEMON_OK"))
    check("SendCommandRequest exists",      hasattr(pb2, "SendCommandRequest"))
    check("ChannelWaitRequest exists",      hasattr(pb2, "ChannelWaitRequest"))
    check("DiscoverBoardRequest exists",    hasattr(pb2, "DiscoverBoardRequest"))
    check("StreamTelemetryRequest exists",  hasattr(pb2, "StreamTelemetryRequest"))

    # 2. Start server
    print("\n2. In-process server startup")
    server = _start_server()
    time.sleep(0.1)
    check("server started", True)

    # 3. Client wrapper
    print("\n3. Client RPC calls")
    with HardwareDaemonClient(f"localhost:{TEST_PORT}") as client:

        # GetDaemonStatus
        s = client.get_daemon_status()
        check("GetDaemonStatus → ok",               s.ok)
        check("GetDaemonStatus → connected_devices", "monitoring_pcb" in s.connected_devices)

        # CheckDeviceReady
        r = client.check_device_ready(DeviceTarget.MONITORING_PCB, timeout_ms=5000)
        check("CheckDeviceReady → ready",   r.ready)
        check("CheckDeviceReady → detail",  "boot" in r.detail)

        # DiscoverBoard
        d = client.discover_board(DeviceTarget.MONITORING_PCB, timeout_ms=5000)
        check("DiscoverBoard → identity",               d.board_identity == "shuttle")
        check("DiscoverBoard → channels count",         len(d.channels) == 2)
        check("DiscoverBoard → has stepper1_controller",d.has_channel("stepper1_controller"))
        check("DiscoverBoard → stepper1 num_fields=6",  d.channel_num_fields("stepper1_controller") == 6)
        check("DiscoverBoard → has com_leadscrew_go",   d.has_command("com_leadscrew_go"))
        check("DiscoverBoard → missing channel → False",not d.has_channel("nonexistent"))

        # SendCommand — with match
        c = client.send_command(
            command="com_leadscrew_go",
            command_param="100 350",
            return_string_match="cmd OK",
            timeout_ms=5000,
        )
        check("SendCommand → ok",              c.ok)
        check("SendCommand → status name",     c.status_name == "COMMAND_OK")
        check("SendCommand → actual response", "OK" in c.actual_response)
        check("SendCommand → elapsed_ms",      c.elapsed_ms >= 0)

        # ChannelWait
        cw = client.channel_wait(
            channel_name="stepper1_controller",
            channel_offset=0,
            min_value=99.9,
            max_value=100.1,
            timeout_ms=15000,
        )
        check("ChannelWait → ok",           cw.ok)
        check("ChannelWait → status name",  cw.status_name == "CHANNEL_CONDITION_MET")
        check("ChannelWait → final_value",  99.9 <= cw.final_value <= 100.1,
              f"final_value={cw.final_value}")

        # ReadChannel
        rc = client.read_channel("stepper1_controller", channel_offset=0)
        check("ReadChannel → ok",    rc.ok)
        check("ReadChannel → value", rc.value == 100.0)

        # StreamTelemetry
        frames = list(client.stream_telemetry("stepper1_controller"))
        check("StreamTelemetry → 3 frames",       len(frames) == 3)
        check("StreamTelemetry → frame structure", len(frames[0][0]) == 2)

    server.stop(grace=0)
    print(f"\n=== {ok} passed, {fail} failed ===")
    return fail == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
