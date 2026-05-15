---
title: System Operation
nav_order: 4
---

# System Operation & Sequences

## Contents
- [End-to-End Flow](#end-to-end-flow)
- [Manager Ingestion](#manager-ingestion)
- [Controller State Machine](#controller-state-machine)
- [Board Setup Sequence](#board-setup-sequence)
- [Worker Lifecycle](#worker-lifecycle)
- [Step Execution](#step-execution)
- [Failure Paths](#failure-paths)
- [Channel Discovery](#channel-discovery)

---

## End-to-End Flow

This is the complete path from user input to report file.

```mermaid
flowchart TD
    INPUT["User provides\nCSV file or JSON TestRun"]
    MGR["Manager\ningest_csv() or ingest_json()\nvalidate()\n_dispatch_and_report()"]
    CTRL["Controller\nload definitions\ngroup by (board_pair + config)\nfor each group: setup → worker"]
    BM["BoardManager\nApplyBoardConfig → ESP32\nRebootDevice → Robot PCB\nCheckDeviceReady → identity check"]
    WRK["Worker subprocess\nDiscoverCapabilities\nvalidate TestCase steps\nexecute TestSets → TestCases → Steps"]
    DAEMON["Hardware Daemon\nUART → Monitoring PCB\nCAN → Robot PCB"]
    RPT["Reporter\nwrite JSON report\nreports/<id>_<ts>_COMPLETED.json"]
    RESULT["TestRunResult\nreturned to caller"]

    INPUT --> MGR
    MGR --> CTRL
    CTRL --> BM
    BM -->|"HARDWARE_FAIL: skip group"| CTRL
    BM -->|"success"| WRK
    WRK -->|"gRPC"| DAEMON
    WRK -->|"WorkerOutput stdout"| CTRL
    CTRL --> RPT
    CTRL --> RESULT
    RPT --> RESULT
```

---

## Manager Ingestion

```mermaid
flowchart LR
    subgraph "CSV path"
        CSV["CSV file or string"] --> PARSE["ingest_csv()\nparse columns\ncheck firmware hash\nbuild TestSetRefs"]
    end
    subgraph "JSON path"
        JSON["TestRun JSON file"] --> LOAD["ingest_json()\nload file\nPydantic validation"]
    end
    PARSE --> VAL["_validate()\nfirmware hash not empty\nat least 1 TestSetRef\nno duplicate IDs"]
    LOAD --> VAL
    VAL -->|"ValidationError"| ERR["raise ValidationError\nclear message to caller"]
    VAL -->|"OK"| DISP["_dispatch_and_report()\n→ Controller.run()"]
    DISP --> REPORT["Reporter.write(result)"]
```

**CSV column rules:**
- All rows must share the same `FirmwareHash` — one firmware per TestRun
- `Priority` defaults to 1 if omitted
- `RequestedBy` is optional — used in report metadata
- Multiple rows with the same `BoardConfigId` are batched by the Controller (one reboot)

---

## Controller State Machine

```mermaid
stateDiagram-v2
    [*] --> PENDING: TestRun received
    PENDING --> GROUPING: load definitions
    GROUPING --> CONFIGURING: for each ConfigGroup
    CONFIGURING --> REBOOTING: ESP32 config applied
    REBOOTING --> WAITING_READY: reboot command issued
    WAITING_READY --> SPAWNING_WORKER: board ready + identity verified
    SPAWNING_WORKER --> WAITING_WORKER: Worker subprocess started
    WAITING_WORKER --> AGGREGATING: WorkerOutput received
    AGGREGATING --> CONFIGURING: next group
    AGGREGATING --> COMPLETED: all groups done
    AGGREGATING --> FAILED: SYSTEM fault + abort_on_critical=true

    WAITING_READY --> CONFIGURING: HARDWARE_FAIL (skip group)
    WAITING_WORKER --> WAITING_WORKER: retry (INFRA_FAIL, attempt 2)
    WAITING_WORKER --> FAILED: SYSTEM fault
```

---

## Board Setup Sequence

This sequence runs once per `ConfigGroup` before a Worker is spawned.

```mermaid
sequenceDiagram
    participant CTRL as Controller
    participant BM as BoardManager
    participant DAEMON as Hardware Daemon
    participant ESP as ESP32
    participant ROBOT as Robot PCB
    participant MON as Monitoring PCB

    CTRL->>BM: setup(board_config)
    BM->>DAEMON: gRPC ApplyBoardConfig(config_byte=0xA3)
    DAEMON->>ESP: UART: send config byte
    ESP-->>DAEMON: UART: "CONFIG_APPLIED"
    DAEMON-->>BM: BoardConfigResponse(status=OK, detail="CONFIG_APPLIED")

    BM->>DAEMON: gRPC RebootDevice("robot_pcb", "power_cycle")
    DAEMON->>ROBOT: UART: power cycle
    DAEMON-->>BM: RebootResponse(status=OK)
    Note over BM: sleep 500ms

    BM->>DAEMON: gRPC CheckDeviceReady("monitoring_pcb", timeout=15000)
    loop Poll for boot announcement
        DAEMON->>MON: UART: wait for boot string
        MON-->>DAEMON: UART: "shuttle" (identity announcement)
    end
    DAEMON-->>BM: DeviceReadyResponse(ready=true, board_identity="shuttle")

    BM->>BM: compare "shuttle" == expected_board_identity
    alt identity matches
        BM-->>CTRL: BoardSetupResult(success=True)
    else identity mismatch
        BM-->>CTRL: BoardSetupResult(success=False, HARDWARE_FAIL)
    end
```

---

## Worker Lifecycle

```mermaid
sequenceDiagram
    participant CTRL as Controller
    participant WRK as Worker (subprocess)
    participant DAEMON as Hardware Daemon

    CTRL->>WRK: spawn subprocess\nstdin: WorkerInput JSON
    WRK->>WRK: parse WorkerInput
    WRK->>DAEMON: gRPC Ping()
    DAEMON-->>WRK: PingResponse(alive=true)
    WRK->>DAEMON: gRPC DiscoverCapabilities("monitoring_pcb")
    DAEMON-->>WRK: channels=[stepper1_controller, stepper1_ic]\ncommands=[com_leadscrew_go, ...]

    loop For each TestSet
        loop For each TestCase
            WRK->>WRK: validate_testcase(tc, discovery)
            alt validation fails
                WRK->>WRK: TestCaseResult(INVALID, INVALID_TEST)
            else validation passes
                loop For each Step
                    WRK->>DAEMON: gRPC call (SendCommand / WaitForChannel)
                    DAEMON-->>WRK: result
                    WRK->>WRK: StepResult(PASSED/FAILED/ERROR)
                end
                WRK->>WRK: aggregate → TestCaseResult
            end
        end
        WRK->>WRK: aggregate → TestSetResult
    end

    WRK->>WRK: aggregate → ConfigGroupResult
    WRK-->>CTRL: stdout: WorkerOutput JSON
    Note over WRK: process exits 0
```

---

## Step Execution

### CONSOLE step

```mermaid
sequenceDiagram
    participant ENG as StepEngine
    participant CLIENT as DaemonClient
    participant DAEMON as Hardware Daemon
    participant MON as Monitoring PCB
    participant ROBOT as Robot PCB

    ENG->>CLIENT: send_command("monitoring_pcb", "com_leadscrew_go", "100 350", match="cmd OK", timeout=5000)
    CLIENT->>DAEMON: gRPC SendCommand
    DAEMON->>MON: UART: "com_leadscrew_go 100 350"
    MON->>ROBOT: CAN: ShuttleLeadscrewMsg::Cmd(translation_mm=100, velocity_mmps=350)
    ROBOT-->>MON: CAN: CommandAck(ACCEPTED)
    MON-->>DAEMON: UART: "cmd OK"
    DAEMON-->>CLIENT: CommandResponse(status=OK, matched=true, response="cmd OK")
    CLIENT-->>ENG: CommandResult(matched=true)
    ENG->>ENG: StepResult(PASSED)
```

### CHANNEL_WAIT step

```mermaid
sequenceDiagram
    participant ENG as StepEngine
    participant CLIENT as DaemonClient
    participant DAEMON as Hardware Daemon
    participant MON as Monitoring PCB

    ENG->>CLIENT: wait_for_channel("monitoring_pcb", "stepper1_controller", offset=0, min=99.9, max=100.1, timeout=15000)
    CLIENT->>DAEMON: gRPC WaitForChannel (blocking)
    loop Poll every 50ms until condition or timeout
        DAEMON->>MON: UART: read stepper1_controller channel
        MON-->>DAEMON: float value (e.g. 87.3)
        Note over DAEMON: 87.3 not in [99.9, 100.1], keep polling
    end
    DAEMON->>MON: UART: read channel
    MON-->>DAEMON: float value 100.02
    Note over DAEMON: 100.02 in [99.9, 100.1] → condition met
    DAEMON-->>CLIENT: ChannelWaitResponse(condition_met=true, last_value=100.02)
    CLIENT-->>ENG: ChannelWaitResult(condition_met=true)
    ENG->>ENG: StepResult(PASSED)
```

---

## Failure Paths

### TEST_FAIL — firmware returned wrong response

```mermaid
sequenceDiagram
    participant WRK as Worker
    participant ENG as StepEngine
    participant DAEMON as Hardware Daemon

    WRK->>ENG: execute ConsoleStep(cmd="on_demand_leadscrew", match="BIST SUCCESS")
    ENG->>DAEMON: gRPC SendCommand
    DAEMON-->>ENG: CommandResponse(status=OK, matched=false, response="BIST FAIL")
    Note over ENG: status=OK means command executed\nmatched=false means firmware result wrong
    ENG-->>WRK: StepResult(FAILED, TEST, actual="BIST FAIL", expected="BIST SUCCESS")
    WRK->>WRK: check stop_if_fail → skip remaining steps if true
    WRK->>WRK: run on_fail commands (com_change_mode 2)
    WRK-->>WRK: TestCaseResult(FAILED, TEST)
```

### INFRA_FAIL + retry

```mermaid
sequenceDiagram
    participant CTRL as Controller
    participant RUNNER as worker_runner
    participant WRK as Worker

    CTRL->>RUNNER: run_worker_with_retry(input, max_retries=1)
    RUNNER->>WRK: spawn attempt 1
    Note over WRK: crash (gRPC error, UART disconnect)
    WRK-->>RUNNER: exit code 1
    RUNNER->>RUNNER: INFRA_FAIL, attempt < max_retries+1
    RUNNER->>WRK: spawn attempt 2 (retry)
    WRK-->>RUNNER: exit 0, valid WorkerOutput
    RUNNER->>RUNNER: result.worker_retries = 1
    RUNNER-->>CTRL: WorkerRunResult(success=True, retries=1)
```

### HARDWARE_FAIL — board identity mismatch

```mermaid
sequenceDiagram
    participant CTRL as Controller
    participant BM as BoardManager

    CTRL->>BM: setup(BoardConfig{expected="shuttle", byte=0xA3})
    BM->>BM: ApplyBoardConfig, RebootDevice, CheckDeviceReady
    Note over BM: board announces "core2" instead of "shuttle"
    BM-->>CTRL: BoardSetupResult(success=False, HARDWARE_FAIL,\n"identity mismatch: expected 'shuttle', got 'core2'")
    CTRL->>CTRL: ConfigGroupResult(HARDWARE_FAIL)
    Note over CTRL: continue to next group (don't abort TestRun)
```

---

## Channel Discovery

The first thing the Worker does after connecting to the daemon is discover what channels and commands the board exposes. This prevents invalid TestCase steps from executing against the wrong firmware.

```mermaid
flowchart TD
    A["Worker: DiscoverCapabilities()"] --> B["Daemon: UART handshake\nwith Monitoring PCB"]
    B --> C["DiscoverResult\nchannels=[stepper1_controller, ...]\ncommands=[com_leadscrew_go, ...]"]
    C --> D["Worker: validate_testcase()"]
    D --> E{"step.type == console?"}
    E -->|yes| F{"command in\ndiscovery.commands?"}
    F -->|no| INVALID["TestCaseResult(INVALID\nINVALID_TEST)\ncontinue other TCs"]
    F -->|yes| NEXT
    E -->|no| G{"step.type == channel_wait?"}
    G -->|yes| H{"channel_name in\ndiscovery.channels?"}
    H -->|no| INVALID
    H -->|yes| I{"offset < num_fields?"}
    I -->|no| INVALID
    I -->|yes| NEXT["step valid → add to run list"]
    G -->|no| NEXT
    NEXT --> D
```

**Note on current mock behaviour:** The mock daemon returns a hardcoded `DiscoverResult` matching the shuttle assembly. When the real C++ daemon is implemented, this will be replaced by an actual UART handshake with the Monitoring PCB.
