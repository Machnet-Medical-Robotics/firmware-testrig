---
title: Adding New Functions
nav_order: 5
---

# Adding New Functions

## Contents
- [Adding a New Assembly](#adding-a-new-assembly)
- [Adding a New Step Type](#adding-a-new-step-type)
- [Adding a New Device](#adding-a-new-device)
- [Adding a New Board Config](#adding-a-new-board-config)
- [Adding a New TestRun Template](#adding-a-new-testrun-template)

---

## Adding a New Assembly

An *assembly* is a physical sub-system on the Robot PCB — leadscrew, front clamp, PC rollers, etc. Adding a new assembly requires **only new JSON files**. No Python code changes needed.

### Step 1 — Document the assembly's GScope interface

Create `definitions/configs/<assembly>_channels.md`:

```markdown
# Front Clamp Assembly — GScope Channel Reference

## Channel: `front_clamp_state`
| Offset | Field         | Unit | Notes              |
|--------|---------------|------|--------------------|
| 0      | clamp_position| enum | 0=open 1=closed 2=unknown |

## GScope Commands
| Command            | Params | Response      |
|--------------------|--------|---------------|
| `on_demand_clamp`  | none   | `BIST SUCCESS`|
| `com_clamp_open`   | none   | `cmd OK`      |
| `com_clamp_close`  | none   | `cmd OK`      |
```

### Step 2 — Add to mock daemon

In `hardware_daemon/mock_daemon.py`, add to the two registry tables:

```python
COMMAND_RESPONSES = {
    # ... existing ...
    "on_demand_clamp": "BIST SUCCESS",
    "com_clamp_open":  "cmd OK",
    "com_clamp_close": "cmd OK",
}

CHANNEL_REGISTRY = {
    # ... existing ...
    "front_clamp_state": (
        1,
        "Front clamp position",
        lambda offset, t: 1.0,   # always reports closed
    ),
}
```

### Step 3 — Create a BoardConfig

Create `definitions/configs/PCM1.json`:

```json
{
  "board_config_id": "PCM1",
  "description": "PCM module — front clamp config",
  "dip_switch_byte": 34,
  "expected_board_identity": "pcm",
  "esp32_uart_port": null
}
```

To calculate `dip_switch_byte`: each bit is one DIP switch. `34` = `0x22` = `0b00100010` = SW2 and SW6 ON.

### Step 4 — Create TestCase(s)

Create `definitions/testcases/pcm/clamp_basic.json`:

```json
{
  "schema_version": "1.0",
  "test_case_id": "TC-PCM-CLAMP-BASIC",
  "metadata": {
    "test_name": "Front Clamp Open/Close",
    "description": "Opens and closes the front clamp, verifies state channel",
    "tags": ["clamp", "pcm"],
    "estimated_duration_ms": 10000
  },
  "config": { "echo_console": true },
  "steps": [
    {
      "step_id": 1,
      "type": "console",
      "device": "monitoring_pcb",
      "command": "com_enter_service_mode",
      "return_string_match": "cmd OK",
      "timeout_ms": 5000,
      "stop_if_fail": true
    },
    {
      "step_id": 2,
      "type": "console",
      "device": "monitoring_pcb",
      "command": "on_demand_clamp",
      "return_string_match": "BIST SUCCESS",
      "timeout_ms": 15000,
      "stop_if_fail": true
    },
    {
      "step_id": 3,
      "type": "console",
      "device": "monitoring_pcb",
      "command": "com_clamp_open",
      "return_string_match": "cmd OK",
      "timeout_ms": 5000,
      "stop_if_fail": true
    },
    {
      "step_id": 4,
      "type": "channel_wait",
      "device": "monitoring_pcb",
      "description": "Verify clamp reports open (position=0)",
      "channel_name": "front_clamp_state",
      "expected": { "channel_offset": 0, "min": -0.1, "max": 0.1 },
      "timeout_ms": 5000,
      "stop_if_fail": true
    },
    {
      "step_id": 5,
      "type": "console",
      "device": "monitoring_pcb",
      "command": "com_clamp_close",
      "return_string_match": "cmd OK",
      "timeout_ms": 5000
    }
  ],
  "on_fail": [
    { "command": "com_change_mode", "command_param": "2" }
  ]
}
```

### Step 5 — Create a TestSet

Create `definitions/testsets/pcm/clamp.json`:

```json
{
  "schema_version": "1.0",
  "test_set_id": "TS-PCM-CLAMP",
  "metadata": { "description": "PCM front clamp validation" },
  "board_binding": { "board_type": "robot", "requires_monitor": true },
  "execution_constraints": { "requires_exclusive_access": true, "stop_on_failure": false },
  "test_cases": [
    { "test_case_id": "TC-PCM-CLAMP-BASIC", "order": 1 }
  ]
}
```

### Step 6 — Add to a TestRun (CSV or JSON)

**CSV:**
```csv
FirmwareHash,TestSetId,BoardConfigId,Priority,RequestedBy
a1b2c3d4,TS-PCM-CLAMP,PCM1,1,team-pcm
```

**JSON TestRun** — add to `test_set_refs`:
```json
{
  "test_set_id": "TS-PCM-CLAMP",
  "board_config_id": "PCM1",
  "priority": 1
}
```

### Checklist

```
□ definitions/configs/<assembly>_channels.md    — channel reference
□ definitions/configs/<board_config_id>.json    — BoardConfig with dip_switch_byte
□ definitions/testcases/<assembly>/<name>.json  — one or more TestCases
□ definitions/testsets/<assembly>/<name>.json   — one or more TestSets
□ hardware_daemon/mock_daemon.py                — COMMAND_RESPONSES + CHANNEL_REGISTRY
□ CSV or JSON TestRun referencing the new TestSet
```

---

## Adding a New Step Type

A *step type* is a new kind of hardware interaction — for example, `gpio_set` to set a GPIO pin, or `power_cycle` to cut power to a device.

This requires changes to **4 files**:

### Step 1 — Add to `StepType` enum

`shared/enums.py`:
```python
class StepType(str, Enum):
    CONSOLE      = "console"
    CHANNEL_WAIT = "channel_wait"
    WAIT         = "wait"
    MESSAGE      = "message"
    GPIO_SET     = "gpio_set"   # ← new
```

### Step 2 — Add Pydantic model

`shared/models/steps.py`:
```python
class GpioSetStep(BaseStep):
    """
    Set a GPIO pin on the monitoring PCB to high or low.
    Used to inject mock signals into the Robot PCB.
    """
    type:    Literal[StepType.GPIO_SET] = StepType.GPIO_SET
    pin_id:  str    # GPIO pin identifier e.g. "GPIO1_IN1_IO"
    value:   bool   # True = high, False = low
```

Add to `StepUnion`:
```python
StepUnion = Annotated[
    Union[
        ConsoleStep,
        ChannelWaitStep,
        WaitStep,
        MessageStep,
        GpioSetStep,   # ← add here
    ],
    Field(discriminator="type")
]
```

### Step 3 — Add execution handler

`worker/step_engine.py` — add to dispatch table and add handler:
```python
# In execute_step():
dispatch = {
    "console":      self._execute_console,
    "channel_wait": self._execute_channel_wait,
    "wait":         self._execute_wait,
    "message":      self._execute_message,
    "gpio_set":     self._execute_gpio_set,   # ← add
}

# New handler method:
def _execute_gpio_set(self, step: "GpioSetStep") -> StepResult:
    from shared.models.steps import GpioSetStep
    try:
        result = self._client.set_gpio(
            device_id=step.device.value,
            pin_id=step.pin_id,
            value=step.value,
        )
        if result.status == DeviceStatus.OK:
            return self._pass_result(step.step_id, duration_ms=result.duration_ms)
        return self._error_result(step.step_id, result.detail, FailureType.INFRA)
    except Exception as exc:
        return self._error_result(step.step_id, str(exc), FailureType.INFRA)
```

### Step 4 — Add gRPC method and mock handler

**`shared/proto/hardware_daemon.proto`** — add RPC:
```proto
rpc SetGpio (GpioSetRequest) returns (GpioSetResponse);

message GpioSetRequest {
    string device_id = 1;
    string pin_id    = 2;
    bool   value     = 3;
}
message GpioSetResponse {
    DeviceStatus status = 1;
    string       detail = 2;
}
```

Regenerate stubs:
```bash
python -m grpc_tools.protoc -I shared/proto \
    --python_out=shared/proto --grpc_python_out=shared/proto \
    shared/proto/hardware_daemon.proto
# Fix import in generated grpc file
```

Add to `shared/proto/client.py`:
```python
def set_gpio(self, device_id: str, pin_id: str, value: bool):
    resp = self._stub_or_raise().SetGpio(
        pb2.GpioSetRequest(device_id=device_id, pin_id=pin_id, value=value)
    )
    # return typed result
```

Add to `hardware_daemon/mock_daemon.py`:
```python
def SetGpio(self, request, context):
    logger.info("SetGpio | pin=%s value=%s", request.pin_id, request.value)
    return pb2.GpioSetResponse(status=pb2.DEVICE_STATUS_OK, detail="GPIO_SET")
```

---

## Adding a New Device

A *device* is a new physical target — e.g. a PSU controller, oscilloscope, or camera.

### Step 1 — Add to `DeviceTarget` enum

`shared/enums.py`:
```python
class DeviceTarget(str, Enum):
    MONITORING_PCB = "monitoring_pcb"
    ROBOT_PCB      = "robot_pcb"
    PSU_CONTROLLER = "psu_controller"   # ← new
```

### Step 2 — Register in mock daemon

`hardware_daemon/mock_daemon.py` — add to device registries:
```python
DEVICE_CHANNELS = {
    "monitoring_pcb":  [...],
    "robot_pcb":       [...],
    "psu_controller":  ["psu_voltage", "psu_current"],  # ← add
}
DEVICE_COMMANDS = {
    "monitoring_pcb":  [...],
    "psu_controller":  ["psu_enable", "psu_set_voltage"],
}
```

Add to `CHANNEL_REGISTRY` and `COMMAND_RESPONSES` as needed.

### Step 3 — Use in TestCase steps

Steps can now reference `"device": "psu_controller"` and the daemon will route accordingly.

> **Note:** `device_id` in gRPC is a plain string — no proto changes needed to add a new device name. The daemon maps the string to the appropriate hardware interface.

---

## Adding a New Board Config

When testing a different PCB variant or a different DIP switch combination:

1. Create `definitions/configs/<NEW_ID>.json`:
```json
{
  "board_config_id": "CORE2",
  "description": "Core 2 module configuration",
  "dip_switch_byte": 85,
  "expected_board_identity": "core2",
  "esp32_uart_port": null
}
```

2. Calculate `dip_switch_byte` from the DIP switch map:
```python
# Example: SW1, SW3, SW5, SW7 ON = bits 0,2,4,6 = 0b01010101 = 85
byte = 0
for switch_num in [1, 3, 5, 7]:   # 1-indexed
    byte |= (1 << (switch_num - 1))
print(hex(byte))  # 0x55 = 85
```

3. Reference it in TestSetRefs: `"board_config_id": "CORE2"`.

---

## Adding a New TestRun Template

For predefined regression suites that run the same TestSets repeatedly:

Create `definitions/testruns/<name>.json`:
```json
{
  "schema_version": "1.0",
  "test_run_id": "TR-FULL-REGRESSION",
  "metadata": {
    "requested_by": "ci",
    "labels": ["regression", "nightly"]
  },
  "firmware": {
    "repository": "robot-firmware",
    "commit_hash": "REPLACE_WITH_HASH",
    "branch": "main"
  },
  "execution_policy": {
    "max_parallel_board_pairs": 1,
    "retry_on_infra_failure": 1,
    "abort_on_critical_infra_failure": true,
    "stop_on_channel_validation_error": false
  },
  "test_set_refs": [
    { "test_set_id": "TS-SHUTTLE-LEADSCREW", "board_config_id": "SH1", "priority": 1 },
    { "test_set_id": "TS-PCM-CLAMP",         "board_config_id": "PCM1", "priority": 2 }
  ]
}
```

Run it:
```bash
python run_testrun.py definitions/testruns/TR-FULL-REGRESSION.json --manager
```
