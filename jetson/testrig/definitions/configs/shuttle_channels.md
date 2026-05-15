# Shuttle Assembly — GScope Channel Reference

This document maps GScope channel names and offsets for the Shuttle
assembly. Test authors reference these when writing `channel_wait` steps.

---

## Channel: `stepper1_controller`

Produced by `StepperMotor::Stepper` → `m_controller_info_ch`
Update rate: every 10ms

| Offset | Field                    | Unit | Notes                          |
|--------|--------------------------|------|--------------------------------|
| 0      | position_revolutions     | rev  | Current motor position         |
| 1      | speed_rps                | rps  | Current motor speed            |
| 2      | accel_rps2               | rps² | Current acceleration           |
| 3      | position_setpoint_turns  | rev  | Target position                |
| 4      | speed_setpoint_rps       | rps  | Target speed                   |
| 5      | encoder_turns            | rev  | Quadrature encoder position    |

## Channel: `stepper1_ic`

Produced by `StepperMotor::Stepper` → `m_stepper_ic_reg_ch`
Update rate: every 25ms

| Offset | Field               | Unit | Notes                        |
|--------|---------------------|------|------------------------------|
| 0      | driver_status       | reg  | Raw IC status register value |
| 1      | driver_global_status| reg  | Raw IC global status register|

---

## GScope Commands (ConsoleStep → `command` field)

| Command                  | Params              | Response        | Assembly          |
|--------------------------|---------------------|-----------------|-------------------|
| `on_demand_leadscrew`    | none                | `BIST SUCCESS`  | Shuttle leadscrew |
| `com_leadscrew_go`       | `<pos_mm> <vel>`    | `cmd OK`        | Shuttle leadscrew |
| `com_change_mode`        | `<mode_int>`        | `cmd OK`        | System            |
| `com_enter_service_mode` | none                | `cmd OK`        | System            |

---

## Adding a new assembly

1. Create `definitions/configs/<board_config_id>.md` with its channel map.
2. Create `definitions/testsets/<assembly>/` with TestSet JSON files.
3. Create `definitions/testcases/<assembly>/` with TestCase JSON files.
4. Reference the new TestSetId + board_config_id in your TestRun JSON or CSV.

No Python code changes are needed for new assemblies.
