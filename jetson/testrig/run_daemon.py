"""
run_daemon.py
Start the Mock Hardware Daemon.

Run this in a separate terminal before running tests or the controller.
Leave it running for the duration of your dev session.
Stop it with Ctrl+C.

Usage:
    # Windows (PowerShell, venv active):
    python run_daemon.py

    # Windows (PowerShell, venv NOT active):
    .venv\Scripts\python.exe run_daemon.py

    # Linux (venv active):
    python3 run_daemon.py

    # Linux (venv NOT active):
    .venv/bin/python3 run_daemon.py

Options:
    --port            gRPC port (default: 50051)
    --board-identity  Identity string the board announces on boot (default: shuttle)
    --boot-delay-ms   Simulated boot delay in ms (default: 500)
"""

import argparse
import logging
import signal
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("run_daemon")


def main():
    parser = argparse.ArgumentParser(description="TestRig Mock Hardware Daemon")
    parser.add_argument("--port",           type=int, default=50051)
    parser.add_argument("--board-identity", type=str, default="shuttle")
    parser.add_argument("--boot-delay-ms",  type=int, default=500)
    args = parser.parse_args()

    from hardware_daemon.mock_daemon import serve
    server = serve(
        port=args.port,
        board_identity=args.board_identity,
        boot_delay_ms=args.boot_delay_ms,
    )

    logger.info("=" * 55)
    logger.info("  Mock Hardware Daemon running")
    logger.info("  Port:           %d", args.port)
    logger.info("  Board identity: %s", args.board_identity)
    logger.info("  Boot delay:     %dms", args.boot_delay_ms)
    logger.info("  Press Ctrl+C to stop")
    logger.info("=" * 55)

    # Handle Ctrl+C gracefully on both Windows and Linux
    def shutdown(sig=None, frame=None):
        logger.info("Shutting down daemon...")
        server.stop(grace=2)
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
