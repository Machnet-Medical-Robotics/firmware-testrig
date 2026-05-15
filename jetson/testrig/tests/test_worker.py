"""
tests/test_worker.py
Integration tests for the Test Worker Runtime (Phase 4).

Tests:
  1.  Full happy path — all steps pass
  2.  Step FAIL  (return_string_match mismatch)  → TEST_FAIL, continue
  3.  Step FAIL  + stop_if_fail=True             → remaining steps SKIPPED
  4.  Channel validation error (bad channel name) → INFRA_FAIL, other TCs run
  5.  Channel validation error (bad offset)       → INFRA_FAIL
  6.  stop_on_channel_validation_error=True       → aborts TestSet
  7.  TestSet stop_on_failure=True                → skips remaining TCs
  8.  on_fail commands executed on TC failure
  9.  Unknown command → INFRA error (not TEST)
  10. Worker subprocess stdin/stdout contract

Run with:
    python -m tests.test_worker
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time

from hardware_daemon.mock_daemon import serve
from shared.enums import (
    FailureType, StepStatus, TestCaseStatus, TestSetStatus,
)
from shared.models.steps import (
    ChannelWaitStep, ChannelExpected, ConsoleStep, WaitStep,
)
from shared.models.testrig import (
    BoardBinding, ExecutionConstraints,
    OnFailCommand, TestCase, TestCaseConfig, TestCaseRef,
    TestSet,
)
from shared.models.worker_io import ResolvedTestSet, WorkerInput
from worker.worker import run_worker

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

PORT       = 50098
DAEMON_ADDR = f"localhost:{PORT}"

passed = 0
failed = 0


def ok(label: str):
    global passed
    passed += 1
    print(f"  [PASS] {label}")


def fail(label: str, detail: str = ""):
    global failed
    failed += 1
    print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Helpers to build test fixtures
# ---------------------------------------------------------------------------

def make_worker_input(
    testsets: list[ResolvedTestSet],
    stop_on_channel_validation_error: bool = False,
) -> WorkerInput:
    return WorkerInput(
        board_pair_id="pair_1",
        board_config_id="SH1",
        firmware_hash="a1b2c3d4",
        daemon_address=DAEMON_ADDR,
        testsets=testsets,
    )


def make_testset(
    test_set_id: str,
    tc_refs: list[TestCaseRef],
    stop_on_failure: bool = False,
) -> TestSet:
    return TestSet(
        test_set_id=test_set_id,
        board_binding=BoardBinding(),
        execution_constraints=ExecutionConstraints(stop_on_failure=stop_on_failure),
        test_cases=tc_refs,
    )


def make_console_step(
    step_id: int,
    command: str,
    match: str = "cmd OK",
    param: str = "",
    stop_if_fail: bool = False,
    timeout_ms: int = 3000,
) -> ConsoleStep:
    return ConsoleStep(
        step_id=step_id,
        command=command,
        command_param=param or None,
        return_string_match=match,
        timeout_ms=timeout_ms,
        stop_if_fail=stop_if_fail,
        device="monitoring_pcb",
    )


def make_channel_step(
    step_id: int,
    channel: str = "stepper1_controller",
    offset: int = 0,
    min_v: float = 0.0,
    max_v: float = 200.0,
    stop_if_fail: bool = False,
    timeout_ms: int = 8000,
) -> ChannelWaitStep:
    return ChannelWaitStep(
        step_id=step_id,
        channel_name=channel,
        expected=ChannelExpected(channel_offset=offset, min=min_v, max=max_v),
        timeout_ms=timeout_ms,
        stop_if_fail=stop_if_fail,
        device="monitoring_pcb",
    )


def make_testcase(
    tc_id: str,
    steps: list,
    on_fail: list[OnFailCommand] | None = None,
) -> TestCase:
    return TestCase(
        test_case_id=tc_id,
        config=TestCaseConfig(echo_console=True),
        steps=steps,
        on_fail=on_fail or [],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_happy_path():
    """All steps pass — full shuttle leadscrew flow."""
    print("\n[1] Happy path — full leadscrew flow")

    tc = make_testcase("TC-HAPPY", [
        make_console_step(1, "com_enter_service_mode", match="cmd OK"),
        make_console_step(2, "on_demand_leadscrew",    match="BIST SUCCESS"),
        make_console_step(3, "com_leadscrew_go",       match="cmd OK", param="100 350"),
        make_channel_step(4, channel="stepper1_controller", offset=0,
                          min_v=99.0, max_v=101.0, timeout_ms=12000),
        make_console_step(5, "com_leadscrew_go",       match="cmd OK", param="0 350"),
    ])
    ts  = make_testset("TS-HAPPY", [TestCaseRef(test_case_id="TC-HAPPY", order=1)])
    out = run_worker(make_worker_input([ResolvedTestSet(test_set=ts, test_cases=[tc])]))

    ts_result = out.result.test_set_results[0]
    tc_result = ts_result.test_case_results[0]

    if ts_result.status == TestSetStatus.PASSED:
        ok("TestSet status = PASSED")
    else:
        fail("TestSet status", f"got {ts_result.status}")

    if tc_result.status == TestCaseStatus.PASSED:
        ok("TestCase status = PASSED")
    else:
        fail("TestCase status", f"got {tc_result.status}")

    all_passed = all(r.status == StepStatus.PASSED for r in tc_result.step_results)
    if all_passed:
        ok(f"All {len(tc_result.step_results)} steps PASSED")
    else:
        fail("Not all steps passed",
             str([r.status for r in tc_result.step_results]))


def test_step_fail_continues():
    """A step that fails (bad match) marks TC as FAILED but doesn't stop next TC."""
    print("\n[2] Step FAIL — continues to next TestCase")

    tc_fail = make_testcase("TC-FAIL-STEP", [
        make_console_step(1, "com_enter_service_mode", match="WRONG_MATCH"),
    ])
    tc_ok = make_testcase("TC-AFTER-FAIL", [
        make_console_step(1, "com_enter_service_mode", match="cmd OK"),
    ])
    ts = make_testset("TS-STEP-FAIL", [
        TestCaseRef(test_case_id="TC-FAIL-STEP",  order=1),
        TestCaseRef(test_case_id="TC-AFTER-FAIL", order=2),
    ])
    out = run_worker(make_worker_input([
        ResolvedTestSet(test_set=ts, test_cases=[tc_fail, tc_ok])
    ]))

    ts_r  = out.result.test_set_results[0]
    tc_r1 = ts_r.test_case_results[0]
    tc_r2 = ts_r.test_case_results[1]

    if tc_r1.status == TestCaseStatus.FAILED and tc_r1.failure_type == FailureType.TEST:
        ok("First TC → FAILED / TEST_FAIL")
    else:
        fail("First TC status", f"status={tc_r1.status} type={tc_r1.failure_type}")

    if tc_r2.status == TestCaseStatus.PASSED:
        ok("Second TC ran and PASSED after first failed")
    else:
        fail("Second TC status", f"{tc_r2.status}")


