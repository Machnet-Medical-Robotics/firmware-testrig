"""
tests/test_end_to_end.py
End-to-end tests covering the full system pipeline in two modes.

MODE A — Without Manager (direct Controller path):
  JSON file → TestRunController.run() → TestRunResult
  This always works, even before Phase 6 (Manager) is built.
  Useful for testing Controller + Worker + Daemon in isolation.

MODE B — With Manager (full system path):
  JSON file → TestRigManager.run_json() → TestRunResult + report file
  CSV string → TestRigManager.run_csv() → TestRunResult + report file
  Tests the complete production flow including ingestion and reporting.

Both modes use the same mock daemon and real definitions/ files.
Both are self-contained — no external daemon process needed.

Run:
    python -m tests.test_end_to_end               # all tests
    python -m tests.test_end_to_end --no-manager  # skip Manager tests
    python -m tests.test_end_to_end --manager-only

On Windows (venv active, from testrig/):
    python -m tests.test_end_to_end
On Linux:
    python -m tests.test_end_to_end
    # or with explicit PYTHONPATH if not installed editable:
    PYTHONPATH=. python3 -m tests.test_end_to_end
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import time
from pathlib import Path

from hardware_daemon.mock_daemon import serve
from shared.models.testrig import TestRun
from shared.enums import TestRunStatus, TestSetStatus, TestCaseStatus
from controller.controller import TestRunController

logging.basicConfig(
    level=logging.WARNING,
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

DEFS_ROOT    = Path(__file__).parent.parent / "definitions"
PROJECT_ROOT = Path(__file__).parent.parent
PORT         = 50095
DAEMON_ADDR  = f"localhost:{PORT}"

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


# ============================================================================
# Shared validator — used by both Mode A and Mode B tests
# ============================================================================

def assert_shuttle_result(result, label_prefix: str = ""):
    """
    Validate a TestRunResult from the shuttle leadscrew TestRun.
    Reused by both Controller-direct and Manager tests so we assert
    the same things regardless of which entry point was used.
    """
    prefix = f"{label_prefix}: " if label_prefix else ""

    if result.status == TestRunStatus.COMPLETED:
        ok(f"{prefix}TestRun status = COMPLETED")
    else:
        fail(f"{prefix}TestRun status", result.status.value)

    if result.system_fault_detail is None:
        ok(f"{prefix}No system fault")
    else:
        fail(f"{prefix}System fault", result.system_fault_detail)

    if result.firmware_commit_hash == "a1b2c3d4":
        ok(f"{prefix}Firmware hash = a1b2c3d4")
    else:
        fail(f"{prefix}Firmware hash", result.firmware_commit_hash)

    if len(result.config_group_results) == 1:
        ok(f"{prefix}1 ConfigGroup")
    else:
        fail(f"{prefix}ConfigGroup count", str(len(result.config_group_results)))
        return

    group = result.config_group_results[0]
    if group.failure_type is None:
        ok(f"{prefix}ConfigGroup no failure")
    else:
        fail(f"{prefix}ConfigGroup failure", str(group.failure_type))

    if not group.test_set_results:
        fail(f"{prefix}No TestSetResults")
        return

    ts = group.test_set_results[0]
    if ts.status == TestSetStatus.PASSED:
        ok(f"{prefix}TestSet PASSED")
    else:
        fail(f"{prefix}TestSet status", ts.status.value)

    if not ts.test_case_results:
        fail(f"{prefix}No TestCaseResults")
        return

    tc = ts.test_case_results[0]
    if tc.status == TestCaseStatus.PASSED:
        ok(f"{prefix}TestCase PASSED")
    else:
        fail(f"{prefix}TestCase status", tc.status.value)

    if len(tc.step_results) == 6:
        ok(f"{prefix}6 steps executed")
    else:
        fail(f"{prefix}Step count", str(len(tc.step_results)))

    if all(s.status.value == "PASSED" for s in tc.step_results):
        ok(f"{prefix}All 6 steps PASSED")
    else:
        bad = [(s.step_id, s.status.value) for s in tc.step_results
               if s.status.value != "PASSED"]
        fail(f"{prefix}Failed steps", str(bad))


# ============================================================================
# MODE A — Direct Controller (no Manager)
# Works without Phase 6. This is what you ran before Phase 6 was built.
# ============================================================================

def test_direct_controller_json():
    """
    Load TR-2026-05-00001.json, build TestRun manually,
    pass directly to TestRunController.run().
    No Manager involved.
    """
    print("\n[A1] Direct Controller — JSON file → TestRunController → Result")

    path = DEFS_ROOT / "testruns" / "TR-2026-05-00001.json"
    if not path.exists():
        fail("TestRun JSON file not found", str(path))
        return

    test_run = TestRun(**json.loads(path.read_text(encoding="utf-8")))
    ok(f"TestRun parsed: {test_run.test_run_id}")

    ctrl   = TestRunController(
        definitions_root=DEFS_ROOT,
        daemon_address=DAEMON_ADDR,
        board_pair_id="pair_1",
        project_root=PROJECT_ROOT,
    )
    result = ctrl.run(test_run)
    assert_shuttle_result(result, "direct")


def test_direct_result_serialisable():
    """
    TestRunResult from direct Controller run must serialise to JSON cleanly.
    This is what the Manager's reporter will write to disk in Mode B.
    """
    print("\n[A2] Direct Controller — result round-trip JSON serialisation")

    path     = DEFS_ROOT / "testruns" / "TR-2026-05-00001.json"
    test_run = TestRun(**json.loads(path.read_text(encoding="utf-8")))
    ctrl     = TestRunController(
        definitions_root=DEFS_ROOT,
        daemon_address=DAEMON_ADDR,
        board_pair_id="pair_1",
        project_root=PROJECT_ROOT,
    )
    result = ctrl.run(test_run)

    try:
        result_json = result.model_dump_json(indent=2)
        ok("model_dump_json() succeeded")
    except Exception as exc:
        fail("model_dump_json() failed", str(exc))
        return

    try:
        from shared.models.testrig import TestRunResult
        reparsed = TestRunResult.model_validate_json(result_json)
        ok("Round-trip JSON parse succeeded")
    except Exception as exc:
        fail("Round-trip parse failed", str(exc))
        return

    if reparsed.test_run_id == result.test_run_id:
        ok("Round-trip test_run_id matches")
    else:
        fail("test_run_id mismatch")

    data = json.loads(result_json)
    print("\n  JSON structure:")
    for k, v in data.items():
        if isinstance(v, list):
            print(f"    {k}: [{len(v)} item(s)]")
        else:
            print(f"    {k}: {v}")


# ============================================================================
# MODE B — With Manager (full production flow)
# Requires Phase 6 (manager/ package).
# ============================================================================

def test_manager_from_json():
    """
    Full system flow: JSON file → Manager.run_json() → result + report file.
    """
    print("\n[B1] Manager — JSON file → Manager.run_json() → Result + Report")

    from manager.manager import TestRigManager

    with tempfile.TemporaryDirectory() as tmp:
        mgr    = TestRigManager(
            definitions_root=DEFS_ROOT,
            reports_dir=Path(tmp),
            daemon_address=DAEMON_ADDR,
            board_pair_id="pair_1",
            project_root=PROJECT_ROOT,
        )
        path   = DEFS_ROOT / "testruns" / "TR-2026-05-00001.json"
        result = mgr.run_json(path)

        assert_shuttle_result(result, "manager-json")

        # Verify report file was written
        reports = list(Path(tmp).glob("*.json"))
        if reports:
            ok(f"Report file written: {reports[0].name}")
        else:
            fail("No report file found in reports dir")
            return

        if result.test_run_id in reports[0].name:
            ok("Report filename contains test_run_id")
        else:
            fail("test_run_id not in report filename", reports[0].name)

        if result.status.value in reports[0].name:
            ok("Report filename contains status")
        else:
            fail("Status not in report filename", reports[0].name)

        # Load the report back and verify
        from manager.reporter import Reporter
        reporter = Reporter(Path(tmp))
        loaded   = reporter.load(reports[0])
        if loaded.test_run_id == result.test_run_id:
            ok("Loaded report test_run_id matches")
        else:
            fail("Loaded report mismatch", loaded.test_run_id)


def test_manager_from_csv():
    """
    Full system flow: CSV string → Manager.run_csv() → result + report.
    This is how ad-hoc runs will be submitted in production.
    """
    print("\n[B2] Manager — CSV string → Manager.run_csv() → Result + Report")

    from manager.manager import TestRigManager

    csv_content = (
        "FirmwareHash,TestSetId,BoardConfigId\n"
        "a1b2c3d4,TS-SHUTTLE-LEADSCREW,SH1\n"
    )

    with tempfile.TemporaryDirectory() as tmp:
        mgr    = TestRigManager(
            definitions_root=DEFS_ROOT,
            reports_dir=Path(tmp),
            daemon_address=DAEMON_ADDR,
            board_pair_id="pair_1",
            project_root=PROJECT_ROOT,
        )
        result = mgr.run_csv(csv_content, requested_by="e2e-test")

        assert_shuttle_result(result, "manager-csv")

        if result.firmware_commit_hash == "a1b2c3d4":
            ok("Firmware hash from CSV propagated to result")
        else:
            fail("Firmware hash", result.firmware_commit_hash)

        reports = list(Path(tmp).glob("*.json"))
        if reports:
            ok(f"CSV run report written: {reports[0].name}")
        else:
            fail("No report file found")


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="TestRig end-to-end tests")
    parser.add_argument("--no-manager",   action="store_true",
                        help="Skip Mode B (Manager) tests")
    parser.add_argument("--manager-only", action="store_true",
                        help="Skip Mode A (direct Controller) tests")
    args = parser.parse_args()

    run_direct  = not args.manager_only
    run_manager = not args.no_manager

    print("=" * 60)
    print("  End-to-End Tests")
    print("=" * 60)
    if run_direct and run_manager:
        print("  Running: Mode A (direct) + Mode B (manager)")
    elif run_direct:
        print("  Running: Mode A only (direct Controller)")
    else:
        print("  Running: Mode B only (Manager)")
    print("  (self-contained: starts own mock daemon)")
    print()

    server = serve(port=PORT, board_identity="shuttle", boot_delay_ms=100)
    time.sleep(0.2)

    try:
        if run_direct:
            test_direct_controller_json()
            test_direct_result_serialisable()

        if run_manager:
            test_manager_from_json()
            test_manager_from_csv()

    finally:
        server.stop(grace=0)

    total = passed + failed
    print(f"\n{'='*60}")
    print(f"  End-to-End Tests: {passed}/{total} passed")
    print(f"{'='*60}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
