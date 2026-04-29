# LAYERED ARCHITECTURE (REFINED)

---

## 1. TESTRIG MANAGER (Planning Layer)

### Responsibilities

* Source of truth for test intent
* Queue + prioritisation
* TestRun validation
* Firmware resolution
* Global scheduling trigger

### Lifecycle

```text
IDLE → INGESTING → VALIDATING → QUEUED → DISPATCHED → COMPLETED
```

### Inputs / Outputs

#### Input

* CSV / API request

#### Output

* `TestRun` object → Controller

### IPC

* Internal Python call or lightweight DTO (no gRPC needed)

### Language

* Python

---

## 2. TEST RUN CONTROLLER (Orchestrator)

### Responsibilities

* Split TestRun by:

  * BoardPair
  * ESP32 Config groups
* Control ESP32 configuration lifecycle
* Control robot reboot lifecycle
* Spawn Worker per (BoardPair + ConfigGroup)
* Aggregate results
* Enforce scheduling + ordering

* Assign test runs to available board-pairs
* Pull firmware hash + Execute robot firmware flashing
* Testset orchestration
* Ensure:
  * per-board sequential execution
  * cross-board parallel execution
* Sort testSet by board configuration
* Execute state machine per board-pair
  * Call Board Configurator per testset
* Handle retry policies per test run (NOT hardware error interpretation)
* Coordinate with Test Worker (Calls TestRig Service API)
* Aggregate results per:
  * testcase
  * testset
  * testrun

### Core Functions

```python
ingest_test_run()
group_by_board_and_config()
acquire_esp32_lock()
apply_esp32_config()
trigger_robot_reboot()
wait_for_board_ready()
spawn_worker()
collect_worker_result()
aggregate_results()
```

### Lifecycle

States: (execution State Machine)

```text
PENDING
→ QUEUED
→ GROUPING (by board + config)
→ CONFIGURING (ESP32)
→ REBOOTING (robot PCB)
→ WAITING_READY
→ SPAWNING_WORKER
→ WAITING_WORKER
→ AGGREGATING
→ COMPLETED | FAILED
```

### IPC

#### Controller → Worker

* subprocess (stdin/stdout or file/socket)

#### Controller → Hardware Daemon

* via Worker ONLY (controller does NOT talk to hardware directly)

### Language

* Python

---

## 3. TEST WORKER RUNTIME (Execution Unit)

> NOT a service/daemon
> NOT long-running
> Spawned per (BoardPair + ConfigGroup)


### Responsibilities

* PCB readiness check
* Execute TestSets sequentially
* Interpret TestCases (DSL execution engine)
* Coordinate command/check from each hardware
* Perform step-level control
* Call hardware daemon via gRPC
* Evaluate results

### Lifecycle

```text
INIT
→ CONNECTING_TO_DAEMON
→ WAITING_FOR_READY_CONFIRMATION
→ RUNNING_TESTSETS
→ RUNNING_TESTCASES
→ EXECUTING_STEP
→ COLLECTING_RESULTS
→ COMPLETED | FAILED
→ EXIT
```

### Input

```json
{
  "board_pair_id": "...",
  "config_id": "...",
  "firmware": "...",
  "testsets": [...]
}
```

### Output

```json
{
  "status": "COMPLETED",
  "results": [...]
}
```

### IPC

#### Worker ↔ Hardware Daemon

* **gRPC**

  * Unary → commands
  * Streaming → telemetry

## Language

* Python

---

## 4. HARDWARE DAEMON (C++)

> ✔ Single source of truth for ALL hardware
> ✔ Persistent process
> ✔ Stateless execution engine (logic-free)

### Responsibilities

* Manage ALL physical interfaces:

  * UART (robot PCB)
  * UART (monitor PCB)
  * Future: camera, TCP, PSU control, oscilloscope
* Handle reconnect after reboot
* Provide unified API for:

  * command execution
  * telemetry streaming
  * readiness detection

### Lifecycle

```text
INIT
→ DEVICE_DISCOVERY
→ IDLE
→ ACTIVE (handling requests)
→ ERROR_RECOVERY
→ IDLE
```

### IPC

#### Exposed API

* gRPC server

#### Methods

```proto
SendCommand(device_id, command)
ReadChannel(device_id, channel)
StreamTelemetry(device_id)
WaitForCondition(...)
CheckDeviceReady(device_id)
```

## Language

* C++

---

