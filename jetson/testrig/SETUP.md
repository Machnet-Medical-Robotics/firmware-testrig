# TestRig — Setup & Run Guide
## Windows (dev) and Linux/Jetson Orin Nano (deployed)

---

## Platform differences

| Area | Windows | Linux / Jetson |
|---|---|---|
| Python command | `python` or `py` | `python3` |
| Venv activate | `.venv\Scripts\activate` | `source .venv/bin/activate` |
| PYTHONPATH separator | `;` | `:` |
| Serial ports (future) | `COM3`, `COM4` … | `/dev/ttyUSB0`, `/dev/ttyACM0` … |
| gRPC / protobuf | Identical | Identical |
| Pydantic | Identical | Identical |
| subprocess | Identical | Identical |

**Summary: all testrig Python code runs identically on both platforms.**
The only future difference will be `esp32_uart_port` in `BoardConfig`
(`"COM4"` on Windows vs `"/dev/ttyUSB1"` on Linux).

---

## Prerequisites

### Windows
1. Install **Python 3.11+** from python.org (tick "Add to PATH")
2. Verify: `python --version`

### Linux / Jetson Orin Nano
1. Python 3.11+ is usually pre-installed. Check: `python3 --version`
2. If not: `sudo apt update && sudo apt install python3 python3-venv python3-pip`

---

## First-time setup

```bash
# Clone or extract the testrig package
cd testrig/

# Create virtual environment
# Windows:
python -m venv .venv

# Linux:
python3 -m venv .venv

# Activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Windows (CMD):
.venv\Scripts\activate.bat
# Linux:
source .venv/bin/activate

# Install dependencies (identical on both platforms)
pip install -r requirements.txt
```

### If pip install fails on Jetson (ARM)
grpcio has prebuilt wheels for aarch64. If it tries to build from source:
```bash
pip install --upgrade pip
pip install grpcio --no-binary :none: --ignore-installed
```
Or use the system package: `sudo apt install python3-grpcio`

---

## Verify the proto stubs are present

The generated files `shared/proto/hardware_daemon_pb2.py` and
`hardware_daemon_pb2_grpc.py` are included in the package.

If you ever need to regenerate them (e.g. after editing the `.proto` file):
```bash
# With venv active:
python -m grpc_tools.protoc \
  -I shared/proto \
  --python_out=shared/proto \
  --grpc_python_out=shared/proto \
  shared/proto/hardware_daemon.proto

# Then fix the import in the generated grpc file:
# Change:  import hardware_daemon_pb2 ...
# To:      from shared.proto import hardware_daemon_pb2 ...
```

---

## Running the tests

All commands assume you are in the `testrig/` directory with venv active.

### Run everything
```bash
# Windows:
set PYTHONPATH=.
python -m tests.test_mock_daemon
python -m tests.test_worker
python -m tests.test_controller

# Linux:
PYTHONPATH=. python3 -m tests.test_mock_daemon
PYTHONPATH=. python3 -m tests.test_worker
PYTHONPATH=. python3 -m tests.test_controller
```

Expected output:
```
Phase 3 Smoke Test: 16/16 passed
Phase 4 Tests:      18/18 passed
Phase 5 Tests:      17/17 passed
```

### Run a single test suite
```bash
# Windows
set PYTHONPATH=.
python -m tests.test_mock_daemon

# Linux
PYTHONPATH=. python3 -m tests.test_mock_daemon
```

---

## Running the mock daemon standalone

Useful for manual testing while developing the Controller or Manager.

```bash
# With venv active, from testrig/:

# Windows:
set PYTHONPATH=.
python -m hardware_daemon.mock_daemon --port 50051 --board-identity shuttle

# Linux:
PYTHONPATH=. python3 -m hardware_daemon.mock_daemon \
  --port 50051 \
  --board-identity shuttle \
  --boot-delay-ms 500
```

The daemon will log all RPC calls. Leave it running and connect to it
with any gRPC client or run a Worker against it manually.

---

## Running the Worker manually

```bash
# With mock daemon already running on :50051

# Build a WorkerInput JSON (see definitions/ for examples), then:

# Windows:
set PYTHONPATH=.
echo {"board_pair_id":"pair_1",...} | python -m worker.worker

# Linux:
echo '{"board_pair_id":"pair_1",...}' | PYTHONPATH=. python3 -m worker.worker
```

