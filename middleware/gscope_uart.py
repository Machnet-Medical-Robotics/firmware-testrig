#!/usr/bin/env python3
"""
GScope UART Client
==================
Communicates with a GScope-enabled robot PCB over UART.
Sends the ENABLE_ALL_CHANNELS command (function=COMMANDS, payload=0x01)
to trigger m_on_connect_cb() and receive the device info dump.

Protocol (gscope-header.hpp):
  Header (8 bytes, little-endian):
    [0]    id           : uint8  = 125 (GSCOPE_ID)
    [1]    function     : uint8  (6 = COMMANDS)
    [2:4]  payload_size : uint16 LE
    [4:6]  crc          : uint16 LE  (XOR of all bytes with crc field=0)
    [6:8]  timestamp_us : uint16 LE  (0 for PC side)

  Payload for ENABLE_ALL_CHANNELS:
    [0]    command      : uint8 = 0x01 (Header::Command::ENABLE_ALL_CHANNELS)

Usage:
  python gscope_uart.py --port COM3 --baud 115200
  python gscope_uart.py --port /dev/ttyUSB0 --baud 115200
  python gscope_uart.py --port COM3 --baud 115200 --no-color
"""

import argparse
import struct
import sys
import threading
import time
from enum import IntEnum

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("pyserial not found. Install it with:  pip install pyserial")
    sys.exit(1)

# ──────────────────────────────────────────────────────────────────────────────
# Protocol constants  (from gscope-header.hpp)
# ──────────────────────────────────────────────────────────────────────────────

GSCOPE_ID   = 125
HEADER_SIZE = 8


class Function(IntEnum):
    NONE                          = 0
    GET_VERSION                   = 1
    GET_MANIFEST_GENERAL_CHANNEL  = 2
    GET_MANIFEST_CHANNEL_INFO     = 3
    REGULAR_PRODUCE               = 4
    DEBUG_INFORMATION             = 5
    COMMANDS                      = 6
    GET_MANIFEST_GENERAL_BUTTON   = 7
    GET_MANIFEST_BUTTON_INFO      = 8
    BUTTON_ACTION                 = 9
    VIRTUAL_COM                   = 10
    VIDEO_STREAM                  = 11
    HALF_DUPLEX_START_RECEPTION   = 12
    GET_COMMAND_GENERAL_INFO      = 13
    GET_COMMAND_INFO              = 14


class Command(IntEnum):
    NONE                 = 0
    ENABLE_ALL_CHANNELS  = 1
    DISABLE_ALL_CHANNELS = 2
    CHANGE_FLAG_TO_CHANNEL = 3


# Color / type byte appended to debug strings by gscope (Color enum in gscope-types.hpp)
class Color(IntEnum):
    BLACK    = 0;  MAROON  = 1;  BROWN   = 2;  OLIVE   = 3
    TEAL     = 4;  NAVY    = 5;  RED     = 6;  ORANGE  = 7
    YELLOW   = 8;  LIME    = 9;  GREEN   = 10; CYAN    = 11
    BLUE     = 12; PURPLE  = 13; MAGENTA = 14; GRAY    = 15
    PINK     = 16; APRICOT = 17; MINT    = 18; LAVENDER= 19
    INFO     = 20; WARN    = 21; ERROR   = 22; DEBUG   = 23
    GSCOPE   = 24; RAW     = 25

COLOR_ANSI = {
    Color.INFO:    "\033[36m",   # cyan
    Color.WARN:    "\033[33m",   # yellow
    Color.ERROR:   "\033[31m",   # red
    Color.DEBUG:   "\033[34m",   # blue
    Color.GSCOPE:  "\033[35m",   # magenta
    Color.GREEN:   "\033[32m",
    Color.LIME:    "\033[92m",
}
ANSI_RESET = "\033[0m"

# ──────────────────────────────────────────────────────────────────────────────
# CRC  (simple XOR over all bytes, with crc field treated as 0 during compute)
# ──────────────────────────────────────────────────────────────────────────────

