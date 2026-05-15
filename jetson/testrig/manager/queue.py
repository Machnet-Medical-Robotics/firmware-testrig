"""
manager/queue.py
Priority queue for TestRuns awaiting dispatch to the Controller.

Each TestRun entry has:
  - A priority level (lower number = higher priority, runs first)
  - An arrival order (FIFO tiebreaker within same priority)
  - A status (PENDING → QUEUED → DISPATCHED → DONE)

The Manager calls enqueue() when a TestRun is ingested and validated.
It calls dequeue() to get the next TestRun when the Controller is free.

This is intentionally simple — a single in-process queue for one board pair.
Multi-board-pair scheduling (parallel dispatch) is a future extension.

THREAD SAFETY:
  All public methods acquire self._lock. Safe to call from multiple threads
  (e.g. a future HTTP API thread enqueuing while the Manager loop dequeues).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from shared.enums import TestRunStatus
from shared.models.testrig import TestRun

logger = logging.getLogger("manager.queue")


@dataclass
class QueueEntry:
    """
    One TestRun in the queue.

    priority:     Lower number runs first (1 = highest priority).
    sequence:     Insertion order — tiebreaker so equal-priority runs
                  execute in FIFO order.
    enqueued_at:  UTC timestamp of when this entry was added.
    status:       Current lifecycle status of this entry.
    test_run:     The TestRun to be executed.
    """
    test_run:    TestRun
    priority:    int
    sequence:    int
    enqueued_at: datetime
    status:      TestRunStatus = TestRunStatus.QUEUED

    def sort_key(self):
        """Lower priority number and earlier sequence = runs first."""
        return (self.priority, self.sequence)


class TestRunQueue:
    """
    Thread-safe priority queue for TestRuns.

    Usage:
        queue = TestRunQueue()
        queue.enqueue(test_run, priority=1)
        entry = queue.dequeue()   # returns highest-priority entry or None
        queue.mark_done(entry)
    """

    def __init__(self):
        self._entries:  List[QueueEntry] = []
        self._sequence: int              = 0
        self._lock:     threading.Lock   = threading.Lock()

    def enqueue(self, test_run: TestRun, priority: int = 1) -> QueueEntry:
        """
        Add a TestRun to the queue.

        Args:
            test_run: The validated TestRun to enqueue.
            priority: Execution priority. Lower = runs sooner. Default 1.

        Returns:
            The created QueueEntry.
        """
        with self._lock:
            self._sequence += 1
            entry = QueueEntry(
                test_run=test_run,
                priority=priority,
                sequence=self._sequence,
                enqueued_at=datetime.now(timezone.utc),
                status=TestRunStatus.QUEUED,
            )
            self._entries.append(entry)
            self._entries.sort(key=lambda e: e.sort_key())
            logger.info(
                "Enqueued | id=%s priority=%d queue_depth=%d",
                test_run.test_run_id, priority, len(self._entries),
            )
            return entry

    def dequeue(self) -> Optional[QueueEntry]:
        """
        Remove and return the highest-priority pending entry.
        Returns None if the queue is empty.
        """
        with self._lock:
            for entry in self._entries:
                if entry.status == TestRunStatus.QUEUED:
                    entry.status = TestRunStatus.RUNNING
                    logger.info(
                        "Dequeued | id=%s priority=%d",
                        entry.test_run.test_run_id, entry.priority,
                    )
                    return entry
            return None

    def mark_done(self, entry: QueueEntry) -> None:
        """Mark a previously dequeued entry as completed and remove it."""
        with self._lock:
            entry.status = TestRunStatus.COMPLETED
            self._entries = [e for e in self._entries if e is not entry]
            logger.info("Done | id=%s", entry.test_run.test_run_id)

    def mark_failed(self, entry: QueueEntry) -> None:
        """Mark a previously dequeued entry as failed and remove it."""
        with self._lock:
            entry.status = TestRunStatus.FAILED
            self._entries = [e for e in self._entries if e is not entry]
            logger.warning("Failed | id=%s", entry.test_run.test_run_id)

    def peek(self) -> Optional[QueueEntry]:
        """Return the next entry without removing it. Thread-safe."""
        with self._lock:
            for entry in self._entries:
                if entry.status == TestRunStatus.QUEUED:
                    return entry
            return None

    def depth(self) -> int:
        """Number of entries currently waiting (QUEUED status)."""
        with self._lock:
            return sum(1 for e in self._entries if e.status == TestRunStatus.QUEUED)

    def status_summary(self) -> dict:
        """Return a snapshot of the queue for monitoring/reporting."""
        with self._lock:
            return {
                "queued":  [e.test_run.test_run_id for e in self._entries
                            if e.status == TestRunStatus.QUEUED],
                "running": [e.test_run.test_run_id for e in self._entries
                            if e.status == TestRunStatus.RUNNING],
                "depth":   self.depth(),
            }
