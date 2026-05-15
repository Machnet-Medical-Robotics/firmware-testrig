---
title: Architecture
nav_order: 2
---

# Architecture

## Contents
- [Design Philosophy](#design-philosophy)
- [Layered Architecture](#layered-architecture)
- [Physical Hardware Model](#physical-hardware-model)
- [Communication Protocols](#communication-protocols)
- [Data Model Hierarchy](#data-model-hierarchy)
- [Failure Classification](#failure-classification)
- [Deployment Model](#deployment-model)

---

## Design Philosophy

The testrig is built around three principles:

**1. Separation of concerns by layer.**
Each layer has a single owner and a single responsibility. The Manager knows about scheduling. The Controller knows about board configuration. The Worker knows about test logic. The Daemon knows about hardware. None of them reach into each other's domain.

**2. Push complexity toward hardware.**
Timing-sensitive operations (UART polling, channel monitoring loops) live in the Hardware Daemon (C++ in production), not in Python. The Worker makes one blocking gRPC call and gets a pass/fail result back.

**3. Test definitions are data, not code.**
Adding a new assembly or a new test requires only new JSON files. No Python code changes are needed unless a genuinely new *type* of hardware interaction is required.

---

## Layered Architecture

```mermaid
block-beta
  columns 1
  block:INPUT
    A["CSV File / JSON TestRun / API call"]
  end
  block:L1["Layer 1 — Scheduling"]
    B["TestRig Manager\nIngestion · Queue · Reports\nmanager/"]
  end
  block:L2["Layer 2 — Orchestration"]
    C["TestRun Controller\nGrouping · Board setup · Worker lifecycle\ncontroller/"]
  end
  block:L3["Layer 3 — Execution"]
    D["Test Worker Runtime\nDSL interpreter · Step engine\nworker/"]
  end
  block:L4["Layer 4 — Hardware Abstraction"]
    E["Hardware Daemon\ngRPC server · UART bridge\nhardware_daemon/"]
  end
  block:HW["Physical Hardware"]
    F["Monitoring PCB"] --> G["Robot PCB (firmware under test)"]
  end
  INPUT --> L1
  L1 --> L2
  L2 --> L3
  L3 --> L4
  L4 --> HW
```

### Layer ownership

| Layer | Owner | Runs as | Language |
|---|---|---|---|
| Manager | `manager/` | Long-running process | Python |
| Controller | `controller/` | Called by Manager | Python |
| Worker | `worker/` | Subprocess (spawned per config group) | Python |
| Hardware Daemon | `hardware_daemon/` | Persistent server | Python mock / C++ production |

---

## Physical Hardware Model

```mermaid
graph LR
    subgraph "Jetson Orin Nano (Controller Machine)"
        MGR["Manager"]
        CTRL["Controller"]
        WRK["Worker\n(subprocess)"]
        DAEMON["Hardware Daemon\n(C++)"]
    end

    subgraph "Board Pair (×3 max)"
        MON["Monitoring PCB\n(UART target)"]
        ESP["ESP32\n(DIP config)"]
        ROBOT["Robot PCB\n(firmware under test)"]
    end

    WRK -->|"gRPC"| DAEMON
    CTRL -->|"gRPC"| DAEMON
    DAEMON -->|"UART"| MON
    DAEMON -->|"UART"| ESP
    MON -->|"CAN"| ROBOT
    MON -->|"GPIO mock signals"| ROBOT
```

**Key hardware facts:**
- The **Robot PCB** runs the firmware under test. It is never addressed directly by the testrig except for optional double-verification.
- The **Monitoring PCB** is the primary UART target. It relays commands to the Robot PCB over CAN and can inject mock signals (e.g. fake encoder pulses) via GPIO.
- The **ESP32** controls the DIP switch array (8 switches via MOSFET array) that configures the Robot PCB hardware mode. Addressed over a separate UART port.
- **Board pairs** are numbered: `pair_1`, `pair_2`, `pair_3`. Currently 1 pair is active.

---

## Communication Protocols

```mermaid
sequenceDiagram
    participant MGR as Manager
    participant CTRL as Controller
    participant WRK as Worker
    participant DAEMON as Hardware Daemon
    participant MON as Monitoring PCB
    participant ROBOT as Robot PCB

    MGR->>CTRL: TestRun object (Python call)
    CTRL->>DAEMON: gRPC ApplyBoardConfig(0xA3)
    DAEMON->>MON: UART → ESP32 DIP config
    CTRL->>DAEMON: gRPC RebootDevice("robot_pcb")
    DAEMON->>ROBOT: UART power cycle
    ROBOT-->>MON: UART boot announcement ("shuttle")
    MON-->>DAEMON: UART identity string
    DAEMON-->>CTRL: gRPC DeviceReadyResponse(identity="shuttle")
    CTRL->>WRK: subprocess stdin (WorkerInput JSON)
    WRK->>DAEMON: gRPC DiscoverCapabilities
    DAEMON-->>WRK: channels + commands list
    WRK->>DAEMON: gRPC SendCommand("com_leadscrew_go", "100 350")
    DAEMON->>MON: UART command
    MON->>ROBOT: CAN message
    ROBOT-->>MON: CAN response
    MON-->>DAEMON: UART "cmd OK"
    DAEMON-->>WRK: gRPC CommandResponse(matched=true)
    WRK->>DAEMON: gRPC WaitForChannel("stepper1_controller", offset=0, min=99.9, max=100.1)
    loop Poll every 50ms
        DAEMON->>MON: UART channel read
        MON-->>DAEMON: float value
    end
    DAEMON-->>WRK: gRPC ChannelWaitResponse(condition_met=true, value=100.0)
    WRK-->>CTRL: subprocess stdout (WorkerOutput JSON)
    CTRL-->>MGR: TestRunResult
    MGR->>MGR: Write JSON report to reports/
```

### IPC summary

| From | To | Protocol | Why |
|---|---|---|---|
| Manager | Controller | Python function call | Same process |
| Controller | Worker | subprocess stdin/stdout JSON | Lifecycle isolation, crash boundary |
| Controller | Daemon | gRPC (unary) | Board management |
| Worker | Daemon | gRPC (unary + streaming) | Step execution |
| Monitoring | Daemon | UART | Physical hardware bridge |
| Monitoring | Robot PCB | CAN | Firmware commands |

---

## Data Model Hierarchy

```mermaid
graph TD
    TR["TestRun\n(scheduling, firmware, policy)"]
    TSR["TestSetRef\n(TestSetId + BoardConfigId + Priority)"]
    TS["TestSet\n(board binding, execution constraints)"]
    TCR["TestCaseRef\n(TestCaseId + Order)"]
    TC["TestCase\n(DSL steps, on_fail)"]
    STEP["Step\n(console / channel_wait / wait / message)"]
    BC["BoardConfig\n(dip_switch_byte, expected_identity)"]

    TR --> TSR
    TSR --> TS
    TSR --> BC
    TS --> TCR
    TCR --> TC
    TC --> STEP
```

**Ownership:**

| Model | Owner | Lives in |
|---|---|---|
| TestRun | Manager | `definitions/testruns/` |
| TestSet | Controller | `definitions/testsets/` |
| TestCase | Worker | `definitions/testcases/` |
| BoardConfig | Controller | `definitions/configs/` |
| Step | Worker/Daemon | Inline in TestCase JSON |

---

## Failure Classification

Every failure is classified by **why** it happened, not just that it did. This drives retry policy and reporting.

```mermaid
flowchart TD
    FAIL["Something failed"]
    Q1{"What failed?"}
    Q2{"Firmware returned\nwrong value/response?"}
    Q3{"TestCase JSON\nreferences unknown\nchannel or command?"}
    Q4{"Board never\nbecame ready?"}
    Q5{"Worker crashed\nor gRPC error?"}
    Q6{"Worker retry\nexhausted?"}

    FAIL --> Q1
    Q1 --> Q2
    Q2 -->|Yes| TEST["TEST_FAIL\nFix the firmware\nNo retry"]
    Q2 -->|No| Q3
    Q3 -->|Yes| INVALID["INVALID_TEST\nFix the TestCase JSON\nNo retry"]
    Q3 -->|No| Q4
    Q4 -->|Yes| HARDWARE["HARDWARE_FAIL\nBoard unavailable\nSkip remaining groups"]
    Q4 -->|No| Q5
    Q5 -->|Yes| INFRA["INFRA_FAIL\nRetry Worker once"]
    INFRA --> Q6
    Q6 -->|Yes| SYSTEM["SYSTEM_FAULT\nAbort TestRun"]
    Q6 -->|No| PASS["Worker retry succeeded\nLog warning"]
```

---

## Deployment Model

```
Jetson Orin Nano
├── run_daemon.py          (persistent, start on boot)
├── run_testrun.py         (CLI entry point)
├── manager/               (ingestion + queue)
├── controller/            (orchestration)
├── worker/                (spawned per test group)
├── hardware_daemon/       (gRPC server, C++ in production)
├── definitions/           (test data — JSON + CSV)
│   ├── configs/           (BoardConfig per assembly)
│   ├── testsets/          (grouped test references)
│   ├── testcases/         (DSL step definitions)
│   └── testruns/          (JSON + CSV run specs)
└── reports/               (JSON results, one per run)
```

The daemon is the only component that must always be running. Everything else is invoked on demand.