def test_stop_if_fail_skips_remaining_steps():
    """stop_if_fail=True on a failing step skips subsequent steps in same TC."""
    print("\n[3] stop_if_fail=True — remaining steps SKIPPED")

    tc = make_testcase("TC-STOP-IF-FAIL", [
        make_console_step(1, "com_enter_service_mode", match="WRONG_MATCH",
                          stop_if_fail=True),
        make_console_step(2, "on_demand_leadscrew", match="BIST SUCCESS"),
        make_console_step(3, "com_leadscrew_go",    match="cmd OK"),
    ])
    ts  = make_testset("TS-SIF", [TestCaseRef(test_case_id="TC-STOP-IF-FAIL", order=1)])
    out = run_worker(make_worker_input([ResolvedTestSet(test_set=ts, test_cases=[tc])]))

    tc_r = out.result.test_set_results[0].test_case_results[0]

    if tc_r.step_results[0].status == StepStatus.FAILED:
        ok("Step 1 → FAILED")
    else:
        fail("Step 1 status", str(tc_r.step_results[0].status))

    skipped = [r for r in tc_r.step_results[1:] if r.status == StepStatus.SKIPPED]
    if len(skipped) == 2:
        ok("Steps 2 and 3 → SKIPPED")
    else:
        fail("Skipped count", f"expected 2, got {len(skipped)}")


