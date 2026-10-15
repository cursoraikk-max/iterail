#!/usr/bin/env python3
"""Portable local gate; runs all55 declared portable contracts."""
import runpy
from pathlib import Path
import sys
root=Path(__file__).resolve().parents[1]
if '--repo' not in sys.argv:sys.argv += ['--repo',str(root)]
runpy.run_path(str(root/'tests/portable/run.py'),run_name='__main__')
