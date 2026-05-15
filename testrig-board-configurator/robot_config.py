#!/usr/bin/env python3
"""
scripts/robot_config.py
-----------------------
Send a hex config value to the Robot Board Config Controller over serial
and report the result.

Usage:
    python robot_config.py <hex_value> [options]

    <hex_value>   7-bit config byte in hex, e.g. 3F or 0x3F (range 00–7F)

Options:
    -p, --port        Serial port (default: auto-detect Arduino Micro)
    -b, --baud        Baud rate   (default: 9600)
    --timeout         Seconds to wait for ACK/NAK  (default: 3)
    --cycle-wait      Seconds to wait for EVT POWER_ON (default: 10)
    --no-wait         Return after ACK/NAK; skip waiting for EVT POWER_ON
    --verbose         Print raw device output

Exit codes:
    0  Success — config applied and power cycle confirmed
    1  Error   — NAK, timeout, or unexpected response
    2  Usage   — bad arguments or port not found

Examples:
    python scripts/robot_config.py 3F
    python scripts/robot_config.py 0x5A --port /dev/ttyACM0
    python scripts/robot_config.py 7F --no-wait --verbose
"""

import argparse
import sys
import time
import serial
import serial.tools.list_ports


# ─────────────────────────────────────────────────────────────
#  NAK code descriptions
# ─────────────────────────────────────────────────────────────

NAK_CODES = {
    "1": "INVALID_HEX — config value must be in range 00–7F",
    "2": "POWER_BUSY  — a power cycle is already in progress",
    "3": "HAL_FAULT   — relay hardware error on the Arduino",
    "9": "PARSE_ERROR — the Arduino could not parse the command",
}

# ─────────────────────────────────────────────────────────────
#  Auto-detect Arduino Micro
# ─────────────────────────────────────────────────────────────

ARDUINO_VID  = 0x2341
ARDUINO_PIDS = {0x8037, 0x0037}


def find_arduino_port() -> str | None:
    for port in serial.tools.list_ports.comports():
        if port.vid == ARDUINO_VID and port.pid in ARDUINO_PIDS:
            return port.device
    for port in serial.tools.list_ports.comports():
        desc   = (port.description or "").lower()
        device = (port.device      or "").lower()
        if "arduino" in desc or "usbmodem" in device or "acm" in device:
            return port.device
    return None


# ─────────────────────────────────────────────────────────────
#  Result type
# ─────────────────────────────────────────────────────────────

class ConfigResult:
    def __init__(
        self,
        success:           bool,
        active_config:     str | None  = None,
        nak_code:          str | None  = None,
        nak_reason:        str | None  = None,
        power_on_received: bool        = False,
        raw_lines:         list[str]   = None,
    ):
        self.success           = success
        self.active_config     = active_config
        self.nak_code          = nak_code
        self.nak_reason        = nak_reason
        self.power_on_received = power_on_received
        self.raw_lines         = raw_lines or []

    def __str__(self) -> str:
        out = []
        if self.success:
            out.append(f"[OK]  Config applied: 0x{self.active_config}")
            if self.power_on_received:
                out.append(f"[OK]  Power cycle complete — board ON (config=0x{self.active_config})")
            else:
                out.append("[--]  Power cycle not yet confirmed (--no-wait or timeout)")
        else:
            out.append("[ERR] Configuration failed")
            if self.nak_code:
                out.append(f"[ERR] NAK {self.nak_code}: {NAK_CODES.get(self.nak_code, 'Unknown error')}")
            if self.nak_reason:
                out.append(f"[ERR] Detail: {self.nak_reason}")
        if self.raw_lines:
            out += ["", "--- Raw device output ---"] + [f"  {l}" for l in self.raw_lines]
        return "\n".join(out)


# ─────────────────────────────────────────────────────────────
#  Core send function
# ─────────────────────────────────────────────────────────────