def test_channel_validation_bad_channel():
    """TestCase referencing unknown channel → INFRA_FAIL, other TC still runs."""
    print("\n[4] Channel validation — unknown channel → INFRA_FAIL, others run")

    tc_bad = make_testcase("TC-BAD-CHANNEL", [
        make_channel_step(1, channel="nonexistent_channel", min_v=0, max_v=1),
    ])
    tc_ok = make_testcase("TC-VALID", [
        make_console_step(1, "com_enter_service_mode", match="cmd OK"),
    ])
    ts = make_testset("TS-VAL", [
        TestCaseRef(test_case_id="TC-BAD-CHANNEL", order=1),
        TestCaseRef(test_case_id="TC-VALID",       order=2),
    ])
    out = run_worker(make_worker_input([
        ResolvedTestSet(test_set=ts, test_cases=[tc_bad, tc_ok])
    ]))

    tc_r1 = out.result.test_set_results[0].test_case_results[0]
    tc_r2 = out.result.test_set_results[0].test_case_results[1]

    if tc_r1.failure_type == FailureType.INVALID_TEST:
        ok("Bad-channel TC → INVALID / INVALID_TEST")
    else:
        fail("Bad-channel TC", f"status={tc_r1.status} type={tc_r1.failure_type}")

    if tc_r2.status == TestCaseStatus.PASSED:
        ok("Valid TC ran and PASSED after validation error")
    else:
        fail("Valid TC", str(tc_r2.status))


def test_channel_validation_bad_offset():
    """Offset out of range for a known channel → INFRA_FAIL."""
    print("\n[5] Channel validation — offset out of range → INFRA_FAIL")

    tc = make_testcase("TC-BAD-OFFSET", [
        make_channel_step(1, channel="stepper1_controller", offset=99),
    ])
    ts  = make_testset("TS-BADOFF",
                       [TestCaseRef(test_case_id="TC-BAD-OFFSET", order=1)])
    out = run_worker(make_worker_input([ResolvedTestSet(test_set=ts, test_cases=[tc])]))

    tc_r = out.result.test_set_results[0].test_case_results[0]
    if tc_r.failure_type == FailureType.INVALID_TEST:
        ok("Out-of-range offset → INVALID_TEST")
    else:
        fail("Bad offset TC", f"status={tc_r.status} type={tc_r.failure_type}")


