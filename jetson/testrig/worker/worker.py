"""
worker/worker.py
Test Worker Runtime — entry point for subprocess execution.

LIFECYCLE:
  INIT
  → CONNECTING_TO_DAEMON      connect gRPC, ping
  → WAITING_FOR_READY         CheckDeviceReady + board identity verification
  → DISCOVERING               DiscoverCapabilities
  → VALIDATING                validate all TestCase steps against discovery
  → RUNNING_TESTSETS          for each TestSet:
      → RUNNING_TESTCASES       for each TestCase (ordered):
          → EXECUTING_STEP        for each Step:
              execute → StepResult
          → on_fail commands if TestCase failed
      → aggregate TestSetResult
  → COLLECTING_RESULTS        aggregate ConfigGroupResult
  → EXIT (write WorkerOutput to stdout)

IPC CONTRACT:
  stdin  → WorkerInput  (JSON, single line)
  stdout → WorkerOutput (JSON, single line)
  stderr → log output   (human readable)

  The Controller spawns this as a subprocess and communicates only via
  stdin/stdout. The Worker never writes to stdout except the final
  WorkerOutput JSON line.

WORKER CRASH / RETRY:
  If the Worker process exits non-zero, the Controller retries once.
  The Worker itself does not implement retry — it exits cleanly on any
  unrecoverable error, writing a WorkerOutput with SYSTEM or INFRA status.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional

from shared.enums import (
    FailureType, StepStatus,
    TestCaseStatus, TestSetStatus, WorkerState,
)
from shared.models.steps import ChannelWaitStep, ConsoleStep
from shared.models.testrig import (
    ConfigGroupResult, OnFailCommand,
    StepResult, TestCase, TestCaseResult, TestSetResult,
)
from shared.models.worker_io import ResolvedTestSet, WorkerInput, WorkerOutput
from shared.proto.client import DaemonClient, DiscoverResult, DeviceStatus
from worker.logger import WorkerLogger
from worker.step_engine import StepEngine


# ============================================================================
# Validation
# ============================================================================

def validate_testcase(
    tc: TestCase,
    discovery: DiscoverResult,
    log: WorkerLogger,
) -> List[str]:
    """
    Cross-check every step in a TestCase against the discovered
    channels and commands. Returns a list of error strings (empty = OK).
    """
    errors = []
    for step in tc.steps:
        if isinstance(step, ChannelWaitStep):
            if not discovery.has_channel(step.channel_name):
                errors.append(
                    f"Step {step.step_id}: channel '{step.channel_name}' "
                    f"not found on board"
                )
            else:
                num_fields = discovery.channel_num_fields(step.channel_name)
                if step.expected.channel_offset >= num_fields:
                    errors.append(
                        f"Step {step.step_id}: offset {step.expected.channel_offset} "
                        f"out of range for '{step.channel_name}' "
                        f"({num_fields} fields)"
                    )
        elif isinstance(step, ConsoleStep):
            if not discovery.has_command(step.command):
                errors.append(
                    f"Step {step.step_id}: command '{step.command}' "
                    f"not found on board"
                )
    return errors


# ============================================================================
# TestCase executor
# ============================================================================

def execute_testcase(
    tc: TestCase,
    engine: StepEngine,
    client: DaemonClient,
    log: WorkerLogger,
) -> TestCaseResult:
    """
    Execute all steps in a TestCase sequentially.
    Handles StopIfFail, on_fail commands, and result aggregation.
    """
    log.set_testcase(tc.test_case_id)
    t0           = time.monotonic()
    step_results: List[StepResult] = []
    stop_flag    = False

    for step in tc.steps:
        # Skip remaining steps if a prior step set stop_if_fail
        if stop_flag:
            step_results.append(StepResult(
                step_id=step.step_id,
                status=StepStatus.SKIPPED,
            ))
            continue

        result = engine.execute_step(step)
        step_results.append(result)

        # Set stop flag if this step failed and has stop_if_fail=True
        if (
            step.stop_if_fail
            and result.status in (StepStatus.FAILED, StepStatus.ERROR)
        ):
            log.warning(
                "Step %d stop_if_fail=True — skipping remaining steps",
                step.step_id,
            )
            stop_flag = True

    duration_ms = int((time.monotonic() - t0) * 1000)

    # Determine overall TestCase status
    statuses = [r.status for r in step_results]
    if all(s == StepStatus.PASSED for s in statuses):
        tc_status    = TestCaseStatus.PASSED
        failure_type = None
    elif any(s == StepStatus.ERROR for s in statuses):
        tc_status    = TestCaseStatus.ERROR
        # Pick the first infra/system failure type
        failure_type = next(
            (r.failure_type for r in step_results if r.status == StepStatus.ERROR),
            FailureType.INFRA,
        )
    elif any(s == StepStatus.FAILED for s in statuses):
        tc_status    = TestCaseStatus.FAILED
        failure_type = FailureType.TEST
    elif all(s == StepStatus.SKIPPED for s in statuses):
        tc_status    = TestCaseStatus.SKIPPED
        failure_type = None
    else:
        # Mix of PASSED and SKIPPED (stop_if_fail triggered after some pass)
        tc_status    = TestCaseStatus.FAILED
        failure_type = FailureType.TEST

    log.testcase_result(tc.test_case_id, tc_status.value, duration_ms)

    # Execute on_fail commands if TestCase failed or errored
    if tc_status in (TestCaseStatus.FAILED, TestCaseStatus.ERROR) and tc.on_fail:
        _run_on_fail(tc.on_fail, client, log)

    log.clear_testcase()

    return TestCaseResult(
        test_case_id=tc.test_case_id,
        status=tc_status,
        failure_type=failure_type,
        step_results=step_results,
        duration_ms=duration_ms,
    )


def _run_on_fail(
    commands: List[OnFailCommand],
    client: DaemonClient,
    log: WorkerLogger,
) -> None:
    """
    Execute on_fail commands to return board to a safe state.
    Errors here are logged but do not affect the TestCase result.
    """
    log.warning("Running on_fail commands (%d)", len(commands))
    for cmd in commands:
        try:
            result = client.send_command(
                device_id="monitoring_pcb",
                command=cmd.command,
                command_param=cmd.command_param or "",
                timeout_ms=5000,
            )
            log.info(
                "on_fail: %s %s → %s",
                cmd.command,
                cmd.command_param or "",
                result.actual_response,
            )
        except Exception as exc:
            log.error("on_fail command failed: %s — %s", cmd.command, exc)


# ============================================================================
# TestSet executor
# ============================================================================

def execute_testset(
    resolved: ResolvedTestSet,
    engine: StepEngine,
    client: DaemonClient,
    log: WorkerLogger,
    stop_on_channel_validation_error: bool = False,
    discovery: Optional[DiscoverResult] = None,
) -> TestSetResult:
    """
    Execute all TestCases in a TestSet sequentially (in order field).
    Validates each TestCase against discovery before running it.
    """
    ts = resolved.test_set
    log.set_testset(ts.test_set_id)
    t0 = time.monotonic()

    # Sort TestCases by order field
    ordered_refs  = sorted(ts.test_cases, key=lambda r: r.order)
    tc_by_id      = {tc.test_case_id: tc for tc in resolved.test_cases}

    tc_results: List[TestCaseResult] = []
    stop_set = False

    for ref in ordered_refs:
        tc = tc_by_id.get(ref.test_case_id)
        if tc is None:
            log.error("TestCase '%s' not found in resolved set", ref.test_case_id)
            tc_results.append(TestCaseResult(
                test_case_id=ref.test_case_id,
                status=TestCaseStatus.ERROR,
                failure_type=FailureType.INFRA,
                error_detail=f"TestCase '{ref.test_case_id}' not resolved",
            ))
            continue

        if stop_set:
            tc_results.append(TestCaseResult(
                test_case_id=tc.test_case_id,
                status=TestCaseStatus.SKIPPED,
            ))
            continue

        # --- Channel/command validation ---
        if discovery is not None:
            validation_errors = validate_testcase(tc, discovery, log)
            if validation_errors:
                detail = "; ".join(validation_errors)
                log.error(
                    "Validation failed for TestCase '%s': %s",
                    tc.test_case_id, detail,
                )
                tc_results.append(TestCaseResult(
                    test_case_id=tc.test_case_id,
                    status=TestCaseStatus.INVALID,
                    failure_type=FailureType.INVALID_TEST,
                    error_detail=f"TestCase definition invalid: {detail}",
                ))
                if stop_on_channel_validation_error:
                    log.error(
                        "stop_on_channel_validation_error=True — aborting TestSet"
                    )
                    stop_set = True
                continue   # skip to next TestCase

        # --- Execute ---
        tc_result = execute_testcase(tc, engine, client, log)
        tc_results.append(tc_result)

        # Stop remaining TestCases if TestSet has stop_on_failure
        if (
            ts.execution_constraints.stop_on_failure
            and tc_result.status in (TestCaseStatus.FAILED, TestCaseStatus.ERROR)
        ):
            log.warning(
                "TestSet stop_on_failure=True — skipping remaining TestCases"
            )
            stop_set = True

    duration_ms = int((time.monotonic() - t0) * 1000)

    # Aggregate TestSet status
    statuses = [r.status for r in tc_results]
    if all(s == TestCaseStatus.PASSED for s in statuses):
        ts_status    = TestSetStatus.PASSED
        failure_type = None
    elif all(s == TestCaseStatus.SKIPPED for s in statuses):
        ts_status    = TestSetStatus.PASSED   # nothing ran — treat as pass
        failure_type = None
    elif any(s in (TestCaseStatus.ERROR, TestCaseStatus.INVALID) for s in statuses):
        error_tcs    = [r for r in tc_results
                        if r.status in (TestCaseStatus.ERROR, TestCaseStatus.INVALID)]
        failure_type = next(
            (r.failure_type for r in error_tcs if r.failure_type == FailureType.INFRA),
            error_tcs[0].failure_type if error_tcs else FailureType.INFRA,
        )
        ts_status    = TestSetStatus.ERROR
    elif any(s == TestCaseStatus.FAILED for s in statuses):
        all_done = [s for s in statuses if s != TestCaseStatus.SKIPPED]
        if all(s == TestCaseStatus.FAILED for s in all_done):
            ts_status = TestSetStatus.FAILED
        else:
            ts_status = TestSetStatus.PARTIAL
        failure_type = FailureType.TEST
    else:
        ts_status    = TestSetStatus.PARTIAL
        failure_type = FailureType.TEST

    log.testset_result(ts.test_set_id, ts_status.value, duration_ms)

    return TestSetResult(
        test_set_id=ts.test_set_id,
        status=ts_status,
        failure_type=failure_type,
        test_case_results=tc_results,
        duration_ms=duration_ms,
    )


# ============================================================================
# Worker main
# ============================================================================

def run_worker(worker_input: WorkerInput) -> WorkerOutput:
    """
    Full Worker execution from WorkerInput → WorkerOutput.
    Called directly in tests. In production, called by main() below.
    """
    log = WorkerLogger(
        board_pair_id=worker_input.board_pair_id,
        board_config_id=worker_input.board_config_id,
    )
    log.info("Worker started | firmware=%s", worker_input.firmware_hash)

    ts_results: List[TestSetResult] = []
    discovery:  Optional[DiscoverResult] = None

    # -----------------------------------------------------------------------
    # Connect to daemon
    # -----------------------------------------------------------------------
    log.info("Connecting to daemon at %s", worker_input.daemon_address)
    client = DaemonClient(worker_input.daemon_address)

    try:
        client.connect()
    except Exception as exc:
        log.error("Failed to connect to daemon: %s", exc)
        return _system_fault_output(worker_input, f"Daemon connection failed: {exc}")

    try:
        ping = client.ping()
        if not ping.alive:
            return _system_fault_output(worker_input, "Daemon ping returned alive=False")
        log.info("Daemon alive | version=%s", ping.daemon_version)

        # -------------------------------------------------------------------
        # Device readiness (board identity already checked by Controller —
        # Worker does a lightweight ping-level check only)
        # -------------------------------------------------------------------
        log.info("Checking device ready")
        ready_result = client.check_device_ready(
            "monitoring_pcb", timeout_ms=10000
        )
        if not ready_result.ready:
            return _system_fault_output(
                worker_input,
                f"Device not ready: {ready_result.detail}",
            )
        log.info(
            "Device ready | identity=%s", ready_result.board_identity
        )

        # -------------------------------------------------------------------
        # Discovery
        # -------------------------------------------------------------------
        log.info("Running capability discovery")
        disc = client.discover_capabilities("monitoring_pcb")
        if disc.status != DeviceStatus.OK:
            return _system_fault_output(
                worker_input,
                f"Discovery failed: {disc.detail}",
            )
        log.info(
            "Discovery complete | %d channels, %d commands",
            len(disc.channels), len(disc.commands),
        )
        discovery = disc

        # -------------------------------------------------------------------
        # Execute TestSets
        # -------------------------------------------------------------------
        engine = StepEngine(client=client, log=log)

        for resolved in worker_input.testsets:
            ts_result = execute_testset(
                resolved=resolved,
                engine=engine,
                client=client,
                log=log,
                discovery=discovery,
            )
            ts_results.append(ts_result)

    except Exception as exc:
        log.error("Unhandled Worker exception: %s", exc, exc_info=True)
        return _system_fault_output(worker_input, f"Unhandled exception: {exc}")

    finally:
        client.close()

    # -----------------------------------------------------------------------
    # Aggregate ConfigGroupResult
    # -----------------------------------------------------------------------
    statuses = [r.status for r in ts_results]

    if all(s == TestSetStatus.PASSED for s in statuses):
        from shared.enums import TestRunStatus
        group_failure = None
    elif any(s == TestSetStatus.ERROR for s in statuses):
        group_failure = next(
            (r.failure_type for r in ts_results if r.status == TestSetStatus.ERROR),
            FailureType.INFRA,
        )
    else:
        group_failure = FailureType.TEST

    config_group_result = ConfigGroupResult(
        board_pair_id=worker_input.board_pair_id,
        board_config_id=worker_input.board_config_id,
        test_set_results=ts_results,
        failure_type=group_failure,
    )

    log.info(
        "Worker complete | %d testsets | failure_type=%s",
        len(ts_results),
        group_failure.value if group_failure else "None",
    )

    return WorkerOutput(
        board_pair_id=worker_input.board_pair_id,
        board_config_id=worker_input.board_config_id,
        result=config_group_result,
    )


def _system_fault_output(
    worker_input: WorkerInput, detail: str
) -> WorkerOutput:
    """Return a WorkerOutput signalling an unrecoverable system fault."""
    return WorkerOutput(
        board_pair_id=worker_input.board_pair_id,
        board_config_id=worker_input.board_config_id,
        result=ConfigGroupResult(
            board_pair_id=worker_input.board_pair_id,
            board_config_id=worker_input.board_config_id,
            failure_type=FailureType.SYSTEM,
            error_detail=detail,
        ),
    )


# ============================================================================
# Subprocess entry point
# ============================================================================

def main():
    """
    Called when the Worker is spawned as a subprocess by the Controller.

    Protocol:
      - Read one JSON line from stdin  → WorkerInput
      - Write one JSON line to stdout  → WorkerOutput
      - All other output goes to stderr (logs)
    """
    logging.basicConfig(
        level=logging.DEBUG,
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    raw = sys.stdin.readline()
    if not raw.strip():
        sys.stderr.write("Worker: empty stdin\n")
        sys.exit(1)

    try:
        worker_input = WorkerInput.model_validate_json(raw)
    except Exception as exc:
        sys.stderr.write(f"Worker: invalid WorkerInput JSON: {exc}\n")
        sys.exit(1)

    output = run_worker(worker_input)

    # Write result to stdout (single JSON line)
    sys.stdout.write(output.model_dump_json() + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
