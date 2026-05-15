"""
tests/test_mock_daemon.py
Smoke test for the Mock Hardware Daemon.

Starts the server in a background thread, runs every RPC method via the
typed DaemonClient, asserts expected responses, then shuts down.

Run with:
    python -m tests.test_mock_daemon
"""

import logging
import sys
import time
import threading

from hardware_daemon.mock_daemon import serve
from shared.proto.client import DaemonClient, DeviceStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("smoke_test")

PORT = 50099   # Use non-default port to avoid conflicts


def run_smoke_test():
    # -----------------------------------------------------------------------
    # Start mock daemon in background thread
    # -----------------------------------------------------------------------
    logger.info("Starting MockDaemon on port %d", PORT)
    server = serve(port=PORT, board_identity="shuttle", boot_delay_ms=100)
    time.sleep(0.2)   # give server a moment to bind

    passed = 0
    failed = 0

    def ok(label: str):
        nonlocal passed
        passed += 1
        logger.info("[PASS] %s", label)

    def fail(label: str, detail: str):
        nonlocal failed
        failed += 1
        logger.error("[FAIL] %s — %s", label, detail)

    # -----------------------------------------------------------------------
    # Connect
    # -----------------------------------------------------------------------
    with DaemonClient(f"localhost:{PORT}") as client:

        # 1. Ping
        result = client.ping()
        if result.alive and result.daemon_version == "mock-0.1":
            ok("Ping → alive, version=mock-0.1")
        else:
            fail("Ping", f"alive={result.alive} version={result.daemon_version}")

        # 2. CheckDeviceReady — monitoring_pcb
        result = client.check_device_ready("monitoring_pcb", timeout_ms=2000)
        if result.ready and result.board_identity == "shuttle":
            ok(f"CheckDeviceReady(monitoring_pcb) → ready, identity='{result.board_identity}'")
        else:
            fail("CheckDeviceReady", f"ready={result.ready} identity={result.board_identity}")

        # 3. CheckDeviceReady — robot_pcb
        result = client.check_device_ready("robot_pcb", timeout_ms=2000)
        if result.ready:
            ok("CheckDeviceReady(robot_pcb) → ready")
        else:
            fail("CheckDeviceReady(robot_pcb)", f"ready={result.ready}")

        # 4. DiscoverCapabilities
        result = client.discover_capabilities("monitoring_pcb")
        expected_channels = {"stepper1_controller", "stepper1_ic"}
        expected_commands = {
            "com_enter_service_mode", "com_change_mode",
            "on_demand_leadscrew", "com_leadscrew_go",
        }
        found_channels = {c.name for c in result.channels}
        found_commands = {c.name for c in result.commands}

        if expected_channels.issubset(found_channels):
            ok(f"DiscoverCapabilities → channels: {sorted(found_channels)}")
        else:
            fail("DiscoverCapabilities channels",
                 f"missing {expected_channels - found_channels}")

        if expected_commands.issubset(found_commands):
            ok(f"DiscoverCapabilities → commands: {sorted(found_commands)}")
        else:
            fail("DiscoverCapabilities commands",
                 f"missing {expected_commands - found_commands}")

        # 5. Channel num_fields
        ch = next((c for c in result.channels if c.name == "stepper1_controller"), None)
        if ch and ch.num_fields == 6:
            ok("stepper1_controller has 6 fields")
        else:
            fail("stepper1_controller num_fields", f"got {ch.num_fields if ch else 'None'}")

        # 6. SendCommand — enter service mode
        result = client.send_command(
            "monitoring_pcb", "com_enter_service_mode",
            return_string_match="cmd OK", timeout_ms=3000,
        )
        if result.matched and result.actual_response == "cmd OK":
            ok("SendCommand(com_enter_service_mode) → matched 'cmd OK'")
        else:
            fail("SendCommand(com_enter_service_mode)",
                 f"matched={result.matched} response='{result.actual_response}'")

        # 7. SendCommand — on_demand_leadscrew BIST
        result = client.send_command(
            "monitoring_pcb", "on_demand_leadscrew",
            return_string_match="BIST SUCCESS", timeout_ms=15000,
        )
        if result.matched:
            ok("SendCommand(on_demand_leadscrew) → matched 'BIST SUCCESS'")
        else:
            fail("SendCommand(on_demand_leadscrew)", f"response='{result.actual_response}'")

        # 8. SendCommand — com_leadscrew_go with param
        result = client.send_command(
            "monitoring_pcb", "com_leadscrew_go",
            command_param="100 350",
            return_string_match="cmd OK", timeout_ms=5000,
        )
        if result.matched:
            ok("SendCommand(com_leadscrew_go 100 350) → matched 'cmd OK'")
        else:
            fail("SendCommand(com_leadscrew_go)", f"response='{result.actual_response}'")

        # 9. SendCommand — unknown command returns error
        result = client.send_command(
            "monitoring_pcb", "nonexistent_command",
            return_string_match="cmd OK", timeout_ms=1000,
        )
        if result.status == DeviceStatus.ERROR and not result.matched:
            ok("SendCommand(unknown_command) → correctly returns ERROR")
        else:
            fail("SendCommand(unknown)", f"status={result.status} matched={result.matched}")

        # 10. WaitForChannel — wait for leadscrew to reach setpoint
        # The mock ramps stepper1_controller[0] to 100.0 over ~5s
        # We wait for it to enter [99.0, 101.0]
        result = client.wait_for_channel(
            "monitoring_pcb",
            channel_name="stepper1_controller",
            channel_offset=0,
            min_value=99.0,
            max_value=101.0,
            timeout_ms=10000,
        )
        if result.condition_met and 99.0 <= result.last_value <= 101.0:
            ok(f"WaitForChannel(stepper1_controller[0]) → condition_met, value={result.last_value:.3f}")
        else:
            fail("WaitForChannel", f"met={result.condition_met} value={result.last_value:.3f}")

        # 11. WaitForChannel — return to 0 range
        # After ramp completes, test the [min, max] of a small range near 0
        # We pass min=0, max=0.5 and the mock starts from 0 on next reboot
        result = client.reboot_device("robot_pcb")
        if result.status == DeviceStatus.OK:
            ok("RebootDevice(robot_pcb) → OK")
        else:
            fail("RebootDevice", f"status={result.status} detail={result.detail}")

        time.sleep(0.15)  # wait for reboot delay to pass

        result = client.wait_for_channel(
            "monitoring_pcb",
            channel_name="stepper1_controller",
            channel_offset=0,
            min_value=0.0,
            max_value=5.0,
            timeout_ms=5000,
        )
        if result.condition_met:
            ok(f"WaitForChannel after reboot → near 0, value={result.last_value:.3f}")
        else:
            fail("WaitForChannel after reboot",
                 f"met={result.condition_met} value={result.last_value:.3f}")

        # 12. WaitForChannel — unknown channel returns error
        result = client.wait_for_channel(
            "monitoring_pcb", "nonexistent_channel",
            channel_offset=0, min_value=0.0, max_value=1.0, timeout_ms=1000,
        )
        if result.status == DeviceStatus.ERROR and not result.condition_met:
            ok("WaitForChannel(unknown_channel) → correctly returns ERROR")
        else:
            fail("WaitForChannel(unknown)", f"status={result.status}")

        # 13. StreamTelemetry — read 5 frames
        frames = []
        stream = client.stream_telemetry("monitoring_pcb", "stepper1_controller")
        try:
            for frame in stream:
                frames.append(frame)
                if len(frames) >= 5:
                    stream.cancel()
                    break
        except Exception:
            pass

        if len(frames) >= 5 and all(len(f.values) == 6 for f in frames):
            ok(f"StreamTelemetry → received {len(frames)} frames, each with 6 values")
        else:
            fail("StreamTelemetry", f"frames={len(frames)}")

        # 14. ApplyBoardConfig
        result = client.apply_board_config(config_byte=0xA3, timeout_ms=2000)
        if result.status == DeviceStatus.OK and result.detail == "CONFIG_APPLIED":
            ok(f"ApplyBoardConfig(0xA3) → CONFIG_APPLIED")
        else:
            fail("ApplyBoardConfig", f"status={result.status} detail={result.detail}")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    total = passed + failed
    print(f"\n{'='*55}")
    print(f"  Phase 3 Smoke Test: {passed}/{total} passed")
    print(f"{'='*55}")

    server.stop(grace=0)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_smoke_test()