def send_config(
    hex_value:          str,
    port:               str,
    baud:               int   = 9600,
    ack_timeout:        float = 3.0,
    cycle_wait:         float = 10.0,
    wait_for_power_on:  bool  = True,
) -> ConfigResult:

    raw: list[str] = []

    def read_line(ser: serial.Serial, timeout_s: float) -> str | None:
        deadline = time.monotonic() + timeout_s
        buf = ""
        while time.monotonic() < deadline:
            ch = ser.read(1)
            if ch:
                ch = ch.decode("ascii", errors="replace")
                if ch in ("\n", "\r"):
                    if buf:
                        return buf.strip()
                else:
                    buf += ch
            else:
                time.sleep(0.005)
        return None

    try:
        ser = serial.Serial(port, baud, timeout=0.1)
    except serial.SerialException as exc:
        return ConfigResult(success=False, nak_reason=f"Could not open {port}: {exc}")

    try:
        # ATmega32U4 resets on port open — wait for boot
        time.sleep(2.0)

        # Drain startup banner
        flush_until = time.monotonic() + 2.0
        while time.monotonic() < flush_until:
            line = ser.readline().decode("ascii", errors="replace").strip()
            if line:
                raw.append(f"[banner] {line}")
                flush_until = time.monotonic() + 0.3

        # Send command
        cmd = f"SET {hex_value.upper().zfill(2)}\n"
        ser.write(cmd.encode("ascii"))
        raw.append(f"[sent] SET {hex_value.upper().zfill(2)}")

        # Wait for ACK / NAK
        response = read_line(ser, ack_timeout)
        if response is None:
            return ConfigResult(
                success=False,
                nak_reason=f"Timeout waiting for ACK/NAK ({ack_timeout}s)",
                raw_lines=raw,
            )

        raw.append(f"[recv] {response}")
        parts = response.split()

        if parts and parts[0] == "NAK":
            return ConfigResult(
                success=False,
                nak_code=parts[1]         if len(parts) > 1 else "?",
                nak_reason=" ".join(parts[2:]) if len(parts) > 2 else "",
                raw_lines=raw,
            )

        if parts and parts[0] == "ACK":
            active = parts[1] if len(parts) > 1 else "??"
            if not wait_for_power_on:
                return ConfigResult(success=True, active_config=active, raw_lines=raw)

            # Wait for EVT POWER_ON
            deadline = time.monotonic() + cycle_wait
            while time.monotonic() < deadline:
                evt = read_line(ser, deadline - time.monotonic())
                if evt is None:
                    break
                raw.append(f"[recv] {evt}")
                ep = evt.split()
                if len(ep) >= 2 and ep[0] == "EVT" and ep[1] == "POWER_ON":
                    return ConfigResult(
                        success=True,
                        active_config=ep[2] if len(ep) > 2 else active,
                        power_on_received=True,
                        raw_lines=raw,
                    )

            return ConfigResult(
                success=True,
                active_config=active,
                power_on_received=False,
                nak_reason=f"EVT POWER_ON not received within {cycle_wait}s",
                raw_lines=raw,
            )

        return ConfigResult(
            success=False,
            nak_reason=f"Unexpected response: {response}",
            raw_lines=raw,
        )

    finally:
        ser.close()


# ─────────────────────────────────────────────────────────────
#  Hex argument validation
# ─────────────────────────────────────────────────────────────

def parse_hex_arg(raw: str) -> str:
    cleaned = raw.strip().upper().lstrip("0X")
    if not cleaned:
        cleaned = "0"
    if not all(c in "0123456789ABCDEF" for c in cleaned):
        raise ValueError(f"'{raw}' is not a valid hex value")
    value = int(cleaned, 16)
    if value > 0xFF:
        raise ValueError(f"0x{cleaned} ({value}) exceeds one byte; valid range is 0x00–0xFF")
    return f"{value:02X}"


# ─────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send a hex config to the Robot Board Config Controller.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("hex_value", help="Config byte in hex, e.g. FE or 0xFE (range 00–FF; bits 7..1 map to relays 0..6, bit 0 ignored)")
    parser.add_argument("-p", "--port",       default=None,  help="Serial port")
    parser.add_argument("-b", "--baud",       type=int, default=9600)
    parser.add_argument("--timeout",          type=float, default=3.0,  metavar="SEC")
    parser.add_argument("--cycle-wait",       type=float, default=10.0, metavar="SEC")
    parser.add_argument("--no-wait",          action="store_true")
    parser.add_argument("--verbose",          action="store_true")
    args = parser.parse_args()

    try:
        hex_clean = parse_hex_arg(args.hex_value)
    except ValueError as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 2

    port = args.port
    if port is None:
        port = find_arduino_port()
        if port is None:
            print("[ERR] Could not auto-detect Arduino Micro. Use --port.", file=sys.stderr)
            return 2
        print(f"[--]  Auto-detected port: {port}")

    print(f"[--]  Sending config 0x{hex_clean} → {port} @ {args.baud} baud")

    result = send_config(
        hex_value=hex_clean,
        port=port,
        baud=args.baud,
        ack_timeout=args.timeout,
        cycle_wait=args.cycle_wait,
        wait_for_power_on=not args.no_wait,
    )

    if not args.verbose:
        result.raw_lines = []

    print(result)
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
