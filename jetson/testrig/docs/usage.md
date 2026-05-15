---
title: Usage Guide
nav_order: 7
---

# Usage Guide

## Contents
- [First-Time Setup](#first-time-setup)
- [Daily Workflow](#daily-workflow)
- [CLI Reference](#cli-reference)
- [CSV Format](#csv-format)
- [TestRun JSON Format](#testrun-json-format)
- [Reading Reports](#reading-reports)
- [gRPC Monitor](#grpc-monitor)
- [Process Monitor](#process-monitor)
- [Troubleshooting](#troubleshooting)

---

## First-Time Setup

Run once after extracting the package. Works on Windows and Linux.

```bash
# From the testrig/ directory — no venv needed:
python setup_dev.py       # Windows
python3 setup_dev.py      # Linux / Jetson
```

This script:
1. Creates `.venv/`
2. Installs `grpcio`, `pydantic`, `psutil`
3. Runs `pip install -e .` (makes all imports work without `PYTHONPATH`)
4. Verifies all imports succeed

**After setup, activate the venv once per terminal session:**

```powershell
# Windows PowerShell:
.venv\Scripts\Activate.ps1

# Windows CMD:
.venv\Scripts\activate.bat

# Linux / Jetson:
source .venv/bin/activate
```

> **No `$env:PYTHONPATH` needed.** The editable install handles this permanently.

---

## Daily Workflow

### Option A — Quick smoke test (no report)

```bash
# Terminal 1:
python run_daemon.py

# Terminal 2:
python run_testrun.py --csv-file definitions/testruns/shuttle_quick.csv
```

### Option B — Full regression with report

```bash
# Terminal 1:
python run_daemon.py

# Terminal 2:
python run_testrun.py --csv-file definitions/testruns/shuttle_regression.csv --manager
```
Report written to `reports/<id>_<timestamp>_COMPLETED.json`.

### Option C — Run predefined JSON TestRun

```bash
python run_testrun.py definitions/testruns/TR-2026-05-00001.json --manager
```

### Option D — Inline CSV (no file needed)

```bash
python run_testrun.py --csv "a1b2c3d4,TS-SHUTTLE-LEADSCREW,SH1" --manager
```

### Option E — Run all automated tests

```bash
# Each is self-contained — no daemon needed:
python -m tests.test_end_to_end
python -m tests.test_manager
python -m tests.test_controller
python -m tests.test_worker
python -m tests.test_mock_daemon
```

---

## CLI Reference

### `run_daemon.py`

Starts the Mock Hardware Daemon. Leave running in a background terminal.

```
python run_daemon.py [--port PORT] [--board-identity IDENTITY] [--boot-delay-ms MS]
```

| Flag | Default | Description |
|---|---|---|
| `--port` | 50051 | gRPC listening port |
| `--board-identity` | `shuttle` | Identity string the board announces after boot |
| `--boot-delay-ms` | 500 | Simulated boot delay in ms |

**Example — simulate a different board:**
```bash
python run_daemon.py --board-identity core2 --port 50052
```

Stop with `Ctrl+C`.

---

### `run_testrun.py`

Runs a TestRun and prints the result.

```
python run_testrun.py [testrun_file] [--csv-file PATH] [--csv STRING]
                      [--daemon ADDR] [--manager] [--json] [--requested-by NAME]
```

| Argument | Description |
|---|---|
| `testrun_file` | Path to a TestRun JSON file |
| `--csv-file PATH` | Path to a CSV file |
| `--csv STRING` | Inline CSV string (header + data rows) |
| `--daemon` | Daemon gRPC address (default: `localhost:50051`) |
| `--manager` | Use full Manager pipeline — writes JSON report to `reports/` |
| `--json` | Print full JSON result instead of summary |
| `--requested-by` | Name recorded in TestRun metadata (default: `cli`) |

**Examples:**
```bash
# Direct Controller, no report:
python run_testrun.py definitions/testruns/TR-2026-05-00001.json

# Via Manager, write report:
python run_testrun.py definitions/testruns/TR-2026-05-00001.json --manager

# From CSV file via Manager:
python run_testrun.py --csv-file definitions/testruns/shuttle_regression.csv --manager

# Inline CSV:
python run_testrun.py --csv "FirmwareHash,TestSetId,BoardConfigId
a1b2c3d4,TS-SHUTTLE-LEADSCREW,SH1" --manager

# Full JSON output:
python run_testrun.py definitions/testruns/TR-2026-05-00001.json --json

# Different daemon:
python run_testrun.py definitions/testruns/TR-2026-05-00001.json --daemon localhost:50052
```

---

### `setup_dev.py`

One-time setup. See [First-Time Setup](#first-time-setup).

---

## CSV Format

The CSV format is the lightweight way to define ad-hoc test runs.

```csv
FirmwareHash,TestSetId,BoardConfigId,Priority,RequestedBy
a1b2c3d4,TS-SHUTTLE-LEADSCREW,SH1,1,team-shuttle
a1b2c3d4,TS-PCM-CLAMP,PCM1,2,team-pcm
```

| Column | Required | Rules |
|---|---|---|
| `FirmwareHash` | ✓ | All rows must have the same hash |
| `TestSetId` | ✓ | Must exist in `definitions/testsets/` |
| `BoardConfigId` | ✓ | Must exist in `definitions/configs/` |
| `Priority` | optional | Integer, default 1. Lower = runs first |
| `RequestedBy` | optional | Stored in report metadata |

**Rules:**
- One CSV = one TestRun = one firmware version
- Multiple rows with the same `BoardConfigId` are batched — one reboot, one Worker execution
- Priority determines order within the same `BoardConfigId` group
- Empty `Priority` defaults to 1

**Existing CSV files:**

| File | TestSets | Use case |
|---|---|---|
| `definitions/testruns/shuttle_quick.csv` | 1 (drive only) | Quick smoke test after flash |
| `definitions/testruns/shuttle_regression.csv` | 1 (all 3 TCs) | Full shuttle regression |

---

## TestRun JSON Format

For predefined suites with full control over execution policy.

```json
{
  "schema_version": "1.0",
  "test_run_id": "TR-2026-05-00001",
  "metadata": {
    "requested_by": "team-shuttle",
    "labels": ["regression", "shuttle"],
    "created_at": "2026-05-07T10:00:00Z"
  },
  "firmware": {
    "repository": "robot-firmware",
    "commit_hash": "a1b2c3d4",
    "branch": "main"
  },
  "execution_policy": {
    "max_parallel_board_pairs": 1,
    "retry_on_infra_failure": 1,
    "abort_on_critical_infra_failure": true,
    "stop_on_channel_validation_error": false
  },
  "test_set_refs": [
    {
      "test_set_id": "TS-SHUTTLE-LEADSCREW",
      "board_config_id": "SH1",
      "priority": 1
    }
  ]
}
```

| Field | Description |
|---|---|
| `retry_on_infra_failure` | How many times to retry a crashed Worker (1 = one retry) |
| `abort_on_critical_infra_failure` | If `true`, a SYSTEM fault stops all remaining groups |
| `stop_on_channel_validation_error` | If `true`, a missing channel/command aborts the whole group |

---

## Reading Reports

Reports are JSON files in `reports/`. Filename format:
```
<test_run_id>_<YYYYMMDD-HHMMSS>_<STATUS>.json
```

Example:
```
TR-2026-05-00001_20260507-143022_COMPLETED.json
```

**Top-level structure:**
```json
{
  "test_run_id": "TR-2026-05-00001",
  "status": "COMPLETED",
  "firmware_commit_hash": "a1b2c3d4",
  "started_at": "...",
  "completed_at": "...",
  "config_group_results": [
    {
      "board_pair_id": "pair_1",
      "board_config_id": "SH1",
      "failure_type": null,
      "test_set_results": [
        {
          "test_set_id": "TS-SHUTTLE-LEADSCREW",
          "status": "PARTIAL",
          "test_case_results": [
            {
              "test_case_id": "TC-SH-LEADSCREW-DRIVE",
              "status": "PASSED",
              "step_results": [...]
            }
          ]
        }
      ]
    }
  ]
}
```

**Status meanings:**

| TestRun status | Meaning |
|---|---|
| `COMPLETED` | All groups ran (some may have failed — check group results) |
| `ABORTED` | System fault caused early termination |

| TestSet status | Meaning |
|---|---|
| `PASSED` | All TestCases passed |
| `PARTIAL` | Mix of passed and failed |
| `FAILED` | All TestCases failed |
| `ERROR` | Infrastructure/definition error |

---

## gRPC Monitor

Interactive tool for inspecting the Hardware Daemon directly. Requires daemon to be running.

```bash
# Interactive menu:
python -m tools.grpc_monitor

# One-shot commands:
python -m tools.grpc_monitor ping
python -m tools.grpc_monitor discover
python -m tools.grpc_monitor discover --device robot_pcb
python -m tools.grpc_monitor stream stepper1_controller
python -m tools.grpc_monitor stream stepper1_controller --limit 20
python -m tools.grpc_monitor send com_leadscrew_go "100 350"
python -m tools.grpc_monitor send com_leadscrew_go "100 350" --match "cmd OK"
python -m tools.grpc_monitor watch stepper1_controller 0 99.0 101.0
python -m tools.grpc_monitor watch stepper1_controller 0 99.0 101.0 --timeout 20000
python -m tools.grpc_monitor ready

# Different daemon:
python -m tools.grpc_monitor --daemon localhost:50052 discover
```

**`stream` output example:**
```
  Streaming stepper1_controller from monitoring_pcb
  Press Ctrl+C to stop

  ts_ms     [0]        [1]        [2]        [3]        [4]        [5]
  ─────────────────────────────────────────────────────────────────────
     1234     0.052      0.021      0.500    100.000      2.000      0.052
     1244    10.456      0.412      0.500    100.000      2.000     10.456
     ...
```

`[0]` = position, `[1]` = speed, `[2]` = accel, `[3]` = setpoint, `[4]` = speed_sp, `[5]` = encoder.

---

## Process Monitor

Live dashboard showing all testrig processes and daemon status.

```bash
python -m tools.process_monitor              # refresh every 2s
python -m tools.process_monitor --once       # print once
python -m tools.process_monitor --port 50052 # different daemon port
python -m tools.process_monitor --interval 5 # refresh every 5s
```

Requires `psutil` (installed by `setup_dev.py`).

**Output example:**
```
  TestRig Process Monitor  [14:30:22 UTC]
  ──────────────────────────────────────────────────────────

  Hardware Daemon  (localhost:50051)
    Status:  ● RUNNING  version=mock-0.1

  Processes
      PID  Role                  Status      CPU     Mem     Up
  ───────  ────────────────────  ──────────  ─────  ───────  ──────
    12345  hardware_daemon       running     0.2%    45.2M  12m30s
    12678  worker                running     8.1%    62.1M      4s

  Ports
    ● :50051  Hardware Daemon gRPC              open
```

---

## Troubleshooting

### "Relative module names not supported"

You ran `python -m .\tests\test_end_to_end.py` — the path format is wrong for `-m`.

```bash
# Wrong:
python -m .\tests\test_end_to_end.py
python -m tests.test_end_to_end.py

# Correct:
python -m tests.test_end_to_end
```

### "ModuleNotFoundError: No module named 'shared'"

The editable install isn't active. Either:
```bash
# Option A: run setup once
python setup_dev.py

# Option B: run with PYTHONPATH (temporary)
$env:PYTHONPATH="."  # PowerShell
set PYTHONPATH=.     # CMD
PYTHONPATH=.         # Linux prefix
```

### "Cannot connect to daemon at localhost:50051"

The daemon isn't running. Start it:
```bash
python run_daemon.py
```

### "Port already in use"

A previous daemon is still running.
```bash
# Linux:
pkill -f mock_daemon
pkill -f run_daemon

# Windows — find and end the Python process in Task Manager,
# or in PowerShell:
Get-Process python | Where-Object {$_.MainWindowTitle -eq ""} | Stop-Process
```

### "Board identity mismatch"

The `expected_board_identity` in `definitions/configs/<id>.json` doesn't match what the daemon returns. Check:
1. The mock daemon `--board-identity` flag matches the config file
2. For real hardware: the Robot PCB is announcing the correct identity on UART

### "TestCase definition invalid: channel 'X' not found on board"

The TestCase references a channel that isn't in the discovery result. Check:
1. The channel name matches exactly (case-sensitive)
2. The mock daemon `CHANNEL_REGISTRY` has the channel
3. For real hardware: the discovery handshake is returning the correct channel list

### Tests fail with import errors after pulling new code

Re-run the editable install:
```bash
pip install -e .
```