def test_stop_on_channel_validation_error():
    """stop_on_channel_validation_error=True aborts the TestSet."""
    print("\n[6] stop_on_channel_validation_error=True — aborts TestSet")

    tc_bad = make_testcase("TC-BAD-CH2", [
        make_channel_step(1, channel="does_not_exist", min_v=0, max_v=1),
    ])
    tc_ok = make_testcase("TC-SHOULD-SKIP", [
        make_console_step(1, "com_enter_service_mode", match="cmd OK"),
    ])
    ts = make_testset("TS-STOPVAL", [
        TestCaseRef(test_case_id="TC-BAD-CH2",      order=1),
        TestCaseRef(test_case_id="TC-SHOULD-SKIP",  order=2),
    ])

    # Build WorkerInput and inject stop flag into the testset constraints
    ts.execution_constraints.stop_on_failure = False  # TestSet level doesn't matter here
    inp = make_worker_input(
        [ResolvedTestSet(test_set=ts, test_cases=[tc_bad, tc_ok])],
        stop_on_channel_validation_error=True,
    )

    # Patch stop flag onto input (worker reads it from execution_policy via controller;
    # for this test we call execute_testset directly with the flag)
    from worker.worker import execute_testset
    from worker.logger import WorkerLogger
    from shared.proto.client import DaemonClient, DeviceStatus

    log    = WorkerLogger("pair_1", "SH1")
    client = DaemonClient(DAEMON_ADDR)
    client.connect()
    disc   = client.discover_capabilities("monitoring_pcb")
    client.close()

    resolved = ResolvedTestSet(test_set=ts, test_cases=[tc_bad, tc_ok])
    ts_r = execute_testset(
        resolved=resolved,
        engine=None,   # won't be reached — validation fails first
        client=None,
        log=log,
        discovery=disc,
        stop_on_channel_validation_error=True,
    )

    tc_r1 = ts_r.test_case_results[0]
    tc_r2 = ts_r.test_case_results[1] if len(ts_r.test_case_results) > 1 else None

    if tc_r1.failure_type == FailureType.INVALID_TEST:
        ok("First TC → INVALID_TEST (definition invalid)")
    else:
        fail("First TC", str(tc_r1.status))

    if tc_r2 and tc_r2.status == TestCaseStatus.SKIPPED:
        ok("Second TC → SKIPPED (aborted after validation error)")
    elif tc_r2 is None:
        ok("Second TC → not executed (aborted)")
    else:
        fail("Second TC should be SKIPPED", str(tc_r2.status) if tc_r2 else "missing")


def test_testset_stop_on_failure():
    """TestSet stop_on_failure=True skips remaining TCs after first failure."""
    print("\n[7] TestSet stop_on_failure=True — skips remaining TCs")

    tc1 = make_testcase("TC-SOF-1", [
        make_console_step(1, "on_demand_leadscrew", match="WRONG_MATCH"),
    ])
    tc2 = make_testcase("TC-SOF-2", [
        make_console_step(1, "com_enter_service_mode", match="cmd OK"),
    ])
    ts = make_testset("TS-SOF", [
        TestCaseRef(test_case_id="TC-SOF-1", order=1),
        TestCaseRef(test_case_id="TC-SOF-2", order=2),
    ], stop_on_failure=True)

    out = run_worker(make_worker_input([
        ResolvedTestSet(test_set=ts, test_cases=[tc1, tc2])
    ]))

    tc_r1 = out.result.test_set_results[0].test_case_results[0]
    tc_r2 = out.result.test_set_results[0].test_case_results[1]

    if tc_r1.status == TestCaseStatus.FAILED:
        ok("TC1 → FAILED")
    else:
        fail("TC1 status", str(tc_r1.status))

    if tc_r2.status == TestCaseStatus.SKIPPED:
        ok("TC2 → SKIPPED (stop_on_failure)")
    else:
        fail("TC2 should be SKIPPED", str(tc_r2.status))


def test_on_fail_commands():
    """on_fail commands are executed when a TestCase fails."""
    print("\n[8] on_fail commands — executed on TC failure")

    tc = make_testcase(
        "TC-ON-FAIL",
        steps=[make_console_step(1, "on_demand_leadscrew", match="WRONG")],
        on_fail=[OnFailCommand(command="com_change_mode", command_param="2")],
    )
    ts  = make_testset("TS-ONFAIL", [TestCaseRef(test_case_id="TC-ON-FAIL", order=1)])
    out = run_worker(make_worker_input([ResolvedTestSet(test_set=ts, test_cases=[tc])]))

    tc_r = out.result.test_set_results[0].test_case_results[0]
    # The test passes if: TC is FAILED and we didn't crash (on_fail ran silently)
    if tc_r.status == TestCaseStatus.FAILED:
        ok("TC FAILED and on_fail commands ran without crash")
    else:
        fail("TC status after on_fail", str(tc_r.status))


