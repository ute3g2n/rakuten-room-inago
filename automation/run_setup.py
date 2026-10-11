"""Review setup and migration state."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from automation.cli import main_for_flow


if __name__ == "__main__":
    raise SystemExit(main_for_flow("setup"))
