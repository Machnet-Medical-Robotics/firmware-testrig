"""
controller/grouper.py
Groups TestSetRefs from a TestRun into ConfigGroups.

A ConfigGroup = all TestSets sharing the same (board_pair_id + board_config_id).
One ConfigGroup → 1 ESP32 config → 1 reboot → 1 Worker execution.

Groups are sorted by the minimum priority value of their TestSetRefs.
TestSetRefs within each group are sorted by priority ascending.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from shared.models.testrig import TestRun, TestSetRef
from shared.models.worker_io import ResolvedTestSet

logger = logging.getLogger("controller.grouper")


@dataclass
class ConfigGroup:
    board_pair_id:     str
    board_config_id:   str
    priority:          int
    testset_refs:      List[TestSetRef]       = field(default_factory=list)
    resolved_testsets: List[ResolvedTestSet]  = field(default_factory=list)

    @property
    def group_key(self) -> Tuple[str, str]:
        return (self.board_pair_id, self.board_config_id)


def group_testset_refs(
    test_run:      TestRun,
    board_pair_id: str = "pair_1",
) -> List[ConfigGroup]:
    """
    Group TestSetRefs by board_config_id into ConfigGroups, sorted by priority.
    All groups are assigned to board_pair_id (single-pair for now).
    """
    groups: Dict[str, ConfigGroup] = {}

    for ref in test_run.test_set_refs:
        key = ref.board_config_id
        if key not in groups:
            groups[key] = ConfigGroup(
                board_pair_id=board_pair_id,
                board_config_id=ref.board_config_id,
                priority=ref.priority,
            )
        groups[key].testset_refs.append(ref)
        groups[key].priority = min(groups[key].priority, ref.priority)

    for g in groups.values():
        g.testset_refs.sort(key=lambda r: r.priority)

    result = sorted(groups.values(), key=lambda g: g.priority)

    logger.info(
        "Grouped %d TestSetRefs into %d config group(s)",
        len(test_run.test_set_refs), len(result),
    )
    for g in result:
        logger.debug(
            "  Group (%s / %s) priority=%d → %s",
            g.board_pair_id, g.board_config_id, g.priority,
            [r.test_set_id for r in g.testset_refs],
        )
    return result
