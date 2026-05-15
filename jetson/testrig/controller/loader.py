"""
controller/loader.py
Loads TestSet, TestCase and BoardConfig JSON files from definitions/.

Scans the full directory tree on first call, then serves from memory.
All file paths use pathlib.Path — works identically on Windows and Linux.

Extension: new assembly = new JSON files under definitions/testsets/<asm>/
and definitions/testcases/<asm>/. No code changes needed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from shared.models.testrig import BoardConfig, TestCase, TestSet

logger = logging.getLogger("controller.loader")


class DefinitionLoader:
    """
    Indexes all definition files from the definitions/ directory tree.

    Usage:
        loader = DefinitionLoader(Path("definitions"))
        loader.load_all()
        ts  = loader.get_testset("TS-SHUTTLE-LEADSCREW")
        tc  = loader.get_testcase("TC-SH-LEADSCREW-DRIVE")
        cfg = loader.get_board_config("SH1")
    """

    def __init__(self, definitions_root: Path):
        self._root          = Path(definitions_root)
        self._testsets:      Dict[str, TestSet]     = {}
        self._testcases:     Dict[str, TestCase]    = {}
        self._boardconfigs:  Dict[str, BoardConfig] = {}
        self._loaded = False

    def load_all(self) -> None:
        self._load_dir("testsets",  TestSet,     self._testsets,     "test_set_id")
        self._load_dir("testcases", TestCase,    self._testcases,    "test_case_id")
        self._load_dir("configs",   BoardConfig, self._boardconfigs, "board_config_id",
                       recursive=False)
        self._loaded = True
        logger.info(
            "Definitions loaded | %d testsets, %d testcases, %d configs",
            len(self._testsets), len(self._testcases), len(self._boardconfigs),
        )

    def get_testset(self, test_set_id: str) -> Optional[TestSet]:
        self._ensure_loaded()
        item = self._testsets.get(test_set_id)
        if item is None:
            logger.error("TestSet '%s' not found in definitions", test_set_id)
        return item

    def get_testcase(self, test_case_id: str) -> Optional[TestCase]:
        self._ensure_loaded()
        item = self._testcases.get(test_case_id)
        if item is None:
            logger.error("TestCase '%s' not found in definitions", test_case_id)
        return item

    def get_board_config(self, board_config_id: str) -> Optional[BoardConfig]:
        self._ensure_loaded()
        item = self._boardconfigs.get(board_config_id)
        if item is None:
            logger.error("BoardConfig '%s' not found in definitions", board_config_id)
        return item

    def summary(self) -> dict:
        self._ensure_loaded()
        return {
            "testsets":      sorted(self._testsets.keys()),
            "testcases":     sorted(self._testcases.keys()),
            "board_configs": sorted(self._boardconfigs.keys()),
        }

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load_all()

    def _load_dir(
        self,
        subdir:    str,
        model_cls,
        registry:  dict,
        id_field:  str,
        recursive: bool = True,
    ) -> None:
        path = self._root / subdir
        if not path.exists():
            logger.warning("Definitions subdir not found: %s", path)
            return
        # Use rglob for recursive (testsets/testcases), glob for flat (configs)
        pattern = "**/*.json" if recursive else "*.json"
        for f in path.glob(pattern):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                obj  = model_cls(**data)
                key  = getattr(obj, id_field)
                registry[key] = obj
                logger.debug("Loaded %s '%s' from %s", model_cls.__name__, key, f.name)
            except Exception as exc:
                logger.error("Failed to load %s from %s: %s", model_cls.__name__, f, exc)
