"""
shared/models/worker_io.py
DTOs exchanged between Controller and Worker via subprocess stdin/stdout.

The Controller serialises WorkerInput to JSON → Worker stdin.
The Worker serialises WorkerOutput to JSON → Worker stdout.
The Controller reads stdout and deserialises WorkerOutput.

Keeping these as explicit Pydantic models (rather than passing raw
TestRun JSON) means the Worker only receives exactly what it needs,
and the interface contract is versioned and validated.
"""

from __future__ import annotations
from typing import List
from pydantic import BaseModel, Field
from shared.models.testrig import TestSet, TestCase, TestRunResult, ConfigGroupResult


class ResolvedTestSet(BaseModel):
    """
    A TestSet with its TestCases fully resolved (loaded from disk).
    The Controller resolves file references before spawning the Worker
    so the Worker never needs filesystem access.
    """
    test_set:   TestSet
    test_cases: List[TestCase] = Field(default_factory=list)


class WorkerInput(BaseModel):
    """
    Everything the Worker needs to execute one config group.

    board_pair_id    — identifies which board pair to talk to.
    board_config_id  — for logging/reporting only (config already applied).
    firmware_hash    — included in result for traceability.
    daemon_address   — gRPC address of the Hardware Daemon.
                       e.g. "localhost:50051"
    testsets         — fully resolved TestSets with their TestCases inline.
    """
    board_pair_id:   str
    board_config_id: str
    firmware_hash:   str
    daemon_address:  str                    = "localhost:50051"
    testsets:        List[ResolvedTestSet]  = Field(default_factory=list)


class WorkerOutput(BaseModel):
    """
    What the Worker returns to the Controller.
    The Controller uses this to build the final TestRunResult.
    """
    board_pair_id:   str
    board_config_id: str
    result:          ConfigGroupResult
