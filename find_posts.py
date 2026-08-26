#!/usr/bin/env python3
"""Entry point kept for the many cron jobs, containers and schedulers that call it.

The code lives in the fedifetcher package.
"""
import sys

from fedifetcher.run import main

if __name__ == "__main__":
    sys.exit(main())
