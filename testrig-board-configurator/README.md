# Robot Board Config Controller
Arduino Micro (ATmega32U4) — DIP switch emulator via 8-relay array

---

## Quick Setup

### 1. Install PlatformIO

**VS Code (recommended)**
1. Open VS Code → Extensions (`Ctrl+Shift+X`)
2. Search **PlatformIO IDE** → Install
3. Restart VS Code — the PlatformIO toolbar appears in the sidebar

**Command line only**
```bash
pip install platformio
```

### 2. Open the project
```
File → Open Folder → select robot_board_controller/
```
PlatformIO detects `platformio.ini` automatically and installs the
`atmelavr` platform + AVR toolchain on first open (takes ~1 min).

### 3. Build
- **VS Code**: click the ✓ (Build) button in the PlatformIO toolbar
- **CLI**: `pio run`

### 4. Upload to Arduino Micro
Connect the board via USB, then:
- **VS Code**: click the → (Upload) button
- **CLI**: `pio run --target upload`

The Arduino Micro uses the **AVR109 / CDC bootloader**.
You may need to press the reset button once to enter bootloader mode
if the upload fails — the board resets automatically on port open in
most cases.

### 5. Monitor serial output
- **VS Code**: click the plug icon (Serial Monitor) — baud 9600
- **CLI**: `pio device monitor`

---

## Sending a Config

### Python script
```bash
pip install pyserial

# Auto-detect port, send config 0x3F, wait for power cycle
python scripts/robot_config.py 3F

# Explicit port
python scripts/robot_config.py 3F --port /dev/ttyACM0   # Linux/Mac
python scripts/robot_config.py 3F --port COM3            # Windows

# Don't wait for power cycle confirmation
python scripts/robot_config.py 3F --no-wait

# Show all raw serial traffic
python scripts/robot_config.py 3F --verbose
```

### Manual (any serial terminal at 9600 baud)
```
SET 3F        → ACK 3F
SET FF        → NAK 1 INVALID_HEX value must be 00-7F
STATUS        → STATUS CONFIG 3F / STATUS POWER ON / STATUS END
CYCLE         → ACK 3F
HELP          → lists commands
```

---

## Running Unit Tests (no hardware needed)

```bash
pio test -e native
```

Tests run on your PC using a HAL stub — no Arduino required.

---

## Project Structure

```
robot_board_controller/
│
├── platformio.ini          # Board target, build flags, upload settings
│
├── src/                    # Implementation files
│   ├── main.cpp            # setup() / loop() — thin orchestrator only
│   ├── hal/
│   │   └── hal.cpp         # GPIO: relay pin init and write
│   ├── config/
│   │   └── config_manager.cpp  # Business logic: config state, power cycle
│   └── uart/
│       └── uart_api.cpp    # Serial protocol: parse commands, format responses
│
├── include/                # Header files (added to include path by platformio.ini)
│   ├── types.h             # Shared enums and result types (no Arduino dependency)
│   ├── hal/
│   │   └── hal.h
│   ├── config/
│   │   └── config_manager.h
│   └── uart/
│       └── uart_api.h
│
├── scripts/
│   └── robot_config.py     # Python host tool: send config, read response
│
└── test/
    └── test_config_manager.cpp   # Unity unit tests (pio test -e native)
```

### Why this structure?

| Rule | Enforced by |
|---|---|
| Only `hal.cpp` calls `digitalWrite`/`pinMode` | Convention + code review |
| Only `uart_api.cpp` calls `Serial.*` | Convention + code review |
| `types.h` has zero Arduino includes | Keeps it testable on native |
| `include/` mirrors `src/` subfolders | `#include "hal/hal.h"` is unambiguous |
| `main.cpp` has no logic | Diff shows instantly if logic leaks in |

---

## Hardware Pin Map

| Relay | Arduino Micro Pin | Robot PCB DIP |
|-------|-------------------|---------------|
| 0     | D2                | Switch bit 0  |
| 1     | D3                | Switch bit 1  |
| 2     | D9                | Switch bit 2  |
| 3     | D10               | Switch bit 3  |
| 4     | D11               | Switch bit 4  |
| 5     | D12               | Switch bit 5  |
| 6     | D13               | Switch bit 6  |
| 7     | A1                | Board power   |

Relay module polarity: **active LOW**
(relay energised when pin is LOW, contact closed)

---

## Troubleshooting

**Upload fails / port not found**
- Press the reset button on the Micro once, then retry upload immediately
- Check `pio device list` to see available ports
- On Linux, add your user to the `dialout` group: `sudo usermod -aG dialout $USER`

**`avrdude: butterfly_recv(): programmer is not responding`**
- The board did not enter bootloader mode in time
- Try: `pio run --target upload` and press reset as soon as the
  "Connecting..." message appears

**Python script can't find port**
- Use `--port` explicitly: `python scripts/robot_config.py 3F --port /dev/ttyACM0`
- On Windows use Device Manager to find the COM port number
