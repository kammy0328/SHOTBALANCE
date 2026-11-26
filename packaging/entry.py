"""PyInstaller 진입점."""
import multiprocessing
import sys

from balance.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
