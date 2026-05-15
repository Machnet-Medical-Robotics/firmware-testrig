"""
tools/process_monitor.py
Monitor all TestRig-related processes — like `top` but specific to this project.

Shows:
  - Hardware Daemon (mock or real) — PID, port, uptime, memory
  - Active Worker subprocesses
  - Controller process (if running)
  - gRPC port binding status
  - Queue depth (if Manager is reachable)

Refreshes every 2 seconds. Press Ctrl+C to exit.

Works on Windows and Linux — uses psutil for cross-platform process info.

Usage:
    python -m tools.process_monitor           # live refresh every 2s
    python -m tools.process_monitor --once    # print once and exit
    python -m tools.process_monitor --port 50051  # check specific port
"""

from __future__ import annotations

import argparse
import os
import platform
import socket
import sys
import time
from datetime import datetime, timezone

# psutil is optional — gracefully degrade if not installed
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


# ---------------------------------------------------------------------------
# Platform-aware terminal control
# ---------------------------------------------------------------------------

def clear_screen():
    if sys.stdout.isatty():
        os.system("cls" if platform.system() == "Windows" else "clear")


def supports_color() -> bool:
    if platform.system() == "Windows":
        try:
            import ctypes
            kernel = ctypes.windll.kernel32
            # Enable VIRTUAL_TERMINAL_PROCESSING on Windows 10+
            kernel.SetConsoleMode(kernel.GetStdHandle(-11), 7)
            return True
        except Exception:
            return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


USE_COLOR = supports_color()


def color(text: str, code: str) -> str:
    if not USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def green(t):  return color(t, "32")
def red(t):    return color(t, "31")
def yellow(t): return color(t, "33")
def cyan(t):   return color(t, "36")
def bold(t):   return color(t, "1")
def dim(t):    return color(t, "2")


# ---------------------------------------------------------------------------
# Port / gRPC checks
# ---------------------------------------------------------------------------

