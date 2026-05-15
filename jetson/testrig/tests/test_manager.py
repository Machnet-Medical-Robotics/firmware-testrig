"""
tests/test_manager.py
Phase 6 tests — TestRig Manager.

Tests:
  1.  ingest_json — valid TestRun JSON file
  2.  ingest_json — file not found raises IngestionError
  3.  ingest_json — malformed JSON raises IngestionError
  4.  ingest_csv  — valid CSV string
  5.  ingest_csv  — missing required column raises IngestionError
  6.  ingest_csv  — inconsistent firmware hash raises IngestionError
  7.  ingest_csv  — CSV file path
  8.  validate    — empty TestSetRefs raises ValidationError
  9.  validate    — duplicate TestSetRef IDs raises ValidationError
  10. queue       — enqueue, dequeue, priority ordering
  11. reporter    — write and load report
  12. Manager.run_json — full flow with mock daemon
  13. Manager.run_csv  — full flow from CSV string
  14. Manager.run_queue — multiple runs processed in priority order

Run with:
    python -m tests.test_manager
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
import time
from pathlib import Path

from hardware_daemon.mock_daemon import serve
from manager.ingestor import IngestionError, ingest_csv, ingest_json
from manager.manager import TestRigManager, ValidationError
from manager.queue import TestRunQueue
from manager.reporter import Reporter
from shared.enums import TestRunStatus
from shared.models.testrig import (
    ExecutionPolicy, FirmwareRef, TestRun,
    TestRunMetadata, TestSetRef, TestRunResult,
)

logging.basicConfig(
    level=logging.WARNING,
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

DEFS_ROOT    = Path(__file__).parent.parent / "definitions"
PROJECT_ROOT = Path(__file__).parent.parent
PORT         = 50094
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


def make_manager(daemon_addr: str = DAEMON_ADDR) -> TestRigManager:
    return TestRigManager(
        definitions_root=DEFS_ROOT,
        reports_dir=Path(tempfile.mkdtemp()),
        daemon_address=daemon_addr,
        board_pair_id="pair_1",
        project_root=PROJECT_ROOT,
    )


# ---------------------------------------------------------------------------
# Ingestor tests
# ---------------------------------------------------------------------------

def test_ingest_json_valid():
    print("\n[1] ingest_json — valid file")
    path = DEFS_ROOT / "testruns" / "TR-2026-05-00001.json"
    tr = ingest_json(path)
    if tr.test_run_id == "TR-2026-05-00001":
        ok("test_run_id correct")
    else:
        fail("test_run_id", tr.test_run_id)
    if len(tr.test_set_refs) == 1:
        ok("1 TestSetRef loaded")
    else:
        fail("TestSetRef count", str(len(tr.test_set_refs)))
    if tr.firmware.commit_hash == "a1b2c3d4":
        ok("firmware hash correct")
    else:
        fail("firmware hash", tr.firmware.commit_hash)


def test_ingest_json_missing_file():
    print("\n[2] ingest_json — file not found → IngestionError")
    try:
        ingest_json(Path("nonexistent/path/TR-FAKE.json"))
        fail("Expected IngestionError")
    except IngestionError as e:
        ok(f"IngestionError raised: {str(e)[:50]}")


def test_ingest_json_malformed():
    print("\n[3] ingest_json — malformed JSON → IngestionError")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        f.write('{"test_run_id": "X", "firmware": "not-an-object"}')
        tmp_path = Path(f.name)
    try:
        ingest_json(tmp_path)
        fail("Expected IngestionError")
    except IngestionError as e:
        ok(f"IngestionError raised for malformed JSON")
    finally:
        tmp_path.unlink(missing_ok=True)


def test_ingest_csv_valid():
    print("\n[4] ingest_csv — valid CSV string")
    csv = (
        "FirmwareHash,TestSetId,BoardConfigId,Priority\n"
        "a1b2c3d4,TS-SHUTTLE-LEADSCREW,SH1,1\n"
        "a1b2c3d4,TS-SHUTTLE-DRIVE,SH1,2\n"
    )
    tr = ingest_csv(csv, requested_by="test-user")
    if len(tr.test_set_refs) == 2:
        ok("2 TestSetRefs from 2 CSV rows")
    else:
        fail("TestSetRef count", str(len(tr.test_set_refs)))
    if tr.firmware.commit_hash == "a1b2c3d4":
        ok("firmware hash from CSV")
    else:
        fail("firmware hash", tr.firmware.commit_hash)
    if tr.metadata.requested_by == "test-user":
        ok("requested_by from parameter")
    else:
        fail("requested_by", tr.metadata.requested_by)
    refs = tr.test_set_refs
    if refs[0].test_set_id == "TS-SHUTTLE-LEADSCREW" and refs[0].priority == 1:
        ok("First row priority=1")
    else:
        fail("First row", str(refs[0]))
    if refs[1].priority == 2:
        ok("Second row priority=2")
    else:
        fail("Second row priority", str(refs[1].priority))


def test_ingest_csv_missing_column():
    print("\n[5] ingest_csv — missing required column → IngestionError")
    csv = "FirmwareHash,TestSetId\na1b2c3d4,TS-A\n"  # missing BoardConfigId
    try:
        ingest_csv(csv)
        fail("Expected IngestionError")
    except IngestionError as e:
        ok(f"IngestionError: missing column detected")


def test_ingest_csv_inconsistent_hash():
    print("\n[6] ingest_csv — inconsistent firmware hash → IngestionError")
    csv = (
        "FirmwareHash,TestSetId,BoardConfigId\n"
        "hash_a,TS-A,SH1\n"
        "hash_b,TS-B,SH1\n"   # different hash
    )
    try:
        ingest_csv(csv)
        fail("Expected IngestionError")
    except IngestionError as e:
        ok("IngestionError: inconsistent firmware hash detected")


def test_ingest_csv_from_file():
    print("\n[7] ingest_csv — from CSV file path")
    csv_content = (
        "FirmwareHash,TestSetId,BoardConfigId\n"
        "a1b2c3d4,TS-SHUTTLE-LEADSCREW,SH1\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8"
    ) as f:
        f.write(csv_content)
        tmp_path = Path(f.name)
    try:
        tr = ingest_csv(tmp_path)
        if len(tr.test_set_refs) == 1:
            ok("CSV file ingested correctly")
        else:
            fail("TestSetRef count from file", str(len(tr.test_set_refs)))
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

def _make_test_run(**kwargs) -> TestRun:
    defaults = dict(
        test_run_id="TR-TEST",
        metadata=TestRunMetadata(requested_by="test"),
        firmware=FirmwareRef(repository="repo", commit_hash="abc123"),
        test_set_refs=[TestSetRef(test_set_id="TS-A", board_config_id="SH1")],
    )
    defaults.update(kwargs)
    return TestRun(**defaults)


def test_validate_empty_testsetrefs():
    print("\n[8] validate — empty TestSetRefs → ValidationError")
    mgr = make_manager()
    tr  = _make_test_run(test_set_refs=[])
    try:
        mgr._validate(tr)
        fail("Expected ValidationError")
    except ValidationError as e:
        ok(f"ValidationError: {str(e)[:60]}")


def test_validate_duplicate_testset():
    print("\n[9] validate — duplicate TestSetRef IDs → ValidationError")
    mgr = make_manager()
    tr  = _make_test_run(test_set_refs=[
        TestSetRef(test_set_id="TS-A", board_config_id="SH1"),
        TestSetRef(test_set_id="TS-A", board_config_id="SH1"),  # duplicate
    ])
    try:
        mgr._validate(tr)
        fail("Expected ValidationError")
    except ValidationError as e:
        ok(f"ValidationError: duplicate TestSetRef detected")


# ---------------------------------------------------------------------------
# Queue tests
# ---------------------------------------------------------------------------

def test_queue_priority_ordering():
    print("\n[10] Queue — priority ordering and FIFO tiebreaker")
    q  = TestRunQueue()
    tr = lambda i: _make_test_run(test_run_id=f"TR-{i}")

    q.enqueue(tr(1), priority=2)   # lower priority
    q.enqueue(tr(2), priority=1)   # higher priority
    q.enqueue(tr(3), priority=1)   # same priority as TR-2, inserted after

    e1 = q.dequeue()
    e2 = q.dequeue()
    e3 = q.dequeue()
    e4 = q.dequeue()  # should be None

    if e1 and e1.test_run.test_run_id == "TR-2":
        ok("Priority 1 runs before priority 2")
    else:
        fail("First dequeue", str(e1.test_run.test_run_id if e1 else None))

    if e2 and e2.test_run.test_run_id == "TR-3":
        ok("Same priority: FIFO order (TR-3 after TR-2)")
    else:
        fail("Second dequeue", str(e2.test_run.test_run_id if e2 else None))

    if e3 and e3.test_run.test_run_id == "TR-1":
        ok("Priority 2 runs last")
    else:
        fail("Third dequeue", str(e3.test_run.test_run_id if e3 else None))

    if e4 is None:
        ok("Empty queue returns None")
    else:
        fail("Expected None from empty queue")


# ---------------------------------------------------------------------------
# Reporter tests
# ---------------------------------------------------------------------------

def test_reporter_write_and_load():
    print("\n[11] Reporter — write and load report")
    with tempfile.TemporaryDirectory() as tmp:
        reporter = Reporter(reports_dir=Path(tmp))

        # Build a minimal valid TestRunResult
        from shared.models.testrig import TestRunResult
        from datetime import datetime, timezone
        result = TestRunResult(
            test_run_id="TR-REPORT-TEST",
            status=TestRunStatus.COMPLETED,
            firmware_commit_hash="abc123",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )

        path = reporter.write(result)

        if path.exists():
            ok(f"Report file written: {path.name}")
        else:
            fail("Report file not created")
            return

        if "TR-REPORT-TEST" in path.name:
            ok("TestRunId in filename")
        else:
            fail("Filename", path.name)

        if "COMPLETED" in path.name:
            ok("Status in filename")
        else:
            fail("Status in filename", path.name)

        # Load and validate
        loaded = reporter.load(path)
        if loaded.test_run_id == "TR-REPORT-TEST":
            ok("Round-trip load: test_run_id matches")
        else:
            fail("Round-trip load", loaded.test_run_id)

        listed = reporter.list_reports()
        if path in listed:
            ok("list_reports() returns the written file")
        else:
            fail("list_reports()")


# ---------------------------------------------------------------------------
# Full Manager flow tests (require daemon)
# ---------------------------------------------------------------------------

def test_manager_run_json():
    print("\n[12] Manager.run_json — full flow from JSON file")
    mgr    = make_manager()
    path   = DEFS_ROOT / "testruns" / "TR-2026-05-00001.json"
    result = mgr.run_json(path)

    if result.status == TestRunStatus.COMPLETED:
        ok("TestRun COMPLETED via Manager.run_json")
    else:
        fail("Status", result.status.value)

    if result.config_group_results and result.config_group_results[0].failure_type is None:
        ok("ConfigGroup no failure_type")
    else:
        fail("ConfigGroup failure",
             str(result.config_group_results[0].failure_type
                 if result.config_group_results else "no groups"))

    # Verify report was written
    reports = mgr._reporter.list_reports()
    if reports and result.test_run_id in reports[0].name:
        ok(f"Report written: {reports[0].name}")
    else:
        fail("Report not found", str(reports))


def test_manager_run_csv():
    print("\n[13] Manager.run_csv — full flow from CSV string")
    csv = (
        "FirmwareHash,TestSetId,BoardConfigId\n"
        "a1b2c3d4,TS-SHUTTLE-LEADSCREW,SH1\n"
    )
    mgr    = make_manager()
    result = mgr.run_csv(csv, requested_by="test-operator")

    if result.status == TestRunStatus.COMPLETED:
        ok("TestRun COMPLETED via Manager.run_csv")
    else:
        fail("Status", result.status.value)

    if result.firmware_commit_hash == "a1b2c3d4":
        ok("Firmware hash propagated from CSV")
    else:
        fail("Firmware hash", result.firmware_commit_hash)


def test_manager_run_queue():
    print("\n[14] Manager.run_queue — two runs in priority order")
    mgr = make_manager()
    path = DEFS_ROOT / "testruns" / "TR-2026-05-00001.json"

    # Enqueue same TestRun twice with different priorities
    tr1 = ingest_json(path)
    tr1 = tr1.model_copy(update={"test_run_id": "TR-QUEUE-LOW"})
    tr2 = ingest_json(path)
    tr2 = tr2.model_copy(update={"test_run_id": "TR-QUEUE-HIGH"})

    mgr._queue.enqueue(tr1, priority=2)  # lower priority
    mgr._queue.enqueue(tr2, priority=1)  # higher priority

    results = mgr.run_queue()

    if len(results) == 2:
        ok("Both queued runs completed")
    else:
        fail("Result count", str(len(results)))

    if results[0].test_run_id == "TR-QUEUE-HIGH":
        ok("High priority run executed first")
    else:
        fail("First result ID", results[0].test_run_id if results else "empty")

    if results[1].test_run_id == "TR-QUEUE-LOW":
        ok("Low priority run executed second")
    else:
        fail("Second result ID", results[1].test_run_id if len(results) > 1 else "missing")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  Phase 6 — Manager Tests")
    print("=" * 60)

    # Start daemon for integration tests
    server = serve(port=PORT, board_identity="shuttle", boot_delay_ms=100)
    time.sleep(0.2)

    try:
        # Ingestor tests (no daemon needed)
        test_ingest_json_valid()
        test_ingest_json_missing_file()
        test_ingest_json_malformed()
        test_ingest_csv_valid()
        test_ingest_csv_missing_column()
        test_ingest_csv_inconsistent_hash()
        test_ingest_csv_from_file()

        # Validation tests
        test_validate_empty_testsetrefs()
        test_validate_duplicate_testset()

        # Queue tests
        test_queue_priority_ordering()

        # Reporter tests
        test_reporter_write_and_load()

        # Full flow tests (need daemon)
        test_manager_run_json()
        test_manager_run_csv()
        test_manager_run_queue()

    finally:
        server.stop(grace=0)

    total = passed + failed
    print(f"\n{'='*60}")
    print(f"  Phase 6 Tests: {passed}/{total} passed")
    print(f"{'='*60}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
