"""Main loop: evdev gamepad source -> DualSenseState -> UHID input reports."""
from __future__ import annotations

import errno
import glob
import os
import select
import signal
import sys
import time
from dataclasses import dataclass, field

from . import dualsense, evdev, uhid
from .mapping import Mapping, PROFILES


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr, flush=True)


def find_virtual_nodes(uniq: str) -> dict:
    """Locate the sysfs HID device the kernel created for us and its hidraw/evdev."""
    for dev in glob.glob("/sys/bus/hid/devices/*054C:0CE6*"):
        try:
            ue = open(f"{dev}/uevent").read()
        except OSError:
            continue
        if f"HID_UNIQ={uniq}" not in ue:
            continue
        drv = os.path.basename(os.readlink(f"{dev}/driver")) if os.path.islink(f"{dev}/driver") else None
        hidraws = [os.path.basename(p) for p in glob.glob(f"{dev}/hidraw/hidraw*")]
        events = sorted(os.path.basename(p) for p in glob.glob(f"{dev}/input/input*/event*"))
        return {"sysfs": dev, "driver": drv, "hidraw": hidraws, "events": events}
    return {}


class VirtualDualSense:
    """Owns the UHID device and services kernel requests."""

    def __init__(self):
        self.dev = dualsense.make_uhid_device()
        self.state = dualsense.DualSenseState()
        self.seq = 0
        self.dev.create()
        log(f"UHID_CREATE2 sent for {dualsense.NAME} ({dualsense.VID:04x}:{dualsense.PID:04x}), uniq {self.dev.uniq}")

    def fileno(self) -> int:
        return self.dev.fileno()

    def service(self) -> None:
        ev = self.dev.read_event()
        if ev.type in (uhid.UHID_START, uhid.UHID_STOP, uhid.UHID_OPEN, uhid.UHID_CLOSE):
            log(f"uhid: {ev.name}")
        dualsense.handle_uhid_event(self.dev, ev, log)

    def emit(self) -> None:
        self.dev.send_input(self.state.pack(self.seq))
        self.seq = (self.seq + 1) & 0xFF

    def wait_bound(self, timeout: float = 5.0) -> dict:
        """Service events until hid-playstation has bound us (hidraw exists)."""
        deadline = time.monotonic() + timeout
        info = {}
        while time.monotonic() < deadline:
            r, _, _ = select.select([self.dev], [], [], 0.1)
            if r:
                self.service()
            info = find_virtual_nodes(self.dev.uniq)
            if info.get("driver") == "playstation" and info.get("hidraw") and info.get("events"):
                return info
        return info

    def close(self) -> None:
        self.dev.close()


@dataclass
class Source:
    """An evdev gamepad feeding the virtual pad; reconnects if it vanishes."""
    name_substr: str
    mapping: Mapping
    grab: bool = False
    exclude: set[str] = field(default_factory=set)  # paths never to use (e.g. Steam's echo of our own pad)
    dev: evdev.EvdevDevice | None = None
    last_scan: float = 0.0

    def try_open(self) -> bool:
        now = time.monotonic()
        if now - self.last_scan < 1.0:
            return False
        self.last_scan = now
        path = evdev.find_device(self.name_substr, exclude=self.exclude)
        if not path:
            return False
        try:
            self.dev = evdev.EvdevDevice(path, grab=self.grab)
        except OSError as e:
            log(f"source: cannot open {path}: {e}")
            return False
        log(f"source: using {path} \"{self.dev.name}\" ({self.dev.vendor:04x}:{self.dev.product:04x})"
            f"{' [grabbed]' if self.grab else ''}")
        return True

    def drop(self) -> None:
        if self.dev:
            log(f"source: lost {self.dev.path}")
            self.dev.close()
            self.dev = None

    def pump(self, state: dualsense.DualSenseState) -> bool:
        """Apply pending events to state. Returns True if a SYN_REPORT arrived."""
        assert self.dev
        try:
            events = self.dev.read_events()
        except OSError as e:
            if e.errno in (errno.ENODEV, errno.EIO):
                self.drop()
                state.neutral()
                return True
            raise
        synced = False
        for ev in events:
            if ev.type == evdev.EV_SYN and ev.code == evdev.SYN_REPORT:
                synced = True
            elif ev.type == evdev.EV_KEY:
                self.mapping.apply_key(state, ev.code, ev.value != 0)
            elif ev.type == evdev.EV_ABS:
                info = self.dev.absinfo.get(ev.code)
                if info:
                    self.mapping.apply_abs(state, ev.code, info.normalize(ev.value))
        return synced


def _matching_paths(name_substr: str) -> set[str]:
    return {p for p, n in evdev.list_devices() if name_substr.lower() in n.lower()}