def is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """Check if a TCP port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, OSError, TimeoutError):
        return False


def ping_daemon(host: str, port: int) -> tuple[bool, str]:
    """
    Try to ping the gRPC daemon. Returns (alive, version_or_error).
    Uses a short timeout so the monitor stays responsive.
    """
    if not is_port_open(host, port, timeout=0.3):
        return False, "port closed"
    try:
        from shared.proto.client import DaemonClient
        client = DaemonClient(f"{host}:{port}")
        client.connect()
        result = client.ping()
        client.close()
        return result.alive, result.daemon_version
    except Exception as exc:
        return False, str(exc)[:40]


# ---------------------------------------------------------------------------
# Process inspection (requires psutil)
# ---------------------------------------------------------------------------

TESTRIG_KEYWORDS = [
    "mock_daemon", "run_daemon", "worker.worker",
    "test_end_to_end", "test_controller", "test_worker",
    "run_testrun", "testrig",
]


def find_testrig_processes() -> list[dict]:
    """Find all Python processes related to this project."""
    if not HAS_PSUTIL:
        return []

    results = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time",
                                      "memory_info", "status", "cpu_percent"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            cmd_str = " ".join(str(c) for c in cmdline)

            if not any(kw in cmd_str for kw in TESTRIG_KEYWORDS):
                continue
            if "process_monitor" in cmd_str:
                continue  # don't show ourselves

            name = proc.info.get("name", "?")
            pid  = proc.info.get("pid", 0)

            # Determine role from cmdline
            role = "unknown"
            if "mock_daemon" in cmd_str or "run_daemon" in cmd_str:
                role = "hardware_daemon"
            elif "worker.worker" in cmd_str:
                role = "worker"
            elif "test_controller" in cmd_str:
                role = "controller_test"
            elif "test_end_to_end" in cmd_str:
                role = "e2e_test"
            elif "run_testrun" in cmd_str:
                role = "run_testrun"
            elif "testrig" in cmd_str:
                role = "testrig"

            # Uptime
            create_time = proc.info.get("create_time", 0)
            uptime_s    = time.time() - create_time if create_time else 0

            # Memory
            mem_info = proc.info.get("memory_info")
            mem_mb   = mem_info.rss / 1024 / 1024 if mem_info else 0.0

            # CPU (first call returns 0, subsequent calls return real value)
            cpu = proc.info.get("cpu_percent", 0.0) or 0.0

            results.append({
                "pid":       pid,
                "name":      name,
                "role":      role,
                "cmd":       cmd_str[-60:],
                "uptime_s":  uptime_s,
                "mem_mb":    mem_mb,
                "cpu":       cpu,
                "status":    proc.info.get("status", "?"),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return sorted(results, key=lambda p: p["role"])


def format_uptime(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m{int(seconds % 60)}s"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h{m}m"


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render(daemon_host: str, daemon_port: int):
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    # Header
    print(bold(f"\n  TestRig Process Monitor") + dim(f"  [{now}]"))
    print(dim("  " + "─" * 58))

    # gRPC Daemon status
    alive, version = ping_daemon(daemon_host, daemon_port)
    port_open = is_port_open(daemon_host, daemon_port, 0.3)

    print(f"\n  {bold('Hardware Daemon')}  ({daemon_host}:{daemon_port})")
    if alive:
        print(f"    Status:  {green('● RUNNING')}  version={version}")
    elif port_open:
        print(f"    Status:  {yellow('● PORT OPEN')} but gRPC ping failed")
    else:
        print(f"    Status:  {red('○ NOT RUNNING')}")
        print(f"    Start:   python run_daemon.py")

    # Process list
    print(f"\n  {bold('Processes')}")
    if not HAS_PSUTIL:
        print(f"    {yellow('psutil not installed')} — install it for process info:")
        print(f"    pip install psutil")
    else:
        procs = find_testrig_processes()
        if not procs:
            print(f"    {dim('No testrig processes found')}")
        else:
            # Column header
            print(
                f"    {'PID':>7}  {'Role':<20}  {'Status':<10}  "
                f"{'CPU':>5}  {'Mem':>7}  {'Up':>6}"
            )
            print(f"    {'─'*7}  {'─'*20}  {'─'*10}  {'─'*5}  {'─'*7}  {'─'*6}")
            for p in procs:
                role_c = {
                    "hardware_daemon": cyan,
                    "worker":          green,
                }.get(p["role"], lambda x: x)

                status_c = green if p["status"] == "running" else yellow
                print(
                    f"    {p['pid']:>7}  "
                    f"{role_c(p['role']):<20}  "
                    f"{status_c(p['status']):<10}  "
                    f"{p['cpu']:>4.1f}%  "
                    f"{p['mem_mb']:>6.1f}M  "
                    f"{format_uptime(p['uptime_s']):>6}"
                )

    # Port summary
    print(f"\n  {bold('Ports')}")
    ports_to_check = [
        (daemon_port, "Hardware Daemon gRPC"),
    ]
    for port, label in ports_to_check:
        open_ = is_port_open(daemon_host, port, 0.3)
        icon  = green("●") if open_ else red("○")
        state = green("open") if open_ else red("closed")
        print(f"    {icon} :{port:<6}  {label:<30}  {state}")

    # Hint
    print(f"\n  {dim('Press Ctrl+C to exit')}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="TestRig process monitor"
    )
    parser.add_argument("--daemon",   default="localhost")
    parser.add_argument("--port",     type=int, default=50051)
    parser.add_argument("--once",     action="store_true",
                        help="Print once and exit (no live refresh)")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="Refresh interval in seconds (default: 2)")
    args = parser.parse_args()

    if not HAS_PSUTIL:
        print("\n  NOTE: psutil not installed. Process info unavailable.")
        print("  Install with:  pip install psutil\n")

    if args.once:
        render(args.daemon, args.port)
        return

    try:
        while True:
            clear_screen()
            render(args.daemon, args.port)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n  Monitor stopped.\n")


if __name__ == "__main__":
    main()