def calc_crc(packet_bytes: bytes) -> int:
    """XOR all bytes together (same algorithm as Header::calc_crc in the firmware)."""
    crc = 0
    for b in packet_bytes:
        crc ^= b
    return crc & 0xFFFF


# ──────────────────────────────────────────────────────────────────────────────
# Packet builder
# ──────────────────────────────────────────────────────────────────────────────

def build_packet(function: int, payload: bytes, timestamp_us: int = 0) -> bytes:
    """
    Build a complete GScope packet with a valid CRC.

    Layout:
      [0]   id           = 125
      [1]   function
      [2:4] payload_size  (LE)
      [4:6] crc           (LE)  ← filled in after XOR
      [6:8] timestamp_us  (LE)
      [8:]  payload
    """
    payload_size = len(payload)

    # Build header with crc=0, then compute CRC over entire frame
    header = struct.pack("<BBHHh",
        GSCOPE_ID,
        int(function),
        payload_size,
        0,               # crc placeholder
        timestamp_us,
    )
    frame_no_crc = header + payload
    crc = calc_crc(frame_no_crc)

    # Re-pack header with correct CRC
    header = struct.pack("<BBHHH",
        GSCOPE_ID,
        int(function),
        payload_size,
        crc,
        timestamp_us,
    )
    return header + payload


def build_connect_packet() -> bytes:
    """Send COMMANDS / ENABLE_ALL_CHANNELS → triggers m_on_connect_cb()."""
    payload = struct.pack("B", int(Command.ENABLE_ALL_CHANNELS))
    return build_packet(Function.COMMANDS, payload)


def build_get_version_packet() -> bytes:
    """Ask for gscope library version."""
    return build_packet(Function.GET_VERSION, b"")


# ──────────────────────────────────────────────────────────────────────────────
# Packet parser
# ──────────────────────────────────────────────────────────────────────────────

def parse_header(data: bytes):
    """Return (id, function, payload_size, crc, timestamp_us) or None."""
    if len(data) < HEADER_SIZE:
        return None
    return struct.unpack("<BBHHH", data[:HEADER_SIZE])


def color_name(color_byte: int) -> str:
    try:
        return Color(color_byte).name
    except ValueError:
        return f"0x{color_byte:02X}"


def ansi_for_color(color_byte: int, use_color: bool) -> str:
    if not use_color:
        return ""
    try:
        c = Color(color_byte)
        return COLOR_ANSI.get(c, "")
    except ValueError:
        return ""


def decode_regular_produce(payload: bytes, use_color: bool) -> str:
    """
    REGULAR_PRODUCE payload layout:
      [0]   channel_id low byte  (extra_data prepended in firmware)
      ← actually firmware writes: extra_data first, then data
      The extra_data is the channel idx (1 byte low byte of uint16).

    For the log channel (channel 0) the payload is:
      text bytes + color_byte (last byte is the Color enum value)
    """
    if len(payload) < 2:
        return f"<short payload: {payload.hex()}>"

    # The firmware send() writes: extra_data first, then data.
    # extra_data for serialization_channel is &idx (1 byte of channel uint16).
    # So payload[0] = low byte of channel idx, rest = channel data.
    channel_low = payload[0]
    data = payload[1:]

    if len(data) == 0:
        return f"[ch {channel_low}] <empty>"

    # Last byte is the color/type indicator appended by printf
    color_byte = data[-1]
    text_bytes = data[:-1]

    try:
        text = text_bytes.decode("utf-8", errors="replace").rstrip("\x00")
    except Exception:
        text = text_bytes.hex()

    color_str = color_name(color_byte)
    ansi = ansi_for_color(color_byte, use_color)
    reset = ANSI_RESET if (use_color and ansi) else ""

    return f"{ansi}[{color_str}] {text}{reset}"


