#!/usr/bin/env python3
"""Run the bundled package from a source checkout or installed plugin cache."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jevflow.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
