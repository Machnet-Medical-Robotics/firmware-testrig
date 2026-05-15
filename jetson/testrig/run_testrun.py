"""
run_testrun.py
Run a TestRun through the testrig system and print the result.

INPUT — three ways to specify what to run:
  1. JSON file:    python run_testrun.py definitions/testruns/TR-2026-05-00001.json
  2. CSV file:     python run_testrun.py --csv-file definitions/testruns/shuttle_regression.csv
  3. Inline CSV:   python run_testrun.py --csv "a1b2c3d4,TS-SHUTTLE-LEADSCREW,SH1"

PIPELINE — two modes:
  Default (no flag):  Direct Controller. No report file written. Fast for dev.
  --manager:          Full Manager pipeline. Writes JSON report to reports/. 
                      Use this for CI or any run you want a record of.

DAEMON:
  The Hardware Daemon must already be running in another terminal:
    python run_daemon.py

EXAMPLES:
  # Quick smoke test, no report:
  python run_testrun.py --csv-file definitions/testruns/shuttle_quick.csv

  # Full regression via CSV, write report:
  python run_testrun.py --csv-file definitions/testruns/shuttle_regression.csv --manager

  # Full regression via JSON, write report:
  python run_testrun.py definitions/testruns/TR-2026-05-00001.json --manager

  # Print raw JSON result:
  python run_testrun.py definitions/testruns/TR-2026-05-00001.json --json

  # Different daemon address:
  python run_testrun.py definitions/testruns/TR-2026-05-00001.json --daemon localhost:50052
"""

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("run_testrun")

DEFS_ROOT    = Path(__file__).parent / "definitions"
PROJECT_ROOT = Path(__file__).parent


def print_summary(result):
    from shared.enums import TestCaseStatus, TestSetStatus
    print()
    print("=" * 62)
    print(f"  TestRun:  {result.test_run_id}")
    print(f"  Status:   {result.status.value}")
    print(f"  Firmware: {result.firmware_commit_hash}")
    if result.started_at and result.completed_at:
        secs = (result.completed_at - result.started_at).total_seconds()
        print(f"  Duration: {secs:.1f}s")
    if result.system_fault_detail:
        print(f"  FAULT:    {result.system_fault_detail}")
    print("=" * 62)

    for group in result.config_group_results:
        print(f"\n  Board: {group.board_pair_id} / Config: {group.board_config_id}")
        if group.failure_type:
            print(f"    ✗ [{group.failure_type.value}] {group.error_detail}")
            continue
        if group.worker_retries:
            print(f"    ⚠  Worker retried {group.worker_retries} time(s)")

        for ts in group.test_set_results:
            ts_icon = "✓" if ts.status == TestSetStatus.PASSED else (
                      "~" if ts.status == TestSetStatus.PARTIAL else "✗")
            print(f"\n  {ts_icon} TestSet: {ts.test_set_id}  [{ts.status.value}]")

            for tc in ts.test_case_results:
                tc_icon = "✓" if tc.status == TestCaseStatus.PASSED else (
                          "—" if tc.status == TestCaseStatus.SKIPPED else "✗")
                line = f"      {tc_icon} {tc.test_case_id}  [{tc.status.value}]"
                if tc.failure_type:
                    line += f"  ({tc.failure_type.value})"
                print(line)

                for step in tc.step_results:
                    if step.status.value == "PASSED":
                        continue   # only show non-passing steps
                    s_icon = "—" if step.status.value == "SKIPPED" else "✗"
                    detail = f"  [{step.failure_type.value}]" if step.failure_type else ""
                    err    = f"  {step.error_detail[:70]}" if step.error_detail else ""
                    print(f"          {s_icon} step {step.step_id}: {step.status.value}{detail}{err}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Run a TestRun through the TestRig system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "testrun_file", nargs="?",
        help="Path to TestRun JSON file",
    )
    input_group.add_argument(
        "--csv-file", metavar="PATH",
        help="Path to a CSV file (FirmwareHash, TestSetId, BoardConfigId, [Priority], [RequestedBy])",
    )
    input_group.add_argument(
        "--csv", metavar="STRING",
        help="Inline CSV string (header + data, comma-separated)",
    )
    parser.add_argument(
        "--manager", action="store_true",
        help="Use full Manager pipeline — writes JSON report to reports/",
    )
    parser.add_argument(
        "--daemon", default="localhost:50051",
        help="Hardware Daemon gRPC address (default: localhost:50051)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Print full JSON result instead of summary",
    )
    parser.add_argument(
        "--requested-by", default="cli",
        help="Name/team to record in TestRun metadata (default: cli)",
    )
    args = parser.parse_args()

    if not args.testrun_file and not args.csv_file and not args.csv:
        parser.print_help()
        sys.exit(1)

    # ------------------------------------------------------------------
    # Resolve input → TestRun (or hand to Manager directly)
    # ------------------------------------------------------------------
    if args.manager:
        from manager.manager import TestRigManager
        mgr = TestRigManager(
            definitions_root=DEFS_ROOT,
            daemon_address=args.daemon,
            project_root=PROJECT_ROOT,
        )
        if args.testrun_file:
            result = mgr.run_json(Path(args.testrun_file))
        elif args.csv_file:
            result = mgr.run_csv(Path(args.csv_file), requested_by=args.requested_by)
        else:
            result = mgr.run_csv(args.csv, requested_by=args.requested_by)

        # Show where report was written
        reports = mgr._reporter.list_reports()
        if reports:
            logger.info("Report written → %s", reports[0])

    else:
        # Direct Controller path — no report written
        from shared.models.testrig import TestRun
        from controller.controller import TestRunController
        from manager.ingestor import ingest_csv, ingest_json

        if args.testrun_file:
            path = Path(args.testrun_file)
            if not path.exists():
                logger.error("File not found: %s", path)
                sys.exit(1)
            test_run = ingest_json(path)
        elif args.csv_file:
            test_run = ingest_csv(
                Path(args.csv_file), requested_by=args.requested_by
            )
        else:
            test_run = ingest_csv(args.csv, requested_by=args.requested_by)

        ctrl   = TestRunController(
            definitions_root=DEFS_ROOT,
            daemon_address=args.daemon,
            project_root=PROJECT_ROOT,
        )
        result = ctrl.run(test_run)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print_summary(result)

    sys.exit(0 if result.system_fault_detail is None else 1)


if __name__ == "__main__":
    main()