def decode_packet(data: bytes, use_color: bool) -> str:
    """Decode a complete validated packet into a human-readable string."""
    hdr = parse_header(data)
    if hdr is None:
        return "<invalid header>"

    _id, function, payload_size, _crc, ts = hdr
    payload = data[HEADER_SIZE: HEADER_SIZE + payload_size]

    try:
        fname = Function(function).name
    except ValueError:
        fname = f"func_{function}"

    if function == int(Function.REGULAR_PRODUCE):
        return decode_regular_produce(payload, use_color)

    if function == int(Function.GET_VERSION):
        version = payload.decode("utf-8", errors="replace").rstrip("\x00")
        return f"[VERSION] {version}"

    if function == int(Function.DEBUG_INFORMATION):
        text = payload.decode("utf-8", errors="replace").rstrip("\x00")
        return f"[DEBUG_CMD] {text}"

    # Fallback: show raw
    return f"[{fname}] payload({payload_size}B): {payload.hex()}"


# ──────────────────────────────────────────────────────────────────────────────
# Framing / reassembly
# ──────────────────────────────────────────────────────────────────────────────

class FrameParser:
    """
    Byte-stream reassembler.  Scans for the GSCOPE_ID start byte and
    rebuilds complete frames.
    """

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes):
        """Feed raw bytes; yields complete, CRC-validated frames."""
        self._buf.extend(data)

        while True:
            # Find the GSCOPE_ID start byte
            try:
                start = self._buf.index(GSCOPE_ID)
            except ValueError:
                self._buf.clear()
                break

            if start > 0:
                self._buf = self._buf[start:]

            if len(self._buf) < HEADER_SIZE:
                break  # need more data

            hdr = parse_header(bytes(self._buf))
            if hdr is None:
                self._buf.pop(0)
                continue

            _id, function, payload_size, _crc, _ts = hdr

            # Sanity check
            if function >= int(Function.TOTAL if hasattr(Function, 'TOTAL') else 15):
                self._buf.pop(0)
                continue

            total_size = HEADER_SIZE + payload_size
            if len(self._buf) < total_size:
                break  # need more data

            frame = bytes(self._buf[:total_size])

            # Validate CRC — firmware verifies calc_crc(data, size) == 0
            # which means the CRC stored in the header must make the whole
            # frame XOR to 0.
            if calc_crc(frame) != 0:
                # Bad frame — skip one byte and retry
                self._buf.pop(0)
                continue

            self._buf = self._buf[total_size:]
            yield frame


# ──────────────────────────────────────────────────────────────────────────────
# Helper: list available serial ports
# ──────────────────────────────────────────────────────────────────────────────

def list_ports():
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("No serial ports found.")
        return
    print("Available serial ports:")
    for p in ports:
        print(f"  {p.device:20s}  {p.description}")


# ──────────────────────────────────────────────────────────────────────────────
# Main reader thread
# ──────────────────────────────────────────────────────────────────────────────

