#!/usr/bin/env python3
"""Compatibility launcher for the Wingman web application."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from wingman.web import main


if __name__ == "__main__":
    main()
