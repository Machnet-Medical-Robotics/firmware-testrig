"""
worker/step_engine.py
DSL step interpreter — the core execution unit of the Worker.

Responsibilities:
  - Execute a single step against the Hardware Daemon via DaemonClient
  - Return a StepResult for every step (PASSED / FAILED / SKIPPED / ERROR)
  - Classify failures correctly (TEST vs INFRA)
  - Never raise exceptions — all errors are caught and returned as StepResult

StepType → handler mapping:
  CONSOLE       → _execute_console      → DaemonClient.send_command()
  CHANNEL_WAIT  → _execute_channel_wait → DaemonClient.wait_for_channel()
  WAIT          → _execute_wait         → time.sleep()
  MESSAGE       → _execute_message      → log only

Extending for a new StepType:
  1. Add entry to StepType enum (shared/enums.py)
  2. Add Pydantic model (shared/models/steps.py)
  3. Add handler method _execute_<type> here
  4. Register it in the dispatch table in execute_step()
  5. Add mock response in hardware_daemon/mock_daemon.py
"""

from __future__ import annotations

import time
from typing import Optional

from shared.enums import FailureType, StepStatus
from shared.models.steps import (
    BaseStep, ConsoleStep, ChannelWaitStep, WaitStep, MessageStep,
)
from shared.models.testrig import StepResult
from shared.proto.client import DaemonClient, DeviceStatus
from worker.logger import WorkerLogger


