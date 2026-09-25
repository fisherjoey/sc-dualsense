import argparse
import sys

from . import bridge
from .mapping import PROFILES


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sc-dualsense",
                                description="Bridge a Steam Controller (via an evdev gamepad) to a virtual DualSense over /dev/uhid.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the bridge daemon")
    r.add_argument("--source", default="X-Box 360 pad",
                   help="substring of the source evdev gamepad name (default: Steam Input's virtual X-Box 360 pad)")
    r.add_argument("--profile", default="xpad", choices=sorted(PROFILES))
    r.add_argument("--grab", action="store_true", help="EVIOCGRAB the source so nothing else reads it")
    r.add_argument("--rate", type=float, default=250.0, help="steady-state report rate in Hz (default 250)")

    s = sub.add_parser("selftest", help="create the virtual DualSense and play scripted inputs")
    s.add_argument("--hold", type=float, default=0.5)
    s.add_argument("--loops", type=int, default=1)

    w = sub.add_parser("watch", help="print evdev events from a gamepad (default: the DualSense)")
    w.add_argument("--device", default="DualSense Wireless Controller")
    w.add_argument("--seconds", type=float, default=0.0)

    sub.add_parser("list", help="list evdev devices")

    a = p.parse_args(argv)
    if a.cmd == "run":
        return bridge.run_bridge(a.source, a.profile, a.grab, a.rate)
    if a.cmd == "selftest":
        return bridge.run_selftest(a.hold, a.loops)
    if a.cmd == "watch":
        return bridge.run_watch(a.device, a.seconds)
    if a.cmd == "list":
        from . import evdev
        for path, name in evdev.list_devices():
            print(f"{path}\t{name}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
