"""
tools/grpc_monitor.py
Interactive gRPC channel inspector for the Hardware Daemon.

Lets you:
  - Ping the daemon
  - Discover what channels and commands are available on a device
  - Subscribe to live telemetry from any channel (streaming)
  - Send one-off commands and see the response
  - Watch a channel until a condition is met

This is a development/debugging tool. It uses the same DaemonClient
as the Worker, so you can test the daemon independently of any TestRun.

Usage (daemon must be running first — python run_daemon.py):

  # Interactive menu:
  python -m tools.grpc_monitor

  # One-shot commands:
  python -m tools.grpc_monitor ping
  python -m tools.grpc_monitor discover
  python -m tools.grpc_monitor stream stepper1_controller
  python -m tools.grpc_monitor send com_leadscrew_go "100 350"
  python -m tools.grpc_monitor watch stepper1_controller 0 99.0 101.0

  # Different daemon address:
  python -m tools.grpc_monitor --daemon localhost:50052 ping
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# gRPC PROTOCOL OVERVIEW — what this tool lets you observe
# ---------------------------------------------------------------------------
#
# The Hardware Daemon exposes two gRPC communication patterns:
#
# 1. UNARY RPCs (request → single response)
#    Used for: Ping, CheckDeviceReady, DiscoverCapabilities, SendCommand,
#              WaitForChannel, RebootDevice, ApplyBoardConfig
#    Pattern:  client sends one message, daemon processes it, returns one reply.
#    Example:  SendCommand("com_leadscrew_go", "100 350") → {"matched": true}
#
# 2. SERVER-STREAMING RPC (request → continuous stream of responses)
#    Used for: StreamTelemetry ONLY
#    Pattern:  client sends one message, daemon sends back a continuous
#              stream of TelemetryFrame messages until the client cancels.
#    Example:  StreamTelemetry("stepper1_controller") →
#                TelemetryFrame(values=[0.0, 0.0, ...], ts=0)
#                TelemetryFrame(values=[5.2, 0.1, ...], ts=100)
#                TelemetryFrame(values=[10.4, 0.1, ...], ts=200)
#                ...  (100Hz, until you press Ctrl+C)
#
# There is NO pub/sub in this system. pub/sub implies a broker (like MQTT
# or Redis) where publishers and subscribers are decoupled. gRPC streaming
# is a direct client→server connection — when the client disconnects, the
# stream ends. The distinction matters:
#   pub/sub:  many publishers, many subscribers, decoupled via broker
#   streaming: one client, one server, direct connection
#
# WaitForChannel is NOT streaming — it's a blocking unary RPC. The daemon
# polls internally and sends ONE response when the condition is met or times
# out. The Worker uses this so it doesn't need to implement polling itself.
#
# ---------------------------------------------------------------------------


def get_client(daemon_addr: str):
    from shared.proto.client import DaemonClient
    client = DaemonClient(daemon_addr)
    client.connect()
    return client


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_ping(client, args):
    """Ping the daemon — confirms it is alive."""
    result = client.ping()
    print(f"\n  Daemon alive:   {result.alive}")
    print(f"  Daemon version: {result.daemon_version}\n")


def cmd_discover(client, args):
    """Show all channels and commands available on a device."""
    device = args.device or "monitoring_pcb"
    result = client.discover_capabilities(device)

    print(f"\n  Device: {device}")
    print(f"  Status: {result.status}  {result.detail}")
    print()
    print(f"  Channels ({len(result.channels)}):")
    for ch in result.channels:
        print(f"    {ch.name:<30} {ch.num_fields} fields  — {ch.description}")
    print()
    print(f"  Commands ({len(result.commands)}):")
    for cmd in result.commands:
        params = "(has params)" if cmd.has_params else "(no params)"
        print(f"    {cmd.name:<30} {params}")
    print()


def cmd_stream(client, args):
    """
    Subscribe to live telemetry from a channel.

    This is the STREAMING RPC — the daemon sends TelemetryFrame messages
    continuously at ~100Hz until you press Ctrl+C.
    """
    device  = args.device or "monitoring_pcb"
    channel = args.channel_name
    limit   = getattr(args, "limit", None)

    print(f"\n  Streaming {channel} from {device}")
    print(f"  Press Ctrl+C to stop\n")

    # Discover field count for column header
    disc = client.discover_capabilities(device)
    num_fields = next(
        (c.num_fields for c in disc.channels if c.name == channel), 6
    )
    header = "  ts_ms     " + "  ".join(f"[{i}]" for i in range(num_fields))
    print(header)
    print("  " + "-" * (len(header) - 2))

    count = 0
    try:
        stream = client.stream_telemetry(device, channel)
        for frame in stream:
            vals = "  ".join(f"{v:8.3f}" for v in frame.values)
            print(f"  {frame.timestamp_ms:8d}  {vals}")
            count += 1
            if limit and count >= limit:
                stream.cancel()
                break
    except KeyboardInterrupt:
        print(f"\n  Stopped after {count} frames.")
    except Exception as e:
        if "cancelled" not in str(e).lower():
            print(f"\n  Stream ended: {e}")


def cmd_send(client, args):
    """Send a GScope command and show the response."""
    device  = args.device or "monitoring_pcb"
    command = args.command_name
    param   = args.param or ""
    match   = args.match or ""
    timeout = args.timeout or 5000

    print(f"\n  Sending: {command} {param}".rstrip())
    print(f"  Device:  {device}")
    print(f"  Match:   '{match}' (empty = any response)")
    print()

    result = client.send_command(
        device_id=device,
        command=command,
        command_param=param,
        return_string_match=match,
        timeout_ms=timeout,
    )

    status_str = "OK" if result.status == 1 else f"ERROR ({result.status})"
    matched_str = "YES" if result.matched else "NO"

    print(f"  Status:   {status_str}")
    print(f"  Matched:  {matched_str}")
    print(f"  Response: '{result.actual_response}'")
    print(f"  Duration: {result.duration_ms}ms")
    if result.detail:
        print(f"  Detail:   {result.detail}")
    print()


def cmd_watch(client, args):
    """
    Watch a channel until a value condition is met (blocking unary RPC).

    Unlike streaming, this sends ONE request and the daemon polls internally,
    returning ONE response when condition is met or timeout expires.
    """
    device  = args.device or "monitoring_pcb"
    channel = args.channel_name
    offset  = args.offset
    lo      = args.min_value
    hi      = args.max_value
    timeout = args.timeout or 30000

    print(f"\n  Watching {channel}[{offset}] in [{lo}, {hi}]")
    print(f"  Device:  {device}")
    print(f"  Timeout: {timeout}ms")
    print()

    start = time.monotonic()
    result = client.wait_for_channel(
        device_id=device,
        channel_name=channel,
        channel_offset=offset,
        min_value=lo,
        max_value=hi,
        timeout_ms=timeout,
    )
    wall = time.monotonic() - start

    met_str    = "YES ✓" if result.condition_met else "NO ✗ (timeout)"
    status_str = "OK" if result.status == 1 else f"STATUS={result.status}"

    print(f"  Condition met: {met_str}")
    print(f"  Last value:    {result.last_value:.4f}")
    print(f"  Duration:      {result.duration_ms}ms  (wall: {wall:.2f}s)")
    print(f"  Status:        {status_str}")
    if result.detail:
        print(f"  Detail:        {result.detail}")
    print()


def cmd_ready(client, args):
    """Check device readiness and board identity."""
    device  = args.device or "monitoring_pcb"
    timeout = args.timeout or 5000

    print(f"\n  Checking {device} readiness (timeout={timeout}ms)...")
    result = client.check_device_ready(device, timeout_ms=timeout)

    ready_str = "READY ✓" if result.ready else "NOT READY ✗"
    print(f"\n  Ready:    {ready_str}")
    print(f"  Identity: '{result.board_identity}'")
    print(f"  Detail:   {result.detail}\n")


def cmd_interactive(client, _args):
    """Interactive menu — choose what to inspect."""
    print("\n  TestRig gRPC Monitor")
    print("  " + "─" * 40)
    while True:
        print()
        print("  1) Ping daemon")
        print("  2) Discover capabilities (channels + commands)")
        print("  3) Stream telemetry (live)")
        print("  4) Send command")
        print("  5) Watch channel until condition")
        print("  6) Check device ready")
        print("  q) Quit")
        print()
        choice = input("  Choice: ").strip().lower()

        if choice == "q":
            break
        elif choice == "1":
            cmd_ping(client, None)
        elif choice == "2":
            device = input("  Device [monitoring_pcb]: ").strip() or "monitoring_pcb"

            class A:
                pass
            a = A(); a.device = device
            cmd_discover(client, a)
        elif choice == "3":
            channel = input("  Channel name [stepper1_controller]: ").strip() or "stepper1_controller"
            device  = input("  Device [monitoring_pcb]: ").strip() or "monitoring_pcb"

            class A:
                pass
            a = A(); a.channel_name = channel; a.device = device; a.limit = None
            cmd_stream(client, a)
        elif choice == "4":
            command = input("  Command name: ").strip()
            param   = input("  Params (empty if none): ").strip()
            match   = input("  Match string (empty = any): ").strip()
            device  = input("  Device [monitoring_pcb]: ").strip() or "monitoring_pcb"

            class A:
                pass
            a = A(); a.command_name = command; a.param = param
            a.match = match; a.device = device; a.timeout = 5000
            cmd_send(client, a)
        elif choice == "5":
            channel = input("  Channel name [stepper1_controller]: ").strip() or "stepper1_controller"
            offset  = int(input("  Offset [0]: ").strip() or "0")
            lo      = float(input("  Min value: ").strip())
            hi      = float(input("  Max value: ").strip())
            timeout = int(input("  Timeout ms [10000]: ").strip() or "10000")
            device  = input("  Device [monitoring_pcb]: ").strip() or "monitoring_pcb"

            class A:
                pass
            a = A(); a.channel_name = channel; a.offset = offset
            a.min_value = lo; a.max_value = hi; a.timeout = timeout; a.device = device
            cmd_watch(client, a)
        elif choice == "6":
            device  = input("  Device [monitoring_pcb]: ").strip() or "monitoring_pcb"
            timeout = int(input("  Timeout ms [5000]: ").strip() or "5000")

            class A:
                pass
            a = A(); a.device = device; a.timeout = timeout
            cmd_ready(client, a)
        else:
            print("  Unknown choice.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="TestRig gRPC Monitor — inspect the Hardware Daemon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--daemon", default="localhost:50051",
                        help="Daemon gRPC address (default: localhost:50051)")
    parser.add_argument("--device", default="monitoring_pcb",
                        help="Device to target (default: monitoring_pcb)")

    sub = parser.add_subparsers(dest="subcommand")

    sub.add_parser("ping",     help="Ping daemon")
    sub.add_parser("discover", help="List channels and commands")

    p_stream = sub.add_parser("stream", help="Live telemetry stream")
    p_stream.add_argument("channel_name", help="Channel name e.g. stepper1_controller")
    p_stream.add_argument("--limit", type=int, default=None,
                          help="Stop after N frames (default: unlimited)")

    p_send = sub.add_parser("send", help="Send a command")
    p_send.add_argument("command_name", help="Command e.g. com_leadscrew_go")
    p_send.add_argument("param", nargs="?", default="",
                        help="Command param string e.g. '100 350'")
    p_send.add_argument("--match", default="",
                        help="Response string to match (default: any)")
    p_send.add_argument("--timeout", type=int, default=5000)

    p_watch = sub.add_parser("watch", help="Watch channel until condition")
    p_watch.add_argument("channel_name")
    p_watch.add_argument("offset",    type=int,   help="Channel offset index")
    p_watch.add_argument("min_value", type=float, help="Minimum value (inclusive)")
    p_watch.add_argument("max_value", type=float, help="Maximum value (inclusive)")
    p_watch.add_argument("--timeout", type=int, default=30000)

    p_ready = sub.add_parser("ready", help="Check device readiness")
    p_ready.add_argument("--timeout", type=int, default=5000)

    args = parser.parse_args()

    # Connect
    try:
        client = get_client(args.daemon)
    except Exception as exc:
        print(f"\n  ERROR: Cannot connect to daemon at {args.daemon}")
        print(f"  {exc}")
        print(f"\n  Is the daemon running?  python run_daemon.py\n")
        sys.exit(1)

    try:
        dispatch = {
            None:       cmd_interactive,
            "ping":     cmd_ping,
            "discover": cmd_discover,
            "stream":   cmd_stream,
            "send":     cmd_send,
            "watch":    cmd_watch,
            "ready":    cmd_ready,
        }
        dispatch[args.subcommand](client, args)
    finally:
        client.close()


if __name__ == "__main__":
    main()