class StepEngine:
    """
    Executes individual DSL steps against the Hardware Daemon.

    One StepEngine instance is created per Worker and reused across all
    TestSets and TestCases. It holds a reference to the shared DaemonClient
    connection and the WorkerLogger.
    """

    def __init__(self, client: DaemonClient, log: WorkerLogger):
        self._client = client
        self._log    = log

    # -----------------------------------------------------------------------
    # Public entry point
    # -----------------------------------------------------------------------

    def execute_step(self, step: BaseStep) -> StepResult:
        """
        Dispatch a step to its handler. Always returns a StepResult —
        exceptions are caught and returned as ERROR status.
        """
        from shared.models.steps import StepType  # local to avoid circular

        self._log.step(step.step_id, step.type.value, step.description or "")

        dispatch = {
            "console":      self._execute_console,
            "channel_wait": self._execute_channel_wait,
            "wait":         self._execute_wait,
            "message":      self._execute_message,
        }

        handler = dispatch.get(step.type.value)
        if handler is None:
            return self._error_result(
                step.step_id,
                f"No handler for step type '{step.type.value}'",
                FailureType.INFRA,
            )

        try:
            result = handler(step)
        except Exception as exc:
            result = self._error_result(
                step.step_id,
                f"Unhandled exception in step {step.step_id}: {exc}",
                FailureType.INFRA,
            )

        self._log.step_result(
            step.step_id,
            result.status.value,
            result.duration_ms or 0,
            result.error_detail or "",
        )
        return result

    # -----------------------------------------------------------------------
    # Step handlers
    # -----------------------------------------------------------------------

    def _execute_console(self, step: ConsoleStep) -> StepResult:
        """
        Send a GScope command to the device, match the response string.

        Maps to: DaemonClient.send_command()

        PASSED:  response received AND return_string_match found (or no match required)
        FAILED:  response received but match not found  → TEST_FAIL
        ERROR:   gRPC error / UART timeout             → INFRA_FAIL
        """
        t0 = time.monotonic()

        try:
            result = self._client.send_command(
                device_id=step.device.value,
                command=step.command,
                command_param=step.command_param or "",
                return_string_match=step.return_string_match or "",
                timeout_ms=step.timeout_ms,
            )
        except Exception as exc:
            return self._error_result(
                step.step_id,
                f"gRPC error on SendCommand: {exc}",
                FailureType.INFRA,
                int((time.monotonic() - t0) * 1000),
            )

        duration_ms = result.duration_ms or int((time.monotonic() - t0) * 1000)

        if result.status == DeviceStatus.ERROR and not result.matched:
            # Command not recognised by daemon / device
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.ERROR,
                failure_type=FailureType.INFRA,
                actual_value=result.actual_response,
                expected=step.return_string_match,
                error_detail=result.detail,
                duration_ms=duration_ms,
            )

        if result.status == DeviceStatus.TIMEOUT:
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.ERROR,
                failure_type=FailureType.INFRA,
                actual_value=result.actual_response,
                expected=step.return_string_match,
                error_detail=f"UART timeout after {step.timeout_ms}ms",
                duration_ms=duration_ms,
            )

        if not result.matched and step.return_string_match:
            # Response received but expected string not found → firmware issue
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.FAILED,
                failure_type=FailureType.TEST,
                actual_value=result.actual_response,
                expected=step.return_string_match,
                error_detail=result.detail,
                duration_ms=duration_ms,
            )

        return StepResult(
            step_id=step.step_id,
            status=StepStatus.PASSED,
            actual_value=result.actual_response,
            expected=step.return_string_match,
            duration_ms=duration_ms,
        )

    def _execute_channel_wait(self, step: ChannelWaitStep) -> StepResult:
        """
        Wait for a GScope channel value to enter [min, max].

        Maps to: DaemonClient.wait_for_channel()

        PASSED:  condition met within timeout
        FAILED:  timeout expired without condition met → TEST_FAIL
                 (the channel exists but the value never reached target)
        ERROR:   channel unknown / gRPC error         → INFRA_FAIL
        """
        expected_str = (
            f"{step.channel_name}[{step.expected.channel_offset}] "
            f"in [{step.expected.min}, {step.expected.max}]"
        )

        t0 = time.monotonic()

        try:
            result = self._client.wait_for_channel(
                device_id=step.device.value,
                channel_name=step.channel_name,
                channel_offset=step.expected.channel_offset,
                min_value=step.expected.min,
                max_value=step.expected.max,
                timeout_ms=step.timeout_ms,
            )
        except Exception as exc:
            return self._error_result(
                step.step_id,
                f"gRPC error on WaitForChannel: {exc}",
                FailureType.INFRA,
                int((time.monotonic() - t0) * 1000),
            )

        duration_ms  = result.duration_ms or int((time.monotonic() - t0) * 1000)
        actual_str   = f"{step.channel_name}[{step.expected.channel_offset}]={result.last_value:.4f}"

        if result.status == DeviceStatus.ERROR:
            # Unknown channel or daemon-side error → INFRA
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.ERROR,
                failure_type=FailureType.INFRA,
                actual_value=actual_str,
                expected=expected_str,
                error_detail=result.detail,
                duration_ms=duration_ms,
            )

        if result.status == DeviceStatus.TIMEOUT or not result.condition_met:
            # Channel reachable but value never hit range → firmware issue
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.FAILED,
                failure_type=FailureType.TEST,
                actual_value=actual_str,
                expected=expected_str,
                error_detail=f"Timeout: {result.detail}",
                duration_ms=duration_ms,
            )

        return StepResult(
            step_id=step.step_id,
            status=StepStatus.PASSED,
            actual_value=actual_str,
            expected=expected_str,
            duration_ms=duration_ms,
        )

    def _execute_wait(self, step: WaitStep) -> StepResult:
        """
        Unconditional delay. No gRPC call.
        Always PASSED unless sleep is interrupted (extremely unlikely).
        """
        t0 = time.monotonic()
        time.sleep(step.timeout_ms / 1000.0)
        duration_ms = int((time.monotonic() - t0) * 1000)
        return StepResult(
            step_id=step.step_id,
            status=StepStatus.PASSED,
            actual_value=f"waited {duration_ms}ms",
            duration_ms=duration_ms,
        )

    def _execute_message(self, step: MessageStep) -> StepResult:
        """
        Human instruction step. Logs the message and returns PASSED.
        In a future interactive mode, this could block for operator ACK.
        """
        self._log.info("MESSAGE: %s", step.message)
        return StepResult(
            step_id=step.step_id,
            status=StepStatus.PASSED,
            actual_value=step.message,
            duration_ms=0,
        )

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _error_result(
        self,
        step_id: int,
        detail: str,
        failure_type: FailureType = FailureType.INFRA,
        duration_ms: int = 0,
    ) -> StepResult:
        return StepResult(
            step_id=step_id,
            status=StepStatus.ERROR,
            failure_type=failure_type,
            error_detail=detail,
            duration_ms=duration_ms,
        )
