#!/usr/bin/env python3
"""Backward-compatible Web launcher.

The required deployment entrypoint is now main.run. This file remains only so
older documentation or scripts that call run_web.py continue to work.
"""

from main import run


if __name__ == "__main__":
    run()
