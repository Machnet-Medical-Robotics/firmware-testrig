"""
tests/test_controller.py
Integration tests for the Test Run Controller (Phase 5).

Run with:
    python -m tests.test_controller
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from unittest.mock import patch

from hardware_daemon.mock_daemon import serve
from shared.enums import FailureType, TestRunStatus, TestSetStatus
from shared.models.testrig import (
    ExecutionPolicy, FirmwareRef, TestRun, TestRunMetadata, TestSetRef,
)
from controller.controller import TestRunController
from controller.grouper import group_testset_refs
from controller.loader import DefinitionLoader

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

PORT         = 50097
DAEMON_ADDR  = f"localhost:{PORT}"
DEFS_ROOT    = Path(__file__).parent.parent / "definitions"
PROJECT_ROOT = Path(__file__).parent.parent

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


def make_test_run(
    test_set_refs=None,
    abort_on_fault: bool = True,
    retry: int = 1,
) -> TestRun:
    return TestRun(
        test_run_id="TR-TEST-001",
        metadata=TestRunMetadata(requested_by="test"),
        firmware=FirmwareRef(repository="robot-firmware", commit_hash="a1b2c3d4"),
        execution_policy=ExecutionPolicy(
            max_parallel_board_pairs=1,
            retry_on_infra_failure=retry,
            abort_on_critical_infra_failure=abort_on_fault,
        ),
        test_set_refs=test_set_refs or [
            TestSetRef(test_set_id="TS-SHUTTLE-LEADSCREW",
                       board_config_id="SH1", priority=1),
        ],
    )


def make_controller(daemon_addr: str = DAEMON_ADDR) -> TestRunController:
    return TestRunController(
        definitions_root=DEFS_ROOT,
        daemon_address=daemon_addr,
        board_pair_id="pair_1",
        project_root=PROJECT_ROOT,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_definition_loader():
    print("\n[1] DefinitionLoader")
    loader = DefinitionLoader(DEFS_ROOT)
    loader.load_all()
    summary = loader.summary()

    for item, label in [
        ("TS-SHUTTLE-LEADSCREW", "testsets"),
        ("TC-SH-LEADSCREW-DRIVE", "testcases"),
    ]:
        if item in summary[label]:
            ok(f"{item} loaded")
        else:
            fail(f"{item} missing", str(summary[label]))

    cfg = loader.get_board_config("SH1")
    if cfg and cfg.dip_switch_hex() == "0xA3":
        ok(f"SH1 dip_switch_byte={cfg.dip_switch_hex()} identity='{cfg.expected_board_identity}'")
    else:
        fail("SH1 config", str(cfg))


def test_grouper():
    print("\n[2] Grouper — groups by board_config_id, sorted by priority")
    tr = make_test_run(test_set_refs=[
        TestSetRef(test_set_id="TS-A", board_config_id="SH1",   priority=2),
        TestSetRef(test_set_id="TS-B", board_config_id="SH1",   priority=1),
        TestSetRef(test_set_id="TS-C", board_config_id="CORE1", priority=1),
    ])
    groups = group_testset_refs(tr)

    if len(groups) == 2:
        ok("2 groups for 2 distinct board_config_ids")
    else:
        fail("Group count", f"expected 2 got {len(groups)}")

    sh1 = next((g for g in groups if g.board_config_id == "SH1"), None)
    if sh1 and len(sh1.testset_refs) == 2:
        ok("SH1 group has 2 TestSetRefs")
    else:
        fail("SH1 size")

    # TS-B (priority=1) should come before TS-A (priority=2)
    if sh1 and [r.test_set_id for r in sh1.testset_refs] == ["TS-B", "TS-A"]:
        ok("SH1 TestSetRefs sorted by priority ascending")
    else:
        fail("SH1 priority order",
             str([r.test_set_id for r in sh1.testset_refs] if sh1 else "None"))


def test_happy_path():
    print("\n[3] Happy path — full TestRun through Controller")
    ctrl   = make_controller()
    result = ctrl.run(make_test_run())

    if result.status == TestRunStatus.COMPLETED:
        ok(f"TestRun COMPLETED")
    else:
        fail("TestRun status", str(result.status))

    if len(result.config_group_results) == 1:
        ok("1 ConfigGroupResult")
    else:
        fail("ConfigGroupResult count", str(len(result.config_group_results)))

    group = result.config_group_results[0]
    if group.failure_type is None:
        ok("ConfigGroup no failure (all passed)")
    else:
        fail("ConfigGroup failure_type", str(group.failure_type))

    if group.test_set_results and group.test_set_results[0].status == TestSetStatus.PASSED:
        ok("TestSet PASSED")
    else:
        fail("TestSet status",
             str(group.test_set_results[0].status if group.test_set_results else "empty"))


def test_board_identity_mismatch():
    print("\n[4] Board identity mismatch → HARDWARE_FAIL")
    wrong_server = serve(port=50096, board_identity="wrong_board", boot_delay_ms=50)
    time.sleep(0.1)
    try:
        ctrl = TestRunController(
            definitions_root=DEFS_ROOT,
            daemon_address="localhost:50096",
            board_pair_id="pair_1",
            project_root=PROJECT_ROOT,
        )
        result = ctrl.run(make_test_run())
        group  = result.config_group_results[0] if result.config_group_results else None

        if group and group.failure_type == FailureType.HARDWARE:
            ok("HARDWARE_FAIL on identity mismatch")
        else:
            fail("Expected HARDWARE_FAIL",
                 str(group.failure_type if group else "no group"))

        if group and "identity mismatch" in (group.error_detail or "").lower():
            ok("Error detail mentions identity mismatch")
        else:
            fail("Error detail missing identity info",
                 group.error_detail if group else "")
    finally:
        wrong_server.stop(grace=0)


def test_missing_board_config():
    print("\n[5] Missing BoardConfig → INFRA_FAIL")
    tr = make_test_run(test_set_refs=[
        TestSetRef(test_set_id="TS-SHUTTLE-LEADSCREW",
                   board_config_id="NONEXISTENT", priority=1),
    ])
    result = make_controller().run(tr)
    group  = result.config_group_results[0] if result.config_group_results else None

    if group and group.failure_type == FailureType.INFRA:
        ok("Missing BoardConfig → INFRA_FAIL")
    else:
        fail("Expected INFRA_FAIL", str(group.failure_type if group else "no group"))


def test_missing_testset():
    print("\n[6] Missing TestSet → INFRA_FAIL")
    tr = make_test_run(test_set_refs=[
        TestSetRef(test_set_id="TS-DOES-NOT-EXIST",
                   board_config_id="SH1", priority=1),
    ])
    result = make_controller().run(tr)
    group  = result.config_group_results[0] if result.config_group_results else None

    if group and group.failure_type == FailureType.INFRA:
        ok("Missing TestSet → INFRA_FAIL")
    else:
        fail("Expected INFRA_FAIL", str(group.failure_type if group else "no group"))


def test_abort_on_system_fault():
    print("\n[7] Daemon unreachable → ABORTED (abort_on_critical_infra_failure=True)")
    ctrl   = TestRunController(
        definitions_root=DEFS_ROOT,
        daemon_address="localhost:59999",
        board_pair_id="pair_1",
        project_root=PROJECT_ROOT,
    )
    result = ctrl.run(make_test_run(abort_on_fault=True))

    if result.status == TestRunStatus.ABORTED:
        ok("TestRun ABORTED on daemon failure")
    else:
        fail("Expected ABORTED", str(result.status))


def test_worker_retry_succeeds():
    print("\n[8] Worker retry — fails once, succeeds on retry")
    from controller import worker_runner

    call_count = [0]
    original   = worker_runner._spawn_once

    def patched(input_json, project_root, attempt):
        call_count[0] += 1
        if call_count[0] == 1:
            from controller.worker_runner import WorkerRunResult
            return WorkerRunResult(
                success=False, attempt=1,
                failure_type=FailureType.INFRA,
                detail="Simulated first-attempt failure",
            )
        return original(input_json, project_root, attempt)

    with patch.object(worker_runner, "_spawn_once", patched):
        result = make_controller().run(make_test_run(retry=1))

    group = result.config_group_results[0] if result.config_group_results else None

    if group and group.worker_retries == 1:
        ok("worker_retries=1 recorded")
    else:
        fail("worker_retries", str(group.worker_retries if group else "no group"))

    if group and group.failure_type is None:
        ok("ConfigGroup succeeded after retry")
    else:
        fail("ConfigGroup should have no failure", str(group.failure_type if group else ""))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  Phase 5 — Controller Integration Tests")
    print("=" * 60)

    server = serve(port=PORT, board_identity="shuttle", boot_delay_ms=100)
    time.sleep(0.2)

    try:
        test_definition_loader()
        test_grouper()
        test_happy_path()
        test_board_identity_mismatch()
        test_missing_board_config()
        test_missing_testset()
        test_abort_on_system_fault()
        test_worker_retry_succeeds()
    finally:
        server.stop(grace=0)

    total = passed + failed
    print(f"\n{'='*60}")
    print(f"  Phase 5 Tests: {passed}/{total} passed")
    print(f"{'='*60}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
