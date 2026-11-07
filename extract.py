#!/usr/bin/env python3
"""Compatibility entry point. `python extract.py` and `python main.py` are the same CLI."""

from main import main

if __name__ == "__main__":
    raise SystemExit(main())
