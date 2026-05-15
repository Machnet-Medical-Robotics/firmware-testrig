"""
setup_dev.py
One-time development environment setup.

Run this ONCE after extracting the package. It:
  1. Creates the virtual environment (.venv)
  2. Installs all dependencies
  3. Installs the project in editable mode (fixes import errors permanently)
  4. Verifies everything works

After running this script, you NEVER need $env:PYTHONPATH="." again.
The venv's Python knows where shared/, worker/, controller/ etc. are.

Usage:
    # Windows (from testrig/ directory, NO venv needed):
    python setup_dev.py

    # Linux / Jetson (from testrig/ directory):
    python3 setup_dev.py
"""

import subprocess
import sys
import os
from pathlib import Path


def run(cmd: list, description: str) -> bool:
    print(f"  → {description}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ✗ FAILED: {result.stderr.strip()[-200:]}")
        return False
    print(f"  ✓ Done")
    return True


def main():
    root = Path(__file__).parent
    os.chdir(root)

    print()
    print("=" * 55)
    print("  TestRig Development Setup")
    print("=" * 55)
    print(f"  Project root: {root}")
    print(f"  Python:       {sys.executable}")
    print()

    # Determine venv paths
    is_windows = sys.platform == "win32"
    venv_dir   = root / ".venv"
    if is_windows:
        venv_python = venv_dir / "Scripts" / "python.exe"
        venv_pip    = venv_dir / "Scripts" / "pip.exe"
    else:
        venv_python = venv_dir / "bin" / "python3"
        venv_pip    = venv_dir / "bin" / "pip"

    # Step 1: Create venv if it doesn't exist
    if not venv_python.exists():
        print("[1/4] Creating virtual environment...")
        if not run([sys.executable, "-m", "venv", str(venv_dir)],
                   "Create .venv"):
            sys.exit(1)
    else:
        print("[1/4] Virtual environment already exists — skipping")

    # Step 2: Upgrade pip
    print("\n[2/4] Upgrading pip...")
    run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
        "Upgrade pip")

    # Step 3: Install dependencies
    print("\n[3/4] Installing dependencies...")
    if not run([str(venv_python), "-m", "pip", "install",
                "-r", "requirements.txt", "--quiet"],
               "Install from requirements.txt"):
        sys.exit(1)

    # Step 4: Editable install (the key step that fixes import errors)
    print("\n[4/4] Installing project in editable mode...")
    print("      (This makes 'from shared.enums import ...' work everywhere)")
    if not run([str(venv_python), "-m", "pip", "install", "-e", ".", "--quiet"],
               "pip install -e ."):
        sys.exit(1)

    # Verify
    print("\n[Verifying]")
    checks = [
        ([str(venv_python), "-c", "from shared.enums import FailureType; print('  ✓ shared.enums')"], "shared imports"),
        ([str(venv_python), "-c", "from worker.worker import run_worker; print('  ✓ worker')"], "worker imports"),
        ([str(venv_python), "-c", "from controller.controller import TestRunController; print('  ✓ controller')"], "controller imports"),
        ([str(venv_python), "-c", "from manager.manager import TestRigManager; print('  ✓ manager')"], "manager imports"),
        ([str(venv_python), "-c", "import grpc; print(f'  ✓ grpcio {grpc.__version__}')"], "grpcio"),
        ([str(venv_python), "-c", "import pydantic; print(f'  ✓ pydantic {pydantic.__version__}')"], "pydantic"),
        ([str(venv_python), "-c", "import psutil; print(f'  ✓ psutil {psutil.__version__}')"], "psutil"),
    ]
    all_ok = True
    for cmd, label in checks:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(result.stdout.strip())
        else:
            print(f"  ✗ {label}: {result.stderr.strip()[:80]}")
            all_ok = False

    print()
    if not all_ok:
        print("  ✗ Some checks failed. Review errors above.")
        sys.exit(1)

    print("=" * 55)
    print("  Setup complete!")
    print("=" * 55)
    print()

    # Print activation instructions
    if is_windows:
        activate = r".venv\Scripts\Activate.ps1"
        python   = r".venv\Scripts\python.exe"
    else:
        activate = "source .venv/bin/activate"
        python   = ".venv/bin/python3"

    print("  Next steps:")
    print()
    print(f"  1. Activate the virtual environment:")
    print(f"       {activate}")
    print()
    print(f"  2. Run tests (NO PYTHONPATH needed):")
    print(f"       python -m tests.test_end_to_end")
    print(f"       python -m tests.test_manager")
    print()
    print(f"  3. Or use the venv Python directly without activating:")
    print(f"       {python} -m tests.test_end_to_end")
    print()
    print(f"  The editable install means:")
    print(f"    python -m tests.test_end_to_end    ← works (module mode)")
    print(f"    python tests/test_end_to_end.py    ← works (script mode)")
    print(f"    python tests\\test_end_to_end.py   ← works (Windows)")
    print(f"    NO $env:PYTHONPATH needed          ← no longer required")
    print()


if __name__ == "__main__":
    main()
