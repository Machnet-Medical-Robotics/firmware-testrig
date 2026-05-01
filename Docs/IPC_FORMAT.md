# Communication Table

# gRPC CONTRACT DESIGN (Hardware Daemon)

## 0. Service Definition

```proto
syntax = "proto3";

package hardware;

service HardwareService {

  // --- Device lifecycle ---
  rpc CheckDeviceReady(DeviceRequest) returns (DeviceReadyResponse);

  // --- Command execution ---
  rpc ExecuteCommand(CommandRequest) returns (CommandResponse);

  // --- Condition-based blocking (VERY IMPORTANT) ---
  rpc WaitForCondition(ConditionRequest) returns (ConditionResponse);

  // --- Telemetry streaming ---
  rpc StreamTelemetry(StreamRequest) returns (stream TelemetryData);

}
```

---

## 1. Device Readiness

```proto
message DeviceRequest {
  string device_id = 1;
}

message DeviceReadyResponse {
  bool ready = 1;
  string message = 2;
}
```

### Behavior

Internally:

```text
- wait for BOOT_COMPLETE
- verify heartbeat stability (N cycles)
- return ready = true
```

---

## 2. Command Execution

Supports your `"console"` type steps.

```proto
message CommandRequest {
  string device_id = 1;
  string command = 2;
  repeated string params = 3;
  int32 timeout_ms = 4;
}

message CommandResponse {
  bool success = 1;
  string output = 2;
  string error = 3;
}
```

---

## 3. Condition Execution (CORE FEATURE)

This replaces your `"channel_wait_until"` logic.

```proto
message ConditionRequest {
  string device_id = 1;
  string channel_name = 2;

  repeated float min_value = 3;
  repeated float max_value = 4;

  int32 timeout_ms = 5;
  int32 poll_interval_ms = 6;
}

message ConditionResponse {
  bool success = 1;
  float last_value = 2;
  string error = 3;
}
```

---

Instead of:

> Worker loops + polls

have:

> **Daemon handles timing + polling + evaluation**

---

## 4. Telemetry Streaming

For future / advanced cases:

```proto
message StreamRequest {
  string device_id = 1;
  string channel_name = 2;
}

message TelemetryData {
  string channel_name = 1;
  float value = 2;
  int64 timestamp = 3;
}
```

---

### Use streaming (gRPC)

* debugging
* live dashboards
* long-running tests

Not required for basic execution

---

# Mapping to Testcase DSL

Testcase:

```json
{
  "TestType": "channel_wait_until",
  "ChannelName": "brushed1_encoder",
  "MaxValue": [250],
  "MinValue": [25],
  "TimeoutMs": 60000
}
```

---

## Worker translates to:

```text
WaitForCondition(
  device_id="robot",
  channel_name="brushed1_encoder",
  min=25,
  max=250,
  timeout=60000
)
```


:

* generate **real `.proto` files + Python/C++ stubs**
* or design the **worker execution engine (how it parses your JSON cleanly)**
* or simulate **parallel execution across 3 board pairs with ESP32 contention**


Good—this is the point where your system either becomes **clean and scalable**, or collapses under string-based hacks.

I’m going to give you a **production-grade blueprint** across all three areas:

1. **Full protobuf (mapped to real firmware messages)**
2. **Codegen pipeline (Python + C++)**
3. **DSL → execution plan compiler**

This will be opinionated and strict—because that’s what prevents pain later.

---

# 🧱 1. FULL PROTOBUF DESIGN (HARDWARE DAEMON)

## 🔥 Core principle

> Your protobuf = your **hardware contract layer**, not just transport

So we model:

* **typed commands (mirroring firmware)**
* **typed responses**
* **typed conditions**

---

## ✅ hardware.proto (clean, scalable)

```proto
syntax = "proto3";

package hardware;

service HardwareService {
  rpc CheckDeviceReady(DeviceRequest) returns (DeviceReadyResponse);

  rpc ExecuteCommand(CommandRequest) returns (CommandResponse);

  rpc WaitForCondition(ConditionRequest) returns (ConditionResponse);

  rpc StreamTelemetry(StreamRequest) returns (stream TelemetryData);
}
```

---

# 🧩 1.1 Device Layer