class GScopeReader:
    def __init__(self, port: str, baud: int, use_color: bool):
        self.port      = port
        self.baud      = baud
        self.use_color = use_color
        self._ser      = None
        self._parser   = FrameParser()
        self._stop     = threading.Event()

    def connect(self):
        print(f"Connecting to {self.port} @ {self.baud} baud …")
        self._ser = serial.Serial(
            port=self.port,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
        )
        print("Connected.")

    def send_connect(self):
        pkt = build_connect_packet()
        print(f"\n→ Sending ENABLE_ALL_CHANNELS  ({len(pkt)} bytes): {pkt.hex()}")
        self._ser.write(pkt)
        self._ser.flush()

    def send_get_version(self):
        pkt = build_get_version_packet()
        print(f"\n→ Sending GET_VERSION  ({len(pkt)} bytes): {pkt.hex()}")
        self._ser.write(pkt)
        self._ser.flush()

    def _read_loop(self):
        while not self._stop.is_set():
            try:
                raw = self._ser.read(256)
            except serial.SerialException as exc:
                print(f"\n[ERROR] Serial read error: {exc}")
                self._stop.set()
                break

            if not raw:
                continue

            for frame in self._parser.feed(raw):
                msg = decode_packet(frame, self.use_color)
                print(msg)

    def run(self):
        self.connect()

        reader = threading.Thread(target=self._read_loop, daemon=True)
        reader.start()

        time.sleep(0.2)                 # let the port settle
        self.send_connect()             # triggers m_on_connect_cb()

        print("\nListening for packets.  Press Ctrl-C to quit.\n")
        try:
            while reader.is_alive():
                reader.join(timeout=0.5)
        except KeyboardInterrupt:
            print("\nExiting.")
        finally:
            self._stop.set()
            if self._ser and self._ser.is_open:
                self._ser.close()


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="GScope UART client — sends ENABLE_ALL_CHANNELS and reads PCB output."
    )
    parser.add_argument("--port",  "-p", help="Serial port (e.g. COM3 or /dev/ttyUSB0)")
    parser.add_argument("--baud",  "-b", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--list",  "-l", action="store_true",      help="List available serial ports and exit")
    parser.add_argument("--no-color",    action="store_true",      help="Disable ANSI colour output")
    parser.add_argument("--test",        action="store_true",      help="Run offline packet encode/decode self-test")
    args = parser.parse_args()

    if args.list:
        list_ports()
        return

    if args.test:
        run_self_test()
        return

    if not args.port:
        parser.print_help()
        print("\nTip: use --list to see available ports.\n")
        sys.exit(1)

    reader = GScopeReader(args.port, args.baud, use_color=not args.no_color)
    reader.run()


# ──────────────────────────────────────────────────────────────────────────────
# Offline self-test
# ──────────────────────────────────────────────────────────────────────────────

def run_self_test():
    """Build a connect packet, verify CRC, then simulate a response."""
    print("=== GScope self-test ===\n")

    # 1. Build and verify the connect packet
    pkt = build_connect_packet()
    print(f"Connect packet ({len(pkt)} bytes): {pkt.hex()}")
    assert len(pkt) == HEADER_SIZE + 1, "Expected 9 bytes"
    assert pkt[0] == GSCOPE_ID,         "Wrong ID"
    assert pkt[1] == int(Function.COMMANDS), "Wrong function"
    assert calc_crc(pkt) == 0,          "CRC verification failed"
    print("  ✓ ID, function, length, CRC all correct\n")

    # 2. Simulate a DEBUG response packet (REGULAR_PRODUCE with log text)
    #    Firmware layout: extra_data[0] = channel_low, then text + color_byte
    channel_low = 0x80  # channel 0 with CHANNEL_ENABLE bit set (bit15 → stored in higher byte)
    # Actually channel 0 log channel: channel_id = 0 | CHANNEL_ENABLE = 0x8000
    # extra_data is &idx (1 byte, low byte of uint16) → 0x00
    channel_low = 0x00
    text = b"MMR - CAIRS Embedded"
    color = int(Color.DEBUG)
    payload = bytes([channel_low]) + text + bytes([color])
    sim_pkt = build_packet(Function.REGULAR_PRODUCE, payload)

    print(f"Simulated REGULAR_PRODUCE packet: {sim_pkt.hex()}")
    assert calc_crc(sim_pkt) == 0, "Simulated packet CRC failed"

    parser = FrameParser()
    frames = list(parser.feed(sim_pkt))
    assert len(frames) == 1, "Expected 1 frame"

    decoded = decode_packet(frames[0], use_color=False)
    print(f"  Decoded: {decoded}")
    assert "MMR - CAIRS Embedded" in decoded
    assert "DEBUG" in decoded
    print("  ✓ REGULAR_PRODUCE decode correct\n")

    # 3. Framing with noise prefix
    noise = bytes([0x00, 0xFF, 0x42])
    parser2 = FrameParser()
    frames2 = list(parser2.feed(noise + sim_pkt))
    assert len(frames2) == 1, "Framer should skip noise and find 1 packet"
    print("  ✓ Framer correctly skips leading noise bytes\n")

    print("All self-tests passed ✓")


if __name__ == "__main__":
    main()
