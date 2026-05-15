"""
controller/controller.py
Test Run Controller — main orchestrator.

Lifecycle per TestRun:
  GROUPING → for each ConfigGroup:
    CONFIGURING (ESP32 + reboot + ready + identity)
    → RESOLVING  (load TestSet/TestCase JSONs)
    → SPAWNING   (Worker subprocess)
    → AGGREGATING (collect result)
  → build TestRunResult

Abort conditions:
  HARDWARE_FAIL → skip group, continue others
  SYSTEM fault  → if abort_on_critical_infra_failure=True, stop all remaining groups
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from shared.enums import ControllerState, FailureType, TestRunStatus
from shared.models.testrig import ConfigGroupResult, TestRun, TestRunResult
from shared.models.worker_io import ResolvedTestSet, WorkerInput
from shared.proto.client import DaemonClient
from controller.loader import DefinitionLoader
from controller.grouper import ConfigGroup, group_testset_refs
from controller.board_manager import BoardManager
from controller.worker_runner import run_worker_with_retry

logger = logging.getLogger("controller")


class TestRunController:
    """
    Executes one TestRun end-to-end → TestRunResult.

    Usage:
        ctrl   = TestRunController(definitions_root=Path("definitions"))
        result = ctrl.run(test_run)
    """

    def __init__(
        self,
        definitions_root: Path,
        daemon_address:   str            = "localhost:50051",
        board_pair_id:    str            = "pair_1",
        project_root:     Optional[Path] = None,
    ):
        self._definitions_root = Path(definitions_root)
        self._daemon_address   = daemon_address
        self._board_pair_id    = board_pair_id
        self._project_root     = project_root or Path(__file__).parent.parent.resolve()
        self._state            = ControllerState.PENDING
        self._loader           = DefinitionLoader(self._definitions_root)

    def run(self, test_run: TestRun) -> TestRunResult:
        run_id  = test_run.test_run_id
        started = datetime.now(timezone.utc)
        logger.info("Controller START | run_id=%s", run_id)

        self._loader.load_all()

        self._state = ControllerState.GROUPING
        groups      = group_testset_refs(test_run, board_pair_id=self._board_pair_id)
        logger.info("Grouped into %d config group(s)", len(groups))

        # Connect daemon — reused across all groups
        client = DaemonClient(self._daemon_address)
        try:
            client.connect()
            ping = client.ping()
            if not ping.alive:
                raise RuntimeError("Daemon ping returned alive=False")
            logger.info("Daemon connected | version=%s", ping.daemon_version)
        except Exception as exc:
            return self._system_fault(
                test_run, started, f"Daemon connection failed: {exc}"
            )

        group_results:  List[ConfigGroupResult] = []
        system_aborted = False
        abort_detail   = ""

        try:
            for group in groups:
                if system_aborted:
                    group_results.append(ConfigGroupResult(
                        board_pair_id=group.board_pair_id,
                        board_config_id=group.board_config_id,
                        failure_type=FailureType.SYSTEM,
                        error_detail=f"Skipped due to prior system fault: {abort_detail}",
                    ))
                    continue

                result = self._execute_group(group, test_run, client)
                group_results.append(result)

                if (
                    result.failure_type == FailureType.SYSTEM
                    and test_run.execution_policy.abort_on_critical_infra_failure
                ):
                    system_aborted = True
                    abort_detail   = result.error_detail or "SYSTEM_FAULT"
                    logger.error(
                        "SYSTEM FAULT — aborting remaining groups | detail=%s",
                        abort_detail,
                    )
        finally:
            client.close()

        completed  = datetime.now(timezone.utc)
        run_status = TestRunStatus.ABORTED if system_aborted else TestRunStatus.COMPLETED

        logger.info(
            "Controller COMPLETE | run_id=%s status=%s groups=%d",
            run_id, run_status.value, len(group_results),
        )
        return TestRunResult(
            test_run_id=test_run.test_run_id,
            status=run_status,
            firmware_commit_hash=test_run.firmware.commit_hash,
            started_at=started,
            completed_at=completed,
            config_group_results=group_results,
            system_fault_detail=abort_detail if system_aborted else None,
        )

    # -----------------------------------------------------------------------
    # Group execution
    # -----------------------------------------------------------------------

    def _execute_group(
        self,
        group:    ConfigGroup,
        test_run: TestRun,
        client:   DaemonClient,
    ) -> ConfigGroupResult:
        cfg_id  = group.board_config_id
        pair_id = group.board_pair_id
        logger.info(
            "Group START | pair=%s config=%s sets=%s",
            pair_id, cfg_id, [r.test_set_id for r in group.testset_refs],
        )

        # Board setup
        self._state  = ControllerState.CONFIGURING
        board_config = self._loader.get_board_config(cfg_id)
        if board_config is None:
            return ConfigGroupResult(
                board_pair_id=pair_id,
                board_config_id=cfg_id,
                failure_type=FailureType.INFRA,
                error_detail=f"BoardConfig '{cfg_id}' not found in definitions",
            )

        setup = BoardManager(client=client, board_pair_id=pair_id).setup(board_config)
        if not setup.success:
            logger.error(
                "BoardSetup FAILED | pair=%s config=%s | %s",
                pair_id, cfg_id, setup.detail,
            )
            return ConfigGroupResult(
                board_pair_id=pair_id,
                board_config_id=cfg_id,
                failure_type=setup.failure_type,
                error_detail=setup.detail,
            )

        # Resolve definitions
        resolved, errors = self._resolve_group(group)
        if errors:
            detail = "; ".join(errors)
            logger.error("Resolution errors | %s", detail)
            return ConfigGroupResult(
                board_pair_id=pair_id,
                board_config_id=cfg_id,
                failure_type=FailureType.INFRA,
                error_detail=f"Definition resolution failed: {detail}",
            )

        # Spawn Worker
        self._state  = ControllerState.SPAWNING_WORKER
        worker_input = WorkerInput(
            board_pair_id=pair_id,
            board_config_id=cfg_id,
            firmware_hash=test_run.firmware.commit_hash,
            daemon_address=self._daemon_address,
            testsets=resolved,
        )

        self._state    = ControllerState.WAITING_WORKER
        run_result     = run_worker_with_retry(
            worker_input=worker_input,
            max_retries=test_run.execution_policy.retry_on_infra_failure,
            project_root=self._project_root,
        )

        self._state = ControllerState.AGGREGATING

        if not run_result.success:
            return ConfigGroupResult(
                board_pair_id=pair_id,
                board_config_id=cfg_id,
                failure_type=run_result.failure_type,
                error_detail=run_result.detail,
                worker_retries=run_result.attempt - 1,
            )

        logger.info(
            "Group COMPLETE | pair=%s config=%s retries=%d",
            pair_id, cfg_id, run_result.attempt - 1,
        )
        return run_result.output.result

    def _resolve_group(
        self, group: ConfigGroup
    ) -> tuple[List[ResolvedTestSet], List[str]]:
        resolved: List[ResolvedTestSet] = []
        errors:   List[str]             = []

        for ref in group.testset_refs:
            ts = self._loader.get_testset(ref.test_set_id)
            if ts is None:
                errors.append(f"TestSet '{ref.test_set_id}' not found")
                continue

            test_cases = []
            for tc_ref in ts.test_cases:
                tc = self._loader.get_testcase(tc_ref.test_case_id)
                if tc is None:
                    errors.append(
                        f"TestCase '{tc_ref.test_case_id}' (in {ref.test_set_id}) not found"
                    )
                else:
                    test_cases.append(tc)

            if not errors:
                resolved.append(ResolvedTestSet(test_set=ts, test_cases=test_cases))

        return resolved, errors

    def _system_fault(
        self, test_run: TestRun, started: datetime, detail: str
    ) -> TestRunResult:
        return TestRunResult(
            test_run_id=test_run.test_run_id,
            status=TestRunStatus.ABORTED,
            firmware_commit_hash=test_run.firmware.commit_hash,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
            system_fault_detail=detail,
        )
