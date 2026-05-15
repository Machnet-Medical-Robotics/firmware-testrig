"""
manager/reporter.py
Writes TestRunResult to a JSON report file in reports/.

The report is the final output of a TestRun — it contains every
TestSet, TestCase, Step result, all timestamps, failure types and
error details. It is a complete record of what happened.

Report filename format:
  reports/<test_run_id>_<YYYYMMDD-HHMMSS>_<status>.json

Example:
  reports/TR-2026-05-00001_20260507-143022_COMPLETED.json

The report is written atomically (to a temp file then renamed) so
a partial write is never left on disk if the process is interrupted.
"""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from shared.models.testrig import TestRunResult

logger = logging.getLogger("manager.reporter")


class Reporter:
    """
    Writes TestRunResult objects to JSON files in a reports directory.

    Usage:
        reporter = Reporter(reports_dir=Path("reports"))
        path = reporter.write(result)
        print(f"Report saved to: {path}")
    """

    def __init__(self, reports_dir: Path):
        self._reports_dir = Path(reports_dir)
        self._reports_dir.mkdir(parents=True, exist_ok=True)

    def write(self, result: TestRunResult) -> Path:
        """
        Serialise result to JSON and write to reports directory.

        The filename encodes the run ID, completion timestamp and status
        so reports are human-browsable without opening the file.

        Writes atomically: JSON → temp file → rename to final path.
        If the rename fails (e.g. cross-device), falls back to direct write.

        Returns:
            Path to the written report file.

        Raises:
            RuntimeError: if the file cannot be written.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        filename  = f"{result.test_run_id}_{timestamp}_{result.status.value}.json"
        out_path  = self._reports_dir / filename

        report_json = result.model_dump_json(indent=2)

        # Atomic write: write to temp file in same dir, then rename
        try:
            tmp = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._reports_dir,
                delete=False,
                suffix=".tmp",
            )
            tmp.write(report_json)
            tmp.flush()
            tmp.close()
            Path(tmp.name).replace(out_path)
        except Exception as exc:
            # Fallback: direct write (non-atomic but safe for dev)
            logger.warning("Atomic write failed (%s), falling back to direct write", exc)
            try:
                out_path.write_text(report_json, encoding="utf-8")
            except Exception as exc2:
                raise RuntimeError(f"Failed to write report to {out_path}: {exc2}") from exc2

        logger.info(
            "Report written | id=%s status=%s path=%s",
            result.test_run_id, result.status.value, out_path,
        )
        return out_path

    def load(self, path: Path) -> TestRunResult:
        """
        Load and validate a report JSON file back into a TestRunResult.
        Useful for post-processing, CI checks, or re-displaying a past result.
        """
        try:
            return TestRunResult.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to load report from {path}: {exc}") from exc

    def list_reports(self) -> list[Path]:
        """Return all .json report files sorted by modification time (newest first)."""
        return sorted(
            self._reports_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
