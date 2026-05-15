"""
shared/models/steps.py
Pydantic models for each TestCase DSL step type.

Extension guide — adding a new step type (e.g. GPIO_SET):
  1. Add GPIO_SET to StepType enum in shared/enums.py.
  2. Create a new model here (e.g. GpioSetStep) inheriting BaseStep.
  3. Add it to the StepUnion at the bottom of this file.
  4. Add execution handler in worker/step_engine.py.
  5. Add mock handler in hardware_daemon/mock_daemon.py.
  No other files need to change.
"""

from __future__ import annotations
from typing import Annotated, Literal, Optional, Union
from pydantic import BaseModel, Field
from shared.enums import DeviceTarget, StepType


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class BaseStep(BaseModel):
    """
    Fields common to every step type.

    step_id       — sequential integer, 1-based, unique within a TestCase.
    type          — discriminator for the step union (see bottom of file).
    device        — which physical device this step targets.
    stop_if_fail  — if True, remaining steps in the TestCase are skipped
                    when this step fails. Does not affect other TestCases
                    unless the TestSet has StopOnFailure: true.
    description   — optional human-readable label shown in logs/reports.
    """
    step_id:      int
    type:         StepType
    device:       DeviceTarget = DeviceTarget.MONITORING_PCB
    stop_if_fail: bool         = False
    description:  Optional[str] = None


# ---------------------------------------------------------------------------
# CONSOLE step
# ---------------------------------------------------------------------------

class ConsoleStep(BaseStep):
    """
    Send a command to the target device and match the response string.

    command             — command name (e.g. "on_demand_leadscrew",
                          "com_leadscrew_go"). Maps 1:1 to the GScope
                          command registered on the firmware side.

    command_param       — optional parameter string appended to command
                          (e.g. "100 350" for com_leadscrew_go).

    return_string_match — substring that must appear in the device response
                          for the step to PASS. Case-sensitive contains check.

    timeout_ms          — how long to wait for the response before INFRA_FAIL.

    Assembly extension note:
      Each Robot PCB assembly (leadscrew, clamp, rollers etc.) registers its
      own GScope commands. Adding a new assembly means adding new TestCases
      that use ConsoleStep with the new command names. No code changes needed.
    """
    type:                Literal[StepType.CONSOLE] = StepType.CONSOLE
    command:             str
    command_param:       Optional[str] = None
    return_string_match: Optional[str] = None
    timeout_ms:          int           = 5000


# ---------------------------------------------------------------------------
# CHANNEL_WAIT step
# ---------------------------------------------------------------------------

class ChannelExpected(BaseModel):
    """
    Value range for a channel condition.

    channel_offset  — index into the GScope channel data array.
                      e.g. for stepper1_controller:
                        0 = position_revolutions
                        1 = speed_rps
                        2 = accel_rps2
                        3 = position_setpoint_turns
                        4 = speed_setpoint_rps
                        5 = encoder_turns
                      Each assembly's channel layout should be documented
                      in definitions/configs/<assembly>.md as channels are added.
    min             — inclusive lower bound.
    max             — inclusive upper bound.
    """
    channel_offset: int   = 0
    min:            float
    max:            float


class ChannelWaitStep(BaseStep):
    """
    Monitor a named GScope UART channel until its value at channel_offset
    falls within [expected.min, expected.max], or timeout_ms expires.

    channel_name  — GScope channel name as registered in firmware
                    (e.g. "stepper1_controller", "stepper1_ic").

    Multiple offsets:
      If you need to check multiple offsets of the same channel, use
      multiple ChannelWaitStep entries with the same channel_name but
      different expected.channel_offset values.

    Assembly extension note:
      New assemblies register new GScope channels. Add the channel name and
      its offset layout to definitions/configs/<assembly>.md, then reference
      the channel_name directly in TestCases. No code changes needed.
    """
    type:         Literal[StepType.CHANNEL_WAIT] = StepType.CHANNEL_WAIT
    channel_name: str
    expected:     ChannelExpected
    timeout_ms:   int = 60000


# ---------------------------------------------------------------------------
# WAIT step
# ---------------------------------------------------------------------------

class WaitStep(BaseStep):
    """
    Unconditional delay.
    timeout_ms — duration to wait in milliseconds.
    """
    type:       Literal[StepType.WAIT] = StepType.WAIT
    timeout_ms: int


# ---------------------------------------------------------------------------
# MESSAGE step
# ---------------------------------------------------------------------------

class MessageStep(BaseStep):
    """
    Human instruction step.
    Logged to the test report. If Config.EchoConsole is true in the
    TestCase, execution blocks until operator acknowledgement.

    Use this for steps that require physical interaction:
      e.g. "Rotate motor CLOCKWISE by hand"
           "Connect oscilloscope probe to TP3"
    """
    type:    Literal[StepType.MESSAGE] = StepType.MESSAGE
    message: str


# ---------------------------------------------------------------------------
# Union — discriminated by `type` field
# Used by TestCase to parse a heterogeneous step list.
# When adding a new step: append it here.
# ---------------------------------------------------------------------------

StepUnion = Annotated[
    Union[
        ConsoleStep,
        ChannelWaitStep,
        WaitStep,
        MessageStep,
    ],
    Field(discriminator="type")
]