The Worker reads one JSON line from stdin (WorkerInput) and writes one
JSON line to stdout (WorkerOutput). All logs go to stderr.

---

## Running the full Controller flow

```python
# From a Python script or REPL (with venv active, PYTHONPATH=.):

from pathlib import Path
from shared.models.testrig import (
    TestRun, TestRunMetadata, FirmwareRef, ExecutionPolicy, TestSetRef
)
from controller.controller import TestRunController

test_run = TestRun(
    test_run_id="TR-MANUAL-001",
    metadata=TestRunMetadata(requested_by="manual"),
    firmware=FirmwareRef(repository="robot-firmware", commit_hash="a1b2c3d4"),
    execution_policy=ExecutionPolicy(),
    test_set_refs=[
        TestSetRef(test_set_id="TS-SHUTTLE-LEADSCREW",
                   board_config_id="SH1", priority=1),
    ],
)

ctrl   = TestRunController(definitions_root=Path("definitions"))
result = ctrl.run(test_run)
print(result.model_dump_json(indent=2))
```

---

## Project structure

```
testrig/
├── requirements.txt
├── shared/
│   ├── enums.py              # All enums (FailureType, StepType, etc.)
│   ├── models/
│   │   ├── steps.py          # DSL step models (ConsoleStep, ChannelWaitStep…)
│   │   ├── testrig.py        # Core domain models (TestRun, TestSet, TestCase…)
│   │   └── worker_io.py      # Controller↔Worker DTOs
│   └── proto/
│       ├── hardware_daemon.proto         # gRPC service definition
│       ├── hardware_daemon_pb2.py        # Generated — do not edit
│       ├── hardware_daemon_pb2_grpc.py   # Generated — do not edit
│       └── client.py                     # Typed wrapper — import this
├── hardware_daemon/
│   └── mock_daemon.py        # Python mock gRPC server
├── worker/
│   ├── logger.py             # Structured logger
│   ├── step_engine.py        # DSL step interpreter
│   └── worker.py             # Worker entry point (subprocess)
├── controller/
│   ├── loader.py             # Load definitions from disk
│   ├── grouper.py            # Group TestSetRefs by config
│   ├── board_manager.py      # ESP32 config + reboot + readiness
│   ├── worker_runner.py      # Spawn Worker subprocess + retry
│   └── controller.py        # Main orchestrator
├── definitions/
│   ├── configs/              # BoardConfig JSON files
│   ├── testsets/             # TestSet JSON files (one dir per assembly)
│   ├── testcases/            # TestCase JSON files (one dir per assembly)
│   └── testruns/             # TestRun JSON files
└── tests/
    ├── test_mock_daemon.py   # Phase 3: 16 tests
    ├── test_worker.py        # Phase 4: 18 tests
    └── test_controller.py    # Phase 5: 17 tests
```

---

## Adding a new assembly

1. Add commands to `hardware_daemon/mock_daemon.py` → `COMMAND_RESPONSES`
2. Add channels to `hardware_daemon/mock_daemon.py` → `CHANNEL_REGISTRY`
3. Add `definitions/configs/<board_config_id>.json`
4. Add `definitions/testsets/<assembly>/<name>.json`
5. Add `definitions/testcases/<assembly>/<name>.json`
6. Reference in a TestRun JSON or CSV

No Python code changes needed in any other file.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'shared'`**
→ PYTHONPATH is not set. Run with `PYTHONPATH=.` prefix (Linux) or
  `set PYTHONPATH=.` before running (Windows CMD).

**`ModuleNotFoundError: No module named 'grpc'`**
→ Venv not activated. Run `source .venv/bin/activate` (Linux) or
  `.venv\Scripts\activate` (Windows).

**`Port already in use` on test run**
→ A previous test run left a daemon running. Kill it:
  Linux: `pkill -f mock_daemon`
  Windows: find and end `python` process in Task Manager.

**Proto stubs import error on Jetson after fresh install**
→ Regenerate stubs (see "Verify the proto stubs" section above).
  The `grpc_tools.protoc` command must be run on the target platform.