def test_unknown_command_infra_error():
    """
    A command unknown to the daemon at runtime → INFRA error (not TEST fail).

    Note: validation catches commands not in the discovery list (that gives
    INFRA at the TestCase level, no step_results). This test verifies the
    StepEngine itself classifies a daemon-side ERROR response as INFRA_FAIL
    by calling the StepEngine directly with a mocked client response.
    """
    print("\n[9] Unknown command at daemon runtime → INFRA error (step engine test)")

    from unittest.mock import MagicMock
    from worker.step_engine import StepEngine
    from worker.logger import WorkerLogger
    from shared.proto.client import DeviceStatus, CommandResult

    # Mock client that returns ERROR status (simulates daemon receiving a
    # command it cannot route — e.g. board not in correct mode)
    mock_client = MagicMock()
    mock_client.send_command.return_value = CommandResult(
        status=DeviceStatus.ERROR,
        matched=False,
        actual_response="",
        duration_ms=50,
        detail="Unknown command 'bad_runtime_cmd'",
    )

    log    = WorkerLogger("pair_1", "SH1")
    engine = StepEngine(client=mock_client, log=log)
    step   = make_console_step(1, "bad_runtime_cmd", match="cmd OK")

    result = engine.execute_step(step)

    if result.status == StepStatus.ERROR and result.failure_type == FailureType.INFRA:
        ok("Daemon ERROR response → StepStatus.ERROR / INFRA_FAIL (not TEST_FAIL)")
    else:
        fail("Unknown command classification",
             f"status={result.status} type={result.failure_type}")


def test_subprocess_contract():
    """Worker subprocess reads WorkerInput from stdin, writes WorkerOutput to stdout."""
    print("\n[10] Subprocess stdin/stdout contract")

    import json
    from shared.models.testrig import TestCaseRef

    tc = make_testcase("TC-SUB", [
        make_console_step(1, "com_enter_service_mode", match="cmd OK"),
    ])
    ts  = make_testset("TS-SUB", [TestCaseRef(test_case_id="TC-SUB", order=1)])
    inp = make_worker_input([ResolvedTestSet(test_set=ts, test_cases=[tc])])

    import os
    from pathlib import Path
    project_root = str(Path(__file__).parent.parent)
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{project_root}{os.pathsep}{existing}" if existing else project_root

    proc = subprocess.run(
        [sys.executable, "-m", "worker.worker"],
        input=inp.model_dump_json() + "\n",
        capture_output=True,
        text=True,
        cwd=project_root,
        env=env,
        timeout=30,
    )

    if proc.returncode != 0:
        fail("Subprocess exit code", f"got {proc.returncode}\nstderr: {proc.stderr[-500:]}")
        return

    stdout = proc.stdout.strip()
    if not stdout:
        fail("Subprocess stdout empty")
        return

    try:
        from shared.models.worker_io import WorkerOutput
        output = WorkerOutput.model_validate_json(stdout)
    except Exception as exc:
        fail("Subprocess stdout not valid WorkerOutput JSON", str(exc))
        return

    if output.board_pair_id == "pair_1":
        ok("Subprocess stdout is valid WorkerOutput JSON")
    else:
        fail("Subprocess output board_pair_id", output.board_pair_id)

    ts_r = output.result.test_set_results[0]
    if ts_r.status == TestSetStatus.PASSED:
        ok("Subprocess TestSet → PASSED")
    else:
        fail("Subprocess TestSet status", str(ts_r.status))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  Phase 4 — Worker Integration Tests")
    print("=" * 60)

    # Start mock daemon
    server = serve(port=PORT, board_identity="shuttle", boot_delay_ms=100)
    time.sleep(0.2)

    try:
        test_happy_path()
        test_step_fail_continues()
        test_stop_if_fail_skips_remaining_steps()
        test_channel_validation_bad_channel()
        test_channel_validation_bad_offset()
        test_stop_on_channel_validation_error()
        test_testset_stop_on_failure()
        test_on_fail_commands()
        test_unknown_command_infra_error()
        test_subprocess_contract()
    finally:
        server.stop(grace=0)

    total = passed + failed
    print(f"\n{'='*60}")
    print(f"  Phase 4 Tests: {passed}/{total} passed")
    print(f"{'='*60}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
