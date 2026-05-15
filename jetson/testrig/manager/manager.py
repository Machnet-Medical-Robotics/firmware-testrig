"""
manager/manager.py
TestRig Manager — the top-level entry point for the testrig system.

RESPONSIBILITIES:
  1. Ingest input (JSON file or CSV string/file) → validated TestRun
  2. Validate the TestRun (firmware field present, TestSetRefs non-empty)
  3. Enqueue in priority queue
  4. Dispatch to Controller when ready
  5. Write result to report file
  6. Return TestRunResult to caller

RELATIONSHIP TO OTHER COMPONENTS:
  Manager creates the TestRun object and hands it to the Controller.
  The Controller owns everything from that point (grouping, board setup,
  Worker spawning). The Manager never talks to hardware directly.

  Manager → Controller → Worker subprocess → Hardware Daemon

USAGE — two modes:

  Mode 1: Single run (most common in dev/CI)
    manager = TestRigManager(...)
    result  = manager.run_json(Path("definitions/testruns/TR-001.json"))
    # or
    result  = manager.run_csv("a1b2c3d4,TS-SHUTTLE-LEADSCREW,SH1")

  Mode 2: Queue multiple runs (for future automation)
    manager.enqueue_json(path1, priority=1)
    manager.enqueue_json(path2, priority=2)
    manager.run_queue()   # processes all queued runs in priority order

WITHOUT THE MANAGER (testing Controller directly):
  You can still bypass the Manager entirely and call TestRunController.run()
  directly with a TestRun object — this is what test_end_to_end.py does.
  The Manager is purely an ingestion + queuing layer on top.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from shared.models.testrig import TestRun, TestRunResult
from controller.controller import TestRunController
from manager.ingestor import IngestionError, ingest_csv, ingest_json
from manager.queue import TestRunQueue
from manager.reporter import Reporter

logger = logging.getLogger("manager")


class ValidationError(Exception):
    """Raised when a TestRun fails semantic validation before queuing."""
    pass


class TestRigManager:
    """
    Top-level entry point for the TestRig system.

    Args:
        definitions_root: Path to the definitions/ directory.
        reports_dir:      Where to write JSON report files.
        daemon_address:   gRPC address of the Hardware Daemon.
        board_pair_id:    Board pair to run tests on.
        project_root:     Root of the testrig package (for subprocess PYTHONPATH).
    """

    def __init__(
        self,
        definitions_root: Path,
        reports_dir:      Path            = Path("reports"),
        daemon_address:   str             = "localhost:50051",
        board_pair_id:    str             = "pair_1",
        project_root:     Optional[Path]  = None,
    ):
        self._definitions_root = Path(definitions_root)
        self._daemon_address   = daemon_address
        self._board_pair_id    = board_pair_id
        self._project_root     = project_root or Path(__file__).parent.parent.resolve()
        self._queue            = TestRunQueue()
        self._reporter         = Reporter(reports_dir=Path(reports_dir))

    # -----------------------------------------------------------------------
    # Single-run convenience methods (most common usage)
    # -----------------------------------------------------------------------

    def run_json(self, path: Path, priority: int = 1) -> TestRunResult:
        """
        Ingest a JSON TestRun file, run it immediately, write report.

        This is the primary entry point for predefined test suites.
        Equivalent to: ingest → validate → enqueue → dispatch → report.

        Args:
            path:     Path to a TestRun JSON file.
            priority: Queue priority (unused for single runs, kept for API consistency).

        Returns:
            TestRunResult with full step-level detail.
        """
        test_run = ingest_json(path)
        self._validate(test_run)
        return self._dispatch_and_report(test_run)

    def run_csv(
        self,
        csv_input:    str | Path,
        requested_by: str = "csv",
        priority:     int = 1,
    ) -> TestRunResult:
        """
        Ingest a CSV string or file, run it immediately, write report.

        This is the primary entry point for ad-hoc test runs.

        Args:
            csv_input:    CSV content string or Path to a .csv file.
                          Required columns: FirmwareHash, TestSetId, BoardConfigId
                          Optional column:  Priority
            requested_by: Who requested this run (for metadata/report).
            priority:     Queue priority (unused for single runs).

        Returns:
            TestRunResult with full step-level detail.

        Example:
            result = manager.run_csv(
                "FirmwareHash,TestSetId,BoardConfigId\\n"
                "a1b2c3d4,TS-SHUTTLE-LEADSCREW,SH1"
            )
        """
        test_run = ingest_csv(csv_input, requested_by=requested_by)
        self._validate(test_run)
        return self._dispatch_and_report(test_run)

    # -----------------------------------------------------------------------
    # Queue-based methods (for future automation / multiple runs)
    # -----------------------------------------------------------------------

    def enqueue_json(self, path: Path, priority: int = 1) -> None:
        """
        Ingest and validate a JSON TestRun file, add to queue.
        Does not run it immediately — call run_queue() to process.
        """
        test_run = ingest_json(path)
        self._validate(test_run)
        self._queue.enqueue(test_run, priority=priority)

    def enqueue_csv(
        self,
        csv_input:    str | Path,
        requested_by: str = "csv",
        priority:     int = 1,
    ) -> None:
        """
        Ingest and validate a CSV TestRun, add to queue.
        Does not run it immediately — call run_queue() to process.
        """
        test_run = ingest_csv(csv_input, requested_by=requested_by)
        self._validate(test_run)
        self._queue.enqueue(test_run, priority=priority)

    def run_queue(self) -> list[TestRunResult]:
        """
        Process all queued TestRuns in priority order, sequentially.

        Each run is dispatched to the Controller, then its result is
        written to a report file. If a run is aborted (SYSTEM fault),
        processing continues with the next queued run.

        Returns:
            List of TestRunResult in the order they were executed.
        """
        results = []
        logger.info("run_queue | depth=%d", self._queue.depth())

        while True:
            entry = self._queue.dequeue()
            if entry is None:
                break

            try:
                result = self._dispatch_and_report(entry.test_run)
                self._queue.mark_done(entry)
                results.append(result)
            except Exception as exc:
                logger.error(
                    "Unexpected error running %s: %s",
                    entry.test_run.test_run_id, exc,
                )
                self._queue.mark_failed(entry)

        logger.info("run_queue complete | %d runs processed", len(results))
        return results

    def queue_status(self) -> dict:
        """Return current queue status for monitoring."""
        return self._queue.status_summary()

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _validate(self, test_run: TestRun) -> None:
        """
        Semantic validation beyond Pydantic schema checks.

        Checks:
          - firmware.commit_hash is not empty
          - at least one TestSetRef exists
          - no duplicate TestSetRef IDs within the same run

        Raises:
            ValidationError: with a clear message describing the problem.
        """
        if not test_run.firmware.commit_hash:
            raise ValidationError(
                f"TestRun {test_run.test_run_id}: firmware.commit_hash is empty"
            )

        if not test_run.test_set_refs:
            raise ValidationError(
                f"TestRun {test_run.test_run_id}: no TestSetRefs defined"
            )

        seen_ids = set()
        for ref in test_run.test_set_refs:
            if ref.test_set_id in seen_ids:
                raise ValidationError(
                    f"TestRun {test_run.test_run_id}: "
                    f"duplicate TestSetRef '{ref.test_set_id}'"
                )
            seen_ids.add(ref.test_set_id)

        logger.debug(
            "Validated TestRun %s (%d TestSetRefs)",
            test_run.test_run_id, len(test_run.test_set_refs),
        )

    def _dispatch_and_report(self, test_run: TestRun) -> TestRunResult:
        """
        Hand the TestRun to the Controller, wait for result, write report.

        This is the core dispatch step. The Controller owns everything
        from this point — board setup, Worker spawning, result aggregation.
        """
        logger.info(
            "Dispatching | id=%s firmware=%s testsets=%d",
            test_run.test_run_id,
            test_run.firmware.commit_hash,
            len(test_run.test_set_refs),
        )

        controller = TestRunController(
            definitions_root=self._definitions_root,
            daemon_address=self._daemon_address,
            board_pair_id=self._board_pair_id,
            project_root=self._project_root,
        )

        result      = controller.run(test_run)
        report_path = self._reporter.write(result)

        logger.info(
            "Complete | id=%s status=%s report=%s",
            result.test_run_id, result.status.value, report_path.name,
        )
        return result