```proto
message DeviceRequest {
  string device_id = 1;
}

message DeviceReadyResponse {
  bool ready = 1;
  string message = 2;
}
```

---

# 🧠 1.2 Typed Command System (CRITICAL)

## ❗ This replaces your string-based commands entirely

---

## CommandRequest

```proto
message CommandRequest {
  string device_id = 1;

  oneof command {
    EmergencyStopCommand estop = 2;
    OperationModeCommand operation_mode = 3;
    PcLeadscrewCommand pc_leadscrew = 4;
    PcRotationCommand pc_rotation = 5;
    GwClampCommand gw_clamp = 6;
    ConsoleCommand console = 50; // fallback
  }

  int32 timeout_ms = 100;
}
```

---

## Example: Real firmware mapping

### PC Rotation

```proto
message PcRotationCommand {
  float rotation_deg = 1;      // [-180, 180]
  float velocity_dps = 2;      // [0, 3600]
}
```

---

### PC Leadscrew

```proto
message PcLeadscrewCommand {
  float translation_mm = 1;    // [-100, 100]
  float velocity_mmps = 2;     // [0, 65]
}
```

---

### Clamp

```proto
enum ClampState {
  CLAMP_UNKNOWN = 0;
  CLAMP_OPEN = 1;
  CLAMP_CLOSED = 2;
}

message GwClampCommand {
  ClampState distal = 1;
  ClampState proximal = 2;
}
```

---

### Operation Mode

```proto
enum OperationMode {
  MODE_RESTRICTED = 0;
  MODE_NORMAL = 1;
  MODE_SERVICE = 2;
}

message OperationModeCommand {
  OperationMode target_mode = 1;
}
```

---

### Console fallback (keep this!)

```proto
message ConsoleCommand {
  string command = 1;
}
```

---

## ✅ CommandResponse

```proto
message CommandResponse {
  bool success = 1;

  string message = 2;

  oneof payload {
    PcRotationState pc_rotation = 10;
    PcLeadscrewState pc_leadscrew = 11;
  }
}
```

---

## Example state

```proto
message PcRotationState {
  float rotation_deg = 1;
  float velocity_dps = 2;
  float torque_nm = 3;
}
```

---

# 🧠 1.3 Condition System (POWERFUL)

## ❗ This is your biggest upgrade

---

```proto
message ConditionRequest {
  string device_id = 1;
  string channel = 2;

  repeated Condition conditions = 3;

  int32 timeout_ms = 4;
  int32 poll_interval_ms = 5;
}
```

---

## Generic condition

```proto
message Condition {
  string field = 1;

  oneof expected {
    RangeCondition range = 2;
    EnumCondition enum_match = 3;
    BoolCondition bool_match = 4;
  }
}
```

---

```proto
message RangeCondition {
  float min = 1;
  float max = 2;
}

message EnumCondition {
  int32 value = 1;
}

message BoolCondition {
  bool value = 1;
}
```

---

## Response

```proto
message ConditionResponse {
  bool success = 1;
  string message = 2;
}
```

---

# 🧠 1.4 Telemetry

```proto
message StreamRequest {
  string device_id = 1;
  string channel = 2;
}

message TelemetryData {
  string channel = 1;
  float value = 2;
  int64 timestamp = 3;
}
```

---

# ⚙️ 2. CODEGEN PIPELINE (Python + C++)

## 🎯 Goal

> Single source of truth = `.proto`

Everything else is generated.

---

## 🧩 Folder structure

```text
proto/
  hardware.proto

generated/
  python/
  cpp/

worker/
daemon/
```

---

## ⚙️ Codegen commands

### Python

```bash
python -m grpc_tools.protoc \
  -I=proto \
  --python_out=generated/python \
  --grpc_python_out=generated/python \
  proto/hardware.proto
```

---

### C++

```bash
protoc -I=proto \
  --cpp_out=generated/cpp \
  --grpc_out=generated/cpp \
  --plugin=protoc-gen-grpc=`which grpc_cpp_plugin` \
  proto/hardware.proto
```

---

## 🔥 Critical rule

> NEVER manually edit generated code

---

## Worker (Python)

