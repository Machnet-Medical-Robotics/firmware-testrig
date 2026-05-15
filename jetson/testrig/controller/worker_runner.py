"""
controller/worker_runner.py
Spawns the Worker subprocess and handles retry logic.

Cross-platform notes:
  - Uses os.pathsep for PYTHONPATH separator (: on Linux, ; on Windows).
  - Uses sys.executable for the Python interpreter path (works in venv on
    both platforms without needing to know the venv layout).
  - Uses pathlib.Path throughout — no hardcoded separators.
  - subprocess.run with capture_output=True works identically on both.

Retry policy:
  Attempt 1: spawn → success → return
             failure → retry (max_retries times)
  All retries exhausted → SYSTEM fault
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import subprocess

from shared.enums import FailureType
from shared.models.worker_io import WorkerInput, WorkerOutput

logger = logging.getLogger("controller.worker_runner")

WORKER_TIMEOUT_S = 3600  # 1 hour hard cap per Worker


@dataclass
class WorkerRunResult:
    success:      bool
    output:       Optional[WorkerOutput] = None
    attempt:      int                    = 1
    failure_type: Optional[FailureType]  = None
    detail:       str                    = ""


def run_worker_with_retry(
    worker_input: WorkerInput,
    max_retries:  int           = 1,
    project_root: Optional[Path] = None,
) -> WorkerRunResult:
    """
    Spawn the Worker subprocess, retry up to max_retries times on failure.
    Returns WorkerRunResult with success=True and valid WorkerOutput, or
    success=False with failure_type=SYSTEM after all retries exhausted.
    """
    if project_root is None:
        project_root = Path(__file__).parent.parent.resolve()

    input_json  = worker_input.model_dump_json() + "\n"
    last_detail = ""

    for attempt in range(1, max_retries + 2):  # total attempts = max_retries + 1
        if attempt > 1:
            logger.warning(
                "Worker retry %d/%d | pair=%s config=%s",
                attempt - 1, max_retries,
                worker_input.board_pair_id,
                worker_input.board_config_id,
            )

        result = _spawn_once(input_json, project_root, attempt)

        if result.success:
            if attempt > 1:
                logger.warning(
                    "Worker succeeded on retry %d | pair=%s",
                    attempt - 1, worker_input.board_pair_id,
                )
                result.output.result.worker_retries = attempt - 1
            return result

        last_detail = result.detail
        logger.error(
            "Worker attempt %d failed | pair=%s | %s",
            attempt, worker_input.board_pair_id, last_detail,
        )

    logger.error(
        "Worker SYSTEM FAULT after %d attempt(s) | pair=%s config=%s",
        max_retries + 1,
        worker_input.board_pair_id,
        worker_input.board_config_id,
    )
    return WorkerRunResult(
        success=False,
        attempt=max_retries + 1,
        failure_type=FailureType.SYSTEM,
        detail=f"Worker failed after {max_retries + 1} attempt(s): {last_detail}",
    )


def _spawn_once(
    input_json:   str,
    project_root: Path,
    attempt:      int,
) -> WorkerRunResult:
    """Spawn one Worker subprocess. No retry logic here."""
    cmd = [sys.executable, "-m", "worker.worker"]

    logger.debug("Spawning Worker | attempt=%d", attempt)

    try:
        proc = subprocess.run(
            cmd,
            input=input_json,
            capture_output=True,
            text=True,
            cwd=str(project_root),
            env=_build_env(project_root),
            timeout=WORKER_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return WorkerRunResult(
            success=False, attempt=attempt,
            failure_type=FailureType.INFRA,
            detail=f"Worker timed out after {WORKER_TIMEOUT_S}s",
        )
    except Exception as exc:
        return WorkerRunResult(
            success=False, attempt=attempt,
            failure_type=FailureType.INFRA,
            detail=f"Failed to spawn Worker: {exc}",
        )

    # Re-emit Worker stderr as controller debug lines
    if proc.stderr:
        for line in proc.stderr.strip().splitlines():
            logger.debug("[worker] %s", line)

    if proc.returncode != 0:
        tail = (proc.stderr or "")[-300:].strip()
        return WorkerRunResult(
            success=False, attempt=attempt,
            failure_type=FailureType.INFRA,
            detail=f"Worker exit code {proc.returncode}. stderr: {tail}",
        )

    stdout = proc.stdout.strip()
    if not stdout:
        return WorkerRunResult(
            success=False, attempt=attempt,
            failure_type=FailureType.INFRA,
            detail="Worker produced no stdout",
        )

    try:
        output = WorkerOutput.model_validate_json(stdout)
    except Exception as exc:
        return WorkerRunResult(
            success=False, attempt=attempt,
            failure_type=FailureType.INFRA,
            detail=f"Invalid WorkerOutput JSON: {exc}",
        )

    return WorkerRunResult(success=True, output=output, attempt=attempt)


def _build_env(project_root: Path) -> dict:
    """
    Build subprocess environment with PYTHONPATH set to project_root.
    Uses os.pathsep so it works on both Windows (;) and Linux (:).
    """
    env      = os.environ.copy()
    root_str = str(project_root)
    existing = env.get("PYTHONPATH", "")

    if root_str not in existing.split(os.pathsep):
        env["PYTHONPATH"] = (
            f"{root_str}{os.pathsep}{existing}" if existing else root_str
        )
    return env
