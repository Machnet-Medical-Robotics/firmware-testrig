"""
manager/ingestor.py
Converts external input (CSV rows or JSON files) into validated TestRun objects.

TWO INPUT FORMATS:

1. JSON TestRun file  (definitions/testruns/<id>.json)
   Full structured TestRun — used for predefined regression suites.
   The Manager reads the file and validates it as a TestRun model.

2. Ad-hoc CSV
   Lightweight format for quick one-off runs.
   Each row = one TestSetRef. The Manager builds a TestRun object from it.

   CSV columns:
     FirmwareHash, TestSetId, BoardConfigId, Priority (optional, default 1)

   Example CSV:
     FirmwareHash,TestSetId,BoardConfigId,Priority
     a1b2c3d4,TS-SHUTTLE-LEADSCREW,SH1,1
     a1b2c3d4,TS-SHUTTLE-DRIVE,SH1,2

   The Manager generates a TestRunId automatically from timestamp + hash.

VALIDATION:
   Both paths run through Pydantic model validation. Invalid input raises
   IngestionError with a clear message — never silently produces a bad TestRun.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import List, Optional

from shared.models.testrig import (
    ExecutionPolicy, FirmwareRef, TestRun,
    TestRunMetadata, TestSetRef,
)

logger = logging.getLogger("manager.ingestor")


class IngestionError(Exception):
    """Raised when input cannot be parsed into a valid TestRun."""
    pass


# ---------------------------------------------------------------------------
# CSV column names — case-insensitive matching
# ---------------------------------------------------------------------------
_CSV_FIRMWARE   = "firmwarehash"
_CSV_TESTSET    = "testsetid"
_CSV_CONFIG     = "boardconfigid"
_CSV_PRIORITY   = "priority"
_CSV_REQUESTED  = "requestedby"

_REQUIRED_CSV_COLS = {_CSV_FIRMWARE, _CSV_TESTSET, _CSV_CONFIG}


def ingest_json(path: Path) -> TestRun:
    """
    Load and validate a TestRun from a JSON file.

    Args:
        path: Path to a JSON file matching the TestRun schema.

    Returns:
        Validated TestRun object.

    Raises:
        IngestionError: if the file doesn't exist, can't be parsed,
                        or fails Pydantic validation.
    """
    if not path.exists():
        raise IngestionError(f"TestRun JSON file not found: {path}")

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as exc:
        raise IngestionError(f"Failed to read/parse JSON from {path}: {exc}") from exc

    try:
        test_run = TestRun(**data)
    except Exception as exc:
        raise IngestionError(f"TestRun JSON validation failed ({path}): {exc}") from exc

    logger.info(
        "Ingested JSON TestRun | id=%s testsets=%d",
        test_run.test_run_id,
        len(test_run.test_set_refs),
    )
    return test_run


def ingest_csv(
    csv_input:    str | Path,
    requested_by: str = "csv-import",
    run_id:       Optional[str] = None,
) -> TestRun:
    """
    Build a TestRun from a CSV string or file path.

    The CSV must have at minimum: FirmwareHash, TestSetId, BoardConfigId.
    Priority column is optional (defaults to 1).
    All rows must share the same FirmwareHash (one firmware per TestRun).

    Args:
        csv_input:    CSV content as a string, or Path to a .csv file.
        requested_by: Who requested this run (for metadata).
        run_id:       Override the generated TestRunId.

    Returns:
        Validated TestRun object.

    Raises:
        IngestionError: on missing columns, inconsistent firmware hash,
                        empty CSV, or validation failure.

    Example CSV content:
        FirmwareHash,TestSetId,BoardConfigId,Priority
        a1b2c3d4,TS-SHUTTLE-LEADSCREW,SH1,1
        a1b2c3d4,TS-SHUTTLE-DRIVE,SH1,2
    """
    # Resolve input to string
    if isinstance(csv_input, Path):
        if not csv_input.exists():
            raise IngestionError(f"CSV file not found: {csv_input}")
        try:
            csv_text = csv_input.read_text(encoding="utf-8")
        except Exception as exc:
            raise IngestionError(f"Failed to read CSV file {csv_input}: {exc}") from exc
    else:
        csv_text = csv_input

    # Parse CSV
    reader = csv.DictReader(StringIO(csv_text.strip()))

    # Validate columns (case-insensitive)
    if reader.fieldnames is None:
        raise IngestionError("CSV has no header row")

    normalised_cols = {c.strip().lower() for c in reader.fieldnames}
    missing = _REQUIRED_CSV_COLS - normalised_cols
    if missing:
        raise IngestionError(
            f"CSV missing required columns: {missing}. "
            f"Required: FirmwareHash, TestSetId, BoardConfigId"
        )

    # Read rows
    rows = []
    for i, raw_row in enumerate(reader, start=2):   # line 2 = first data row
        # Normalise keys
        row = {k.strip().lower(): v.strip() for k, v in raw_row.items() if k}
        rows.append((i, row))

    if not rows:
        raise IngestionError("CSV contains no data rows")

    # Validate consistent firmware hash
    hashes = {row[_CSV_FIRMWARE] for _, row in rows}
    if len(hashes) > 1:
        raise IngestionError(
            f"CSV contains multiple firmware hashes: {hashes}. "
            f"Each CSV represents one TestRun with one firmware version."
        )
    firmware_hash = hashes.pop()
    if not firmware_hash:
        raise IngestionError("CSV FirmwareHash column is empty")

    # Build TestSetRefs
    test_set_refs: List[TestSetRef] = []
    for line_num, row in rows:
        ts_id   = row.get(_CSV_TESTSET,  "").strip()
        cfg_id  = row.get(_CSV_CONFIG,   "").strip()
        pri_raw = row.get(_CSV_PRIORITY, "1").strip()

        if not ts_id:
            raise IngestionError(f"CSV line {line_num}: TestSetId is empty")
        if not cfg_id:
            raise IngestionError(f"CSV line {line_num}: BoardConfigId is empty")

        try:
            priority = int(pri_raw) if pri_raw else 1
        except ValueError:
            raise IngestionError(
                f"CSV line {line_num}: Priority '{pri_raw}' is not an integer"
            )

        test_set_refs.append(TestSetRef(
            test_set_id=ts_id,
            board_config_id=cfg_id,
            priority=priority,
        ))

    # Generate TestRunId if not provided
    if run_id is None:
        ts  = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        run_id = f"TR-CSV-{ts}-{firmware_hash[:8]}"

    # Use RequestedBy from CSV if present (first non-empty value wins),
    # otherwise fall back to the parameter
    csv_requested_by = next(
        (row.get(_CSV_REQUESTED, "").strip() for _, row in rows
         if row.get(_CSV_REQUESTED, "").strip()),
        "",
    )
    effective_requested_by = csv_requested_by or requested_by

    # Build TestRun
    try:
        test_run = TestRun(
            test_run_id=run_id,
            metadata=TestRunMetadata(
                requested_by=effective_requested_by,
                labels=["csv-import"],
                created_at=datetime.now(timezone.utc),
            ),
            firmware=FirmwareRef(
                repository="unknown",
                commit_hash=firmware_hash,
            ),
            execution_policy=ExecutionPolicy(),
            test_set_refs=test_set_refs,
        )
    except Exception as exc:
        raise IngestionError(f"TestRun model validation failed: {exc}") from exc

    logger.info(
        "Ingested CSV TestRun | id=%s firmware=%s testsets=%d",
        test_run.test_run_id,
        firmware_hash,
        len(test_set_refs),
    )
    return test_run
