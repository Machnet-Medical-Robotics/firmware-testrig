"""
worker/logger.py
Structured logging helpers for the Worker.

All Worker log output is prefixed with context tags so that when multiple
Workers run in parallel (one per board pair), log lines are unambiguous.

Usage:
    log = WorkerLogger(board_pair_id="pair_1", board_config_id="SH1")
    log.info("Starting")
    log.step(1, "Executing console step")
    log.step_result(1, "PASSED", duration_ms=120)
"""

from __future__ import annotations
import logging
import sys


class WorkerLogger:
    """
    Thin wrapper around stdlib logging that prepends structured context
    to every message: [pair_id/config_id/testset_id/testcase_id]
    """

    def __init__(
        self,
        board_pair_id: str,
        board_config_id: str,
    ):
        self._pair   = board_pair_id
        self._config = board_config_id
        self._testset: str  = ""
        self._testcase: str = ""
        self._logger = logging.getLogger(f"worker.{board_pair_id}")

    # -----------------------------------------------------------------------
    # Context setters — call these as execution progresses
    # -----------------------------------------------------------------------

    def set_testset(self, testset_id: str) -> None:
        self._testset  = testset_id
        self._testcase = ""

    def set_testcase(self, testcase_id: str) -> None:
        self._testcase = testcase_id

    def clear_testcase(self) -> None:
        self._testcase = ""

    # -----------------------------------------------------------------------
    # Log methods
    # -----------------------------------------------------------------------

    def _prefix(self) -> str:
        parts = [self._pair, self._config]
        if self._testset:
            parts.append(self._testset)
        if self._testcase:
            parts.append(self._testcase)
        return "[" + "/".join(parts) + "]"

    def debug(self, msg: str, *args) -> None:
        self._logger.debug(f"{self._prefix()} {msg}", *args)

    def info(self, msg: str, *args) -> None:
        self._logger.info(f"{self._prefix()} {msg}", *args)

    def warning(self, msg: str, *args) -> None:
        self._logger.warning(f"{self._prefix()} {msg}", *args)

    def error(self, msg: str, *args) -> None:
        self._logger.error(f"{self._prefix()} {msg}", *args)

    def step(self, step_id: int, step_type: str, description: str = "") -> None:
        desc = f" — {description}" if description else ""
        self._logger.info(
            f"{self._prefix()} Step %d [%s]%s", step_id, step_type, desc
        )

    def step_result(
        self,
        step_id: int,
        status: str,
        duration_ms: int = 0,
        detail: str = "",
    ) -> None:
        detail_str = f" | {detail}" if detail else ""
        self._logger.info(
            f"{self._prefix()} Step %d → %s (%dms)%s",
            step_id, status, duration_ms, detail_str,
        )

    def testcase_result(self, testcase_id: str, status: str, duration_ms: int) -> None:
        self._logger.info(
            f"{self._prefix()} TestCase %s → %s (%dms)",
            testcase_id, status, duration_ms,
        )

    def testset_result(self, testset_id: str, status: str, duration_ms: int) -> None:
        self._logger.info(
            f"{self._prefix()} TestSet %s → %s (%dms)",
            testset_id, status, duration_ms,
        )