```python
import hardware_pb2
import hardware_pb2_grpc

req = hardware_pb2.CommandRequest(
    device_id="robot1",
    pc_rotation=hardware_pb2.PcRotationCommand(
        rotation_deg=90,
        velocity_dps=100
    ),
    timeout_ms=2000
)

stub.ExecuteCommand(req)
```

---

## Daemon (C++)

```cpp
Status ExecuteCommand(ServerContext* context,
                      const CommandRequest* req,
                      CommandResponse* res) override {

    if (req->has_pc_rotation()) {
        auto cmd = req->pc_rotation();

        // validate ranges
        if (cmd.velocity_dps() > 3600) {
            res->set_success(false);
            res->set_message("Invalid velocity");
            return Status::OK;
        }

        // call firmware adapter
        driver.rotate(cmd.rotation_deg(), cmd.velocity_dps());

        res->set_success(true);
    }

    return Status::OK;
}
```

---

# 🧠 3. DSL COMPILER (JSON → EXECUTION PLAN)

## 🎯 Goal

Your JSON is **NOT executed directly**

Instead:

```text
JSON → compiled → ExecutionPlan → runtime engine
```

---

## ✅ Why?

* validation before execution
* resolve includes
* inject defaults
* detect errors early

---

## 🧩 Step DSL (final form)

```json
{
  "StepId": 10,
  "Action": {
    "Type": "COMMAND",
    "Device": "robot",
    "Command": {
      "Type": "PcRotation",
      "Args": {
        "rotation_deg": 90,
        "velocity_dps": 100
      }
    }
  }
}
```

---

## 🧠 Compiler

```python
def compile_step(step_json):
    action = step_json["Action"]

    if action["Type"] == "COMMAND":
        return compile_command(action)

    if action["Type"] == "WAIT":
        return WaitOp(duration=action["TimeoutMs"])

    if action["Type"] == "CONDITION":
        return compile_condition(action)
```

---

## Compile → gRPC request

```python
def compile_command(action):
    cmd_type = action["Command"]["Type"]
    args = action["Command"]["Args"]

    if cmd_type == "PcRotation":
        return hardware_pb2.CommandRequest(
            device_id=action["Device"],
            pc_rotation=hardware_pb2.PcRotationCommand(
                rotation_deg=args["rotation_deg"],
                velocity_dps=args["velocity_dps"]
            )
        )
```

---

## Execution Plan

```python
class ExecutionPlan:
    steps: List[ExecutableStep]

class ExecutableStep:
    grpc_call
    validator
    retry_policy
```

---

## Runtime Engine

```python
for step in plan.steps:
    result = step.grpc_call()

    if not step.validate(result):
        if step.retry():
            continue
        else:
            fail()
```

---

# 📊 4. REPORT SYSTEM (FINAL DESIGN)

## ✅ JSON (source of truth)

```json
{
  "TestRunId": "TR-1",
  "BoardPairId": "BP1",
  "Results": [
    {
      "TestCaseId": "TC1",
      "Steps": [
        {
          "StepId": 10,
          "Status": "PASSED",
          "DurationMs": 1200
        }
      ]
    }
  ]
}
```

---

## ✅ CSV (export only)

```csv
TestRunId,BoardPair,TestCase,Step,Status,Duration
TR-1,BP1,TC1,10,PASSED,1200
```

---

# 🚀 FINAL SYSTEM VIEW

```text
JSON DSL
   ↓
DSL Compiler
   ↓
Execution Plan
   ↓
Worker (Python)
   ↓ gRPC
Hardware Daemon (C++)
   ↓
Firmware (CAN/UART)
```

---

# 🔥 Final Verdict

You now have:

### ✅ Strong points

* typed hardware contract (huge win)
* clean separation (worker vs daemon)
* scalable DSL
* deterministic execution

---

### ⚠️ Watch out for

1. **Don’t over-model too early**
   → start with 5–10 core commands

2. **Avoid mixing console + typed forever**
   → console should be fallback only

3. **Keep Step = 1 action**
   → never bundle logic

---

# If you want next step

I can:

* generate **real proto from your entire message table**
* build **Python DSL compiler implementation**
* design **parallel scheduler across 3 board pairs with ESP32 config locking**

That’s where this becomes a *serious test infrastructure platform*.

