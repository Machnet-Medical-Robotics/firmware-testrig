"""
controller/board_manager.py
Board setup phase: ESP32 config → reboot → readiness → identity check.
All hardware calls via DaemonClient (gRPC). No direct UART access.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from shared.enums import FailureType
from shared.models.testrig import BoardConfig
from shared.proto.client import DaemonClient, DeviceStatus

logger = logging.getLogger("controller.board_manager")


@dataclass
class BoardSetupResult:
    success:        bool
    board_identity: str                    = ""
    failure_type:   Optional[FailureType]  = None
    detail:         str                    = ""


class BoardManager:
    READY_TIMEOUT_MS   = 15_000
    CONFIG_TIMEOUT_MS  = 5_000
    POST_REBOOT_WAIT_S = 0.5

    def __init__(self, client: DaemonClient, board_pair_id: str):
        self._client        = client
        self._board_pair_id = board_pair_id

    def setup(self, board_config: BoardConfig) -> BoardSetupResult:
        cfg_id = board_config.board_config_id
        logger.info(
            "BoardSetup START | pair=%s config=%s byte=%s",
            self._board_pair_id, cfg_id, board_config.dip_switch_hex(),
        )

        # 1. Apply ESP32 config
        try:
            cfg_r = self._client.apply_board_config(
                config_byte=board_config.dip_switch_byte,
                timeout_ms=self.CONFIG_TIMEOUT_MS,
            )
        except Exception as exc:
            return BoardSetupResult(False, failure_type=FailureType.HARDWARE,
                                    detail=f"ESP32 config gRPC error: {exc}")

        if cfg_r.status != DeviceStatus.OK or cfg_r.detail != "CONFIG_APPLIED":
            return BoardSetupResult(False, failure_type=FailureType.HARDWARE,
                                    detail=f"ESP32 config failed: {cfg_r.detail}")

        logger.info("Config %s applied (0x%02X)", cfg_id, board_config.dip_switch_byte)

        # 2. Reboot Robot PCB
        try:
            reboot_r = self._client.reboot_device("robot_pcb", method="power_cycle")
        except Exception as exc:
            return BoardSetupResult(False, failure_type=FailureType.HARDWARE,
                                    detail=f"Reboot gRPC error: {exc}")

        if reboot_r.status != DeviceStatus.OK:
            return BoardSetupResult(False, failure_type=FailureType.HARDWARE,
                                    detail=f"Reboot failed: {reboot_r.detail}")

        time.sleep(self.POST_REBOOT_WAIT_S)

        # 3. Wait for readiness
        try:
            ready_r = self._client.check_device_ready(
                "monitoring_pcb", timeout_ms=self.READY_TIMEOUT_MS
            )
        except Exception as exc:
            return BoardSetupResult(False, failure_type=FailureType.HARDWARE,
                                    detail=f"Readiness check gRPC error: {exc}")

        if not ready_r.ready:
            return BoardSetupResult(False, failure_type=FailureType.HARDWARE,
                                    detail=f"Board not ready: {ready_r.detail}")

        announced = ready_r.board_identity
        logger.info("Board ready | identity='%s'", announced)

        # 4. Verify identity
        expected = board_config.expected_board_identity
        if expected and announced != expected:
            return BoardSetupResult(
                False,
                board_identity=announced,
                failure_type=FailureType.HARDWARE,
                detail=(
                    f"Board identity mismatch: expected '{expected}', "
                    f"got '{announced}'. "
                    f"Check DIP config {cfg_id} ({board_config.dip_switch_hex()})."
                ),
            )

        logger.info(
            "BoardSetup COMPLETE | pair=%s config=%s identity='%s'",
            self._board_pair_id, cfg_id, announced,
        )
        return BoardSetupResult(True, board_identity=announced)