## 5. TestRig MONITORING (Monitoring + Systems Supervision service)

Responsibilities:

**App Management Daemon** Potentially split to another daemon
* Ensure all daemons are running
* Auto-restart crashed services
* Health checks (heartbeat-based)
* Maintain error registry (system-level only)
* Detect stuck/hung processes
* Manage service lifecycle (start/stop/restart)
* Track daemon dependencies
* Expose system-wide state
Must not affect test execution

Tasks:

* system metrics
* logs
* test running status
* queue status
* per-board status

Core Functions:

- get_queue_status()
- get_active_test_runs()
- get_system_metrics()
- tail_logs()
- detect_controller_failures()
- heartbeat_monitoring()

Language and Framework: 
- Python

IPC protocol:
- gRPC stream to TEST RUN CONTROLLER

### Take note:

* 1 service instance per board-pair

# 6. MONITORING SERVICE (refined)

## Responsibilities

* System health (NOT test logic)
* Metrics
* Logs
* Worker lifecycle tracking

---

## Lifecycle

```text
INIT → MONITORING → ALERTING → IDLE
```

---

## IPC

* gRPC or metrics endpoint


---

## 6. TestRig SHARED

* TestRun schema (CSV + internal model)
* Testset definition schema
* Testcase schema
* Test report format
* Status enums
* Error codes and Error Map
* API contracts (DTOs)test definitions
* results
* statuses
* board config definitions


# CRITICAL PROTOCOLS (DEFINED)

---

## 1. Board Readiness Detection Protocol

### Purpose

Ensure robot PCB is **fully ready after reboot**

---

### Trigger

After:

* ESP32 config applied
* Robot PCB rebooted

---

### Mechanisms (use at least 2)

#### 1. UART Boot Signature

* Wait for known string:

```
"BOOT_COMPLETE"
```

---

#### 2. Heartbeat Message

* Periodic message:

```
HB:<timestamp>
```

---

#### 3. Active Probe (gRPC call)

```proto
CheckDeviceReady(device_id)
```

Returns:

```json
{ "ready": true }
```

### Final Rule

```text
READY = boot message received AND heartbeat stable for N cycles
```

### Timeout Handling

* timeout → mark test as FAILED (not system error)

---

## 2. ESP32 Configuration Protocol

### Purpose

Set DIP switches via ESP32

### Flow

```text
Controller:
  acquire lock
  send config → ESP32
  wait ACK
  release lock
```

### Requirements

* Must be **serialized**
* Must return:

```json
{ "status": "CONFIG_APPLIED" }
```

---

## 3. Robot Reboot Protocol

### Trigger

After ESP32 config change

### Methods

* Power cycle (preferred)
* Reset pin
* Software reboot command

### Followed by:

→ Board Readiness Protocol

---

## 4. Step Execution Protocol

### Worker → Hardware Daemon

#### Example:

```json
{
  "type": "channel_wait_until",
  "device": "robot_pcb",
  "channel": "encoder",
  "min": 25,
  "max": 250,
  "timeout": 60000
}
```

## Execution model

* Worker sends request
* Daemon performs loop internally
* Returns result

---

## Inter-Process Communication Summary

| From       | To         | Protocol   | Reason                    |
| ---------- | ---------- | ---------- | ------------------------- |
| Manager    | Controller | Python DTO | simple                    |
| Controller | Worker     | subprocess | lifecycle control         |
| Worker     | Hardware   | gRPC       | strong typing + streaming |
| Monitoring | All        | gRPC       | observability             |

---


# 7. BOARD MODEL (finalized)

```json
{
  "board_pair_id": "pair_1",
  "robot_board_id": "R1",
  "monitor_board_id": "M1",
  "current_config": "config_X",
  "state": "IDLE | RUNNING | WAITING_CONFIG"
}
```

---

# 8. DEPLOYMENT MODEL (corrected)

```text
Controller Machine
├── TestRun Manager (Python)
├── TestRun Controller (Python)
├── Monitoring Service (Python)
├── Hardware Daemon (C++)
└── Workers (spawned dynamically, Python)
```

# SYSTEM FLOW

```text
1. Manager → create TestRun
2. Controller:
      group by (board_pair, config)
3. FOR each group:
      configure ESP32
      reboot board
      wait ready
      spawn worker
4. Worker:
      execute testsets → testcases → steps
5. Hardware daemon:
      execute commands
6. Worker returns results
7. Controller aggregates
8. Manager exports report
```