def run_bridge(source_name: str, profile: str, grab: bool, rate_hz: float) -> int:
    mapping = PROFILES[profile]
    before = _matching_paths(source_name)
    vpad = VirtualDualSense()
    info = vpad.wait_bound()
    if info.get("driver") != "playstation":
        log(f"ERROR: hid-playstation did not bind the virtual pad: {info or 'device not found in sysfs'}")
        vpad.close()
        return 1
    log(f"virtual DualSense ready: {info['sysfs']} hidraw={info['hidraw']} evdev={info['events']}")

    # If Steam Input's PlayStation support is on, Steam wraps OUR virtual pad as
    # yet another "X-Box 360 pad" within a second or two. Reading that one back
    # would be a feedback loop, so exclude anything that appears right now.
    settle_until = time.monotonic() + 2.0
    while time.monotonic() < settle_until:
        r, _, _ = select.select([vpad], [], [], 0.1)
        if r:
            vpad.service()
    echo = _matching_paths(source_name) - before
    if echo:
        log(f"WARNING: Steam wrapped the virtual DualSense as {sorted(echo)}; ignoring those. "
            f"Turn OFF Steam > Settings > Controller > 'Enable Steam Input for PlayStation controllers'.")

    src = Source(source_name, mapping, grab=grab, exclude=echo)
    stop = False

    def _sig(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    period = 1.0 / rate_hz
    next_tick = time.monotonic()
    log(f"bridging \"{source_name}\" -> DualSense, profile={profile}, {rate_hz:.0f} Hz stream")
    while not stop:
        if src.dev is None and src.try_open():
            vpad.state.neutral()
        fds = [vpad] + ([src.dev] if src.dev else [])
        timeout = max(0.0, next_tick - time.monotonic())
        try:
            r, _, _ = select.select(fds, [], [], timeout)
        except InterruptedError:
            continue
        dirty = False
        for fd in r:
            if fd is vpad:
                vpad.service()
            else:
                dirty |= src.pump(vpad.state)
        now = time.monotonic()
        if dirty or now >= next_tick:
            vpad.emit()
            next_tick = now + period
    log("stopping")
    vpad.close()
    return 0


def run_selftest(hold: float = 0.5, loops: int = 1) -> int:
    """Create the virtual pad and play scripted inputs so it can be watched
    with evtest/jstest (or `sc-dualsense watch`)."""
    vpad = VirtualDualSense()
    info = vpad.wait_bound()
    if info.get("driver") != "playstation":
        log(f"ERROR: hid-playstation did not bind: {info or 'device not found in sysfs'}")
        vpad.close()
        return 1
    log(f"virtual DualSense ready: {info['sysfs']} hidraw={info['hidraw']} evdev={info['events']}")
    log("watch it with:  evtest /dev/input/%s   or   sc-dualsense watch" % (info["events"][0] if info["events"] else "eventN"))

    script = []
    for b in ("cross", "circle", "square", "triangle", "l1", "r1", "l3", "r3", "create", "options", "ps"):
        script.append((f"button {b}", lambda s, b=b: s.set_button(b, True)))
    for name, (dx, dy) in {"dpad up": (0, -1), "dpad right": (1, 0), "dpad down": (0, 1), "dpad left": (-1, 0)}.items():
        script.append((name, lambda s, dx=dx, dy=dy: setattr(s, "dpad_x", dx) or setattr(s, "dpad_y", dy)))
    script.append(("left stick right", lambda s: setattr(s, "lx", 255)))
    script.append(("left stick up", lambda s: setattr(s, "ly", 0)))
    script.append(("right stick right", lambda s: setattr(s, "rx", 255)))
    script.append(("right stick down", lambda s: setattr(s, "ry", 255)))
    script.append(("L2 full", lambda s: setattr(s, "l2", 255)))
    script.append(("R2 full", lambda s: setattr(s, "r2", 255)))

    stop = False

    def _sig(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    def stream(duration: float):
        end = time.monotonic() + duration
        while time.monotonic() < end and not stop:
            r, _, _ = select.select([vpad], [], [], 0.004)
            if r:
                vpad.service()
            vpad.emit()

    stream(0.5)
    for _ in range(loops):
        for label, act in script:
            if stop:
                break
            vpad.state.neutral()
            act(vpad.state)
            log(f"selftest: {label}")
            stream(hold)
            vpad.state.neutral()
            stream(hold / 2)
    log("selftest: idle streaming; Ctrl-C to remove the virtual pad")
    while not stop:
        stream(1.0)
    vpad.close()
    return 0


def run_watch(name_substr: str = "DualSense Wireless Controller", duration: float = 0.0) -> int:
    """Print evdev events from a device (default: the DualSense) so a human can
    confirm what the kernel sees. Also used to verify the SC source."""
    path = name_substr if name_substr.startswith("/dev/") else evdev.find_device(name_substr)
    if not path:
        log(f"no gamepad evdev matching \"{name_substr}\"")
        return 1
    dev = evdev.EvdevDevice(path)
    log(f"watching {path} \"{dev.name}\"  keys={len(dev.keys)} axes={sorted(dev.axes)}")
    end = time.monotonic() + duration if duration else None
    try:
        while end is None or time.monotonic() < end:
            r, _, _ = select.select([dev], [], [], 0.25)
            if not r:
                continue
            for ev in dev.read_events():
                if ev.type == evdev.EV_SYN:
                    continue
                kind = {evdev.EV_KEY: "KEY", evdev.EV_ABS: "ABS"}.get(ev.type, f"T{ev.type}")
                print(f"{kind} {ev.code:#06x} = {ev.value}", flush=True)
    except (KeyboardInterrupt, OSError) as e:
        if isinstance(e, OSError):
            log(f"device gone: {e}")
    dev.close()
    return 0
