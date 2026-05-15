"""
shared/enums.py
All system-wide enumerations for the TestRig.

Design note:
  All enums are kept in one file intentionally so that the Controller,
  Worker, Manager and Daemon all import from a single source of truth.
  Never duplicate these in component-local files.
"""

from enum import Enum


# ---------------------------------------------------------------------------
# Device targets
# ---------------------------------------------------------------------------

class DeviceTarget(str, Enum):
    """
    Which physical device a step or command is directed at.

    MONITORING_PCB  — primary target for all test commands. The Monitoring
                      PCB relays instructions to the Robot PCB over CAN and
                      can inject mock signals (e.g. GPIO → encoder port).

    ROBOT_PCB       — direct UART to Robot PCB, used only for optional
                      double-verification of GScope channels.

    Adding a new device (e.g. a PSU controller, oscilloscope):
      1. Add an entry here.
      2. Add a handler branch in hardware_daemon/mock_daemon.py
         (and later in the real C++ daemon proto).
      3. No changes needed in Worker or Controller.
    """
    MONITORING_PCB = "monitoring_pcb"
    ROBOT_PCB      = "robot_pcb"


# ---------------------------------------------------------------------------
# Step types (DSL)
# ---------------------------------------------------------------------------

class StepType(str, Enum):
    """
    Supported step types in the TestCase DSL.

    CONSOLE       — send a command string to a device and match the response.
                    Maps to the old `console` type from standalone testing.

    CHANNEL_WAIT  — monitor a named GScope UART channel until its value
                    falls within [min, max] or timeout expires.
                    Maps to the old `channel_wait_until` type.

    WAIT          — unconditional delay (ms).

    MESSAGE       — human instruction. Logged and optionally blocks until
                    operator acknowledgement (controlled by Config.EchoConsole).

    Adding a new step type (e.g. GPIO_SET, POWER_CYCLE):
      1. Add an entry here.
      2. Add the corresponding Pydantic model in shared/models/steps.py.
      3. Add an execution handler in worker/step_engine.py.
      4. Add a mock handler in hardware_daemon/mock_daemon.py.
    """
    CONSOLE      = "console"
    CHANNEL_WAIT = "channel_wait"
    WAIT         = "wait"
    MESSAGE      = "message"


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------

class FailureType(str, Enum):
    """
    Classifies WHY something failed.

    TEST         — Firmware wrong: response mismatch, value out of range.
                   Does NOT trigger retry. Reported per TestCase.

    INFRA        — Testrig infrastructure broke: gRPC error, UART timeout,
                   Worker crash. Triggers Worker retry (once).

    INVALID_TEST — TestCase definition is broken: references unknown channel
                   or command, bad channel offset, malformed step.
                   The board and testrig are fine — fix the TestCase JSON.
                   Never triggers retry.

    HARDWARE     — Board-level fault: never became ready, announced wrong
                   identity, UART disconnect. Marks board pair UNAVAILABLE.

    SYSTEM       — Unrecoverable: Worker retry exhausted, daemon unreachable.
                   Aborts TestRun if abort_on_critical_infra_failure=True.
    """
    TEST         = "TEST"
    INFRA        = "INFRA"
    INVALID_TEST = "INVALID_TEST"
    HARDWARE     = "HARDWARE"
    SYSTEM       = "SYSTEM"


# ---------------------------------------------------------------------------
# Result / status enums
# ---------------------------------------------------------------------------

class StepStatus(str, Enum):
    PASSED  = "PASSED"
    FAILED  = "FAILED"
    SKIPPED = "SKIPPED"   # StopIfFail on a prior step caused skip
    ERROR   = "ERROR"     # Infrastructure error during step execution


class TestCaseStatus(str, Enum):
    PASSED  = "PASSED"
    FAILED  = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR   = "ERROR"
    INVALID = "INVALID"   # TestCase definition is broken — INVALID_TEST failure type


class TestSetStatus(str, Enum):
    PASSED  = "PASSED"
    FAILED  = "FAILED"
    PARTIAL = "PARTIAL"   # Some test cases passed, some failed
    ERROR   = "ERROR"


class TestRunStatus(str, Enum):
    PENDING    = "PENDING"
    QUEUED     = "QUEUED"
    RUNNING    = "RUNNING"
    COMPLETED  = "COMPLETED"
    FAILED     = "FAILED"
    ABORTED    = "ABORTED"   # Aborted due to SYSTEM_FAULT


# ---------------------------------------------------------------------------
# Controller / Worker lifecycle states
# ---------------------------------------------------------------------------

class ControllerState(str, Enum):
    PENDING         = "PENDING"
    QUEUED          = "QUEUED"
    GROUPING        = "GROUPING"
    CONFIGURING     = "CONFIGURING"       # ESP32 config
    REBOOTING       = "REBOOTING"         # Robot PCB reboot
    WAITING_READY   = "WAITING_READY"     # Board readiness check
    SPAWNING_WORKER = "SPAWNING_WORKER"
    WAITING_WORKER  = "WAITING_WORKER"
    AGGREGATING     = "AGGREGATING"
    COMPLETED       = "COMPLETED"
    FAILED          = "FAILED"


class WorkerState(str, Enum):
    INIT                      = "INIT"
    CONNECTING_TO_DAEMON      = "CONNECTING_TO_DAEMON"
    WAITING_FOR_READY         = "WAITING_FOR_READY"
    RUNNING_TESTSETS          = "RUNNING_TESTSETS"
    RUNNING_TESTCASES         = "RUNNING_TESTCASES"
    EXECUTING_STEP            = "EXECUTING_STEP"
    COLLECTING_RESULTS        = "COLLECTING_RESULTS"
    COMPLETED                 = "COMPLETED"
    FAILED                    = "FAILED"


class BoardPairState(str, Enum):
    IDLE           = "IDLE"
    RUNNING        = "RUNNING"
    WAITING_CONFIG = "WAITING_CONFIG"
    UNAVAILABLE    = "UNAVAILABLE"    # Set on HARDWARE_FAIL
