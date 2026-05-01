## 1. gRPC API (Hardware Daemon)

* `CheckDeviceReady` → lifecycle gate
* `ExecuteCommand` → imperative control
* `WaitForCondition` → blocking evaluation (very important)
* `StreamTelemetry` → observability

> worker = orchestration
> daemon = execution + timing + protocol correctness

---

## 2. Command Structure
UART structured commands:

* `PC_ROTATION(rotation_deg, velocity)`
* `GW_CLAMP_1(distal, proximal)`

### gRPC API structured command payload

```proto
message CommandRequest {
  string device_id = 1;
  string command_name = 2;

  map<string, string> args = 3;

  int32 timeout_ms = 4;
}
```

### (production-grade) design

Split commands:

```proto
message CommandRequest {
  string device_id = 1;
  oneof payload {
    ConsoleCommand console = 2;
    MotionCommand motion = 3;
    ControlCommand control = 4;
  }
  int32 timeout_ms = 10;
}
```

This enables:

* validate ranges
* enforce firmware constraints
* avoid string parsing hell

---


## 3. Generalized condition

```proto
message Condition {
  string field = 1;

  oneof expected {
    Range range = 2;
    EnumMatch enum_match = 3;
    BoolMatch bool_match = 4;
  }
}

message ConditionRequest {
  string device_id = 1;
  string channel_name = 2;

  repeated Condition conditions = 3;

  int32 timeout_ms = 4;
  int32 poll_interval_ms = 5;
}
```

This allows support for:

* encoder range
* clamp state == CLOSED
* fault bitmask == 0

---

> The daemon should understand **semantics**, not just transport.


## 4. Step Schema (Clean, 1:1 with gRPC)

> **Each Step = exactly one gRPC call OR local action** (invariant)

### Step Schema

```json
{
  "StepId": 1,
  "Name": "Set joystick force",
  "Action": {
    "Type": "COMMAND",
    "Device": "robot",
    "Command": {
      "CommandName": "set_joystick_force",
      "Args": {
        "force": 0.5
      }
    },
    "TimeoutMs": 2000
  },
  "Validation": {
    "Type": "STRING_MATCH",
    "Expected": "[Joystick Server] Force send success"
  },
  "Control": {
    "StopOnFail": true,
    "Retry": {
      "MaxAttempts": 1
    }
  }
}
```

### Supported Step Types

#### 1. COMMAND → `ExecuteCommand`

```json
"Action": {
  "Type": "COMMAND",
  "Device": "robot",
  "Command": {
    "CommandName": "test_slider_set_command",
    "Args": {
      "speed": 40,
      "cycles": 3,
      "distance": 50
    }
  }
}
```

#### 2. WAIT → local (no gRPC)

```json
"Action": {
  "Type": "WAIT",
  "TimeoutMs": 5000
}
```

#### 3. CONDITION → `WaitForCondition`

```json
"Action": {
  "Type": "CONDITION",
  "Device": "robot",
  "Channel": "encoder",
  "Conditions": [
    {
      "Field": "value",
      "Range": { "Min": 25, "Max": 250 }
    }
  ],
  "TimeoutMs": 60000
}
```

#### 4. SCRIPT_INCLUDE (your json_read)

```json
"Action": {
  "Type": "INCLUDE",
  "Ref": "common.enter_service_mode"
}
```

#### 5. HUMAN_READABLE_MESSAGE (optional)

```json
"Action": {
  "Type": "MESSAGE",
  "Text": "Rotate motor clockwise"
}
```

---

## Update to JSON*
 **explicitly separate** (For maintainability):

```text
Action     → what to do
Validation → how to judge success
Control    → what to do if failure
```

---

## JSON Map to executable

### Current:

```json
{
  "TestType": "console",
  "CommandName": "set_joystick_force 0.5",
  "ReturnStringMatch": "...",
  "StopIfFail": true
}
```

---

### Becomes:

```json
{
  "StepId": 10,
  "Name": "Set force 0.5",
  "Action": {
    "Type": "COMMAND",
    "Device": "robot",
    "Command": {
      "CommandName": "set_joystick_force",
      "Args": {
        "force": 0.5
      }
    }
  },
  "Validation": {
    "Type": "STRING_MATCH",
    "Expected": "[Joystick Server] Force send success"
  },
  "Control": {
    "StopOnFail": true
  }
}
```

---

# 4. Execution Engine

## Pseudocode

```python
def execute_step(step):
    action = step["Action"]

    if action["Type"] == "COMMAND":
        res = grpc.ExecuteCommand(
            device_id=action["Device"],
            command_name=action["Command"]["CommandName"],
            args=action["Command"]["Args"],
            timeout_ms=action.get("TimeoutMs", 2000)
        )

    elif action["Type"] == "CONDITION":
        res = grpc.WaitForCondition(...)

    elif action["Type"] == "WAIT":
        sleep(action["TimeoutMs"])

    elif action["Type"] == "INCLUDE":
        steps = load_ref(action["Ref"])
        for s in steps:
            execute_step(s)

    return validate(step, res)
```

---

## Validation layer

```python
def validate(step, result):
    v = step.get("Validation")

    if not v:
        return True

    if v["Type"] == "STRING_MATCH":
        return v["Expected"] in result.output

    if v["Type"] == "RANGE":
        return v["Min"] <= result.value <= v["Max"]
```

---

## Control layer

```python
if not success:
    if step["Control"]["StopOnFail"]:
        raise TestFailure
```

---

# 5. Report Schema (Do NOT use CSV for full report)

> JSON for full report
> CSV only for summary/export

---

## Full Report (JSON)

```json
{
  "TestRunId": "TR-001",
  "Status": "FAILED",
  "StartTime": "...",
  "EndTime": "...",

  "BoardPairs": [
    {
      "BoardPairId": "BP1",
      "TestSets": [
        {
          "TestSetId": "TS1",
          "TestCases": [
            {
              "TestCaseId": "TC1",
              "Status": "FAILED",
              "Steps": [
                {
                  "StepId": 10,
                  "Status": "FAILED",
                  "Error": "Timeout",
                  "DurationMs": 30000
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

## CSV (flattened summary)

```csv
TestRunId,TestSetId,TestCaseId,StepId,Status,DurationMs,Error
TR-001,TS1,TC1,10,FAILED,30000,Timeout
```

## Rule

| Use case  | Format |
| --------- | ------ |
| Execution | JSON   |
| Storage   | JSON   |
| Debugging | JSON   |
| Analytics | CSV    |
| Dashboard | DB     |