# layered architecture

---

## 1. TestRig MANAGER (planning layer to manage test runs)

Responsibilities:

- Single source of truth for what should be tested
- Ensures correct firmware version is selected

Tasks:

* CSV ingestion (testrun request validation)
* Queue + priority
* Assign test runs to board-pairs
* Global scheduling decisions
* Dispatch to controller
* Result aggregation (final report handling)
* Output channel (report export)

Core Functions:

- ingest_csv_test_run()
- validate_test_run_schema()
- enqueue_test_run()
- prioritise_queue()
- resolve_git_commit(board_id, version_rule)
- dispatch_to_controller(test_run_id)
- receive_final_report()
- export_report()

---

## 2. TEST RUN CONTROLLER (execution orchestrator)

Responsibilities:

- Turns abstract test plan into execution
- Maintains execution state machine

Responsibilities:

* Assign test runs to available board-pairs
* Pull firmware hash + Execute robot firmware flashing
* Testset orchestration
* Testcase orchestration
* Ensure:
  * per-board sequential execution
  * cross-board parallel execution
* Sort testSet by board configuration
* Execute state machine per board-pair
  * Call Board Configurator per testset
* Handle retry policies per test run (NOT hardware error interpretation)
* Coordinate with Test Service (Calls TestRig Service API)
  * Step-by-step execution state machine
* Aggregate results per:
  * testcase
  * testset
  * testrun

Core Functions:

- execute_test_run(test_run)
- sort_by_board_id()
- load_testset(testset_id)
- load_testcase(testcase_id)
- flash_board(commit_hash)
- execute_testset_loop()
- execute_testcase_loop()
- call_test_service(script_line)
- aggregate_test_results()
- generate_test_report()

States: (execution State Machine)
PENDING
QUEUED
FLASHING
RUNNING_TESTSET
RUNNING_TESTCASE
WAITING_HARDWARE
FAILED
COMPLETED

### Take note:

* Robot firmware failure = **valid test result**, NOT system failure

---

## 3. TEST SERVICE (hardware execution platform, stateless execution engine)

Responsibilities:

- Isolate all hardware complexity
- Provide unified “test execution API”

Tasks:

* API layer (Request validation)
* Service orchestration layer (execution engine)
* hardware/PCB abstraction layer
  * UART (robot PCB)
  * UART (monitoring PCB)
  * Future: camera, TCP, PSU control, oscilloscope
* protocol(Handshake)/transport layer
* Device (communication hardware) drivers / adapters
* Command/response execution engine

Core Functions:

- API layer:
    - validate_test_script()
    - execute_test_request()
- Service layer:
    - coordinate_command_and_monitoring()
    - manage_execution_context()
- Hardware abstraction layer:
    - uart_robot_send/receive
    - uart_monitor_send/receive
    - camera_capture() (future)
    - tcp_robot_interface() (future)
    - psu_control() (future)
    - oscilloscope_read() (future)
- Communication layer:
protocol framing
retries / acknowledgements
timeouts
handshake/init sequences

**plugin-driven hardware runtime**

---

### 4. TestRig MONITORING (Monitoring + Systems Supervision service)

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

---

### 5. TestRig SHARED

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

---



## 3. BOARD MODEL (VERY IMPORTANT CHANGE)

```
3 Robot Boards + 3 Monitoring Boards
= 3 independent execution lanes
```

Define:

### Board Pair = Execution Unit

```
BoardPair {
    robot_board_id
    monitor_board_id
    config_profile
    state
}
```

### Rules - Scheduling Domain Model

* Each BoardPair runs **independently (parallel allowed)**
* Each BoardPair executes:

  * sequential testsets only
* Testsets requiring config must match BoardPair config
* No cross-board interference


---

# 4. DEPLOYMENT DIAGRAM

### Single Linux Controller Machine

```
┌──────────────────────────────────────────────┐
│              APP MANAGEMENT DAEMON          │
│  - watchdog                                 │
│  - restart services                         │
│  - health registry                          │
│  - system error list                        │
└──────────────┬─────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│            TEST RUN MANAGER                  │
│  queue + CSV + firmware selection           │
└──────────────┬─────────────────────────────┘
               │ dispatch test runs
               ▼
┌──────────────────────────────────────────────┐
│          TEST RUN CONTROLLER                 │
│  ┌───────────────┐  ┌───────────────┐       │
│  │ Board Pair 1  │  │ Board Pair 2  │ ...   │
│  │ sequential    │  │ sequential    │       │
│  └──────┬────────┘  └──────┬────────┘       │
│         │                  │                 │
│         ▼                  ▼                 │
│     TEST SERVICE        TEST SERVICE        │
│   (instance per         (instance per       │
│    board pair OR         board pair OR      │
│    shared pool)          shared pool)        │
└──────────────┬─────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│             HARDWARE LAYER                  │
│  UART Robot Boards (x3)                     │
│  UART Monitor Boards (x3)                   │
└──────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│            MONITORING SERVICE                │
│ logs + metrics + dashboards                 │
└──────────────────────────────────────────────┘
```

---

## App Management hooks into ALL services

```
App Management
   ↓
systemd-like control
   ↓
health checks:
   - controller alive?
   - test service responsive?
   - stuck executions?
```

---



# 6. TEST SERVICE PLUGIN ARCHITECTURE (CRITICAL DESIGN)

---

## Core idea: Hardware = plugins

Instead of hardcoding UART/camera/etc:

### Base interface

```python
class HardwarePlugin:
    def init(self, config): pass
    def execute(self, command): pass
    def health(self): pass
    def reset(self): pass
```

---

## Plugin categories

### 1. Communication plugins

* UARTRobotPlugin
* UARTMonitorPlugin
* TCPRobotPlugin (future)

---

### 2. Measurement plugins

* OscilloscopePlugin
* PSUControlPlugin
* SensorArrayPlugin (future)

---

### 3. Observation plugins

* CameraPlugin (future)
* VisionInspectionPlugin

---

## Plugin registry system

```
Test Service Core
    ↓
Plugin Registry
    ↓
Dynamic loader (based on config)
    ↓
Active hardware adapters
```

---

## Execution model

Test Service does NOT know hardware specifics.

Instead:

```
Testcase → Abstract command
        ↓
Service layer maps to plugin
        ↓
Plugin executes hardware call
        ↓
Response normalized
```

---