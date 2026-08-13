"""Dev smoke test: inject TaskbarGlassTAP.dll into explorer and exercise the pipe.

Leaves the taskbar clear (alpha 0) applied so the effect can be inspected.
"""
import os
import sys

# make tg importable when run from anywhere
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tg.tap import TAPService, find_dll


def main():
    service = TAPService(find_dll())
    ok = service.start()
    print("started:", ok)
    if not ok:
        return 1
    print("ping:", service.ping())
    service.apply(0, "0a0a0c")
    print("applied clear taskbar (alpha 0)")
    import time
    time.sleep(6)
    print("ping after 6s:", service.ping())
    print("TAP alive, taskbar left clear")
    return 0


if __name__ == "__main__":
    sys.exit(main())