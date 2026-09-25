"""Minimal stdlib evdev reader: enumerate /dev/input/event*, query
capabilities/absinfo via ioctl, grab, and read input_event records."""
from __future__ import annotations

import fcntl
import os
import struct
from dataclasses import dataclass

EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT = 0
EV_MAX = 0x1F
KEY_MAX = 0x2FF
ABS_MAX = 0x3F

ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ = 0, 1, 2, 3, 4, 5
ABS_HAT0X, ABS_HAT0Y, ABS_HAT1X, ABS_HAT1Y, ABS_HAT2X, ABS_HAT2Y = 16, 17, 18, 19, 20, 21
BTN_A, BTN_B, BTN_C, BTN_X, BTN_Y, BTN_Z = 0x130, 0x131, 0x132, 0x133, 0x134, 0x135
BTN_TL, BTN_TR, BTN_TL2, BTN_TR2 = 0x136, 0x137, 0x138, 0x139
BTN_SELECT, BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR = 0x13A, 0x13B, 0x13C, 0x13D, 0x13E
BTN_DPAD_UP, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT = 0x220, 0x221, 0x222, 0x223

EVENT_FMT = "<qqHHi"  # struct input_event on x86_64: timeval(16) type code value
EVENT_SIZE = struct.calcsize(EVENT_FMT)

_IOC_NONE, _IOC_WRITE, _IOC_READ = 0, 1, 2


def _ioc(direction: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord("E") << 8) | nr


def EVIOCGNAME(n: int) -> int: return _ioc(_IOC_READ, 0x06, n)
def EVIOCGBIT(ev: int, n: int) -> int: return _ioc(_IOC_READ, 0x20 + ev, n)
def EVIOCGABS(a: int) -> int: return _ioc(_IOC_READ, 0x40 + a, 24)
EVIOCGRAB = _ioc(_IOC_WRITE, 0x90, 4)
EVIOCGID = _ioc(_IOC_READ, 0x02, 8)


@dataclass
class AbsInfo:
    value: int
    min: int
    max: int
    fuzz: int
    flat: int
    resolution: int

    def normalize(self, v: int) -> float:
        """Map v to 0.0..1.0 across [min, max]."""
        span = self.max - self.min
        if span <= 0:
            return 0.5
        return min(1.0, max(0.0, (v - self.min) / span))


@dataclass
class InputEvent:
    type: int
    code: int
    value: int


def _bits(buf: bytes) -> set[int]:
    out = set()
    for i, byte in enumerate(buf):
        b = 0
        while byte:
            if byte & 1:
                out.add(i * 8 + b)
            byte >>= 1
            b += 1
    return out


class EvdevDevice:
    def __init__(self, path: str, grab: bool = False):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        self.name = self._name()
        self.bustype, self.vendor, self.product, self.version = self._id()
        self.keys = self._capbits(EV_KEY, KEY_MAX)
        self.axes = self._capbits(EV_ABS, ABS_MAX)
        self.absinfo = {a: self._absinfo(a) for a in sorted(self.axes)}
        if grab:
            fcntl.ioctl(self.fd, EVIOCGRAB, struct.pack("i", 1))

    def fileno(self) -> int:
        return self.fd

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass

    def _name(self) -> str:
        buf = bytearray(256)
        fcntl.ioctl(self.fd, EVIOCGNAME(len(buf)), buf)
        return bytes(buf).split(b"\0", 1)[0].decode(errors="replace")

    def _id(self):
        buf = bytearray(8)
        fcntl.ioctl(self.fd, EVIOCGID, buf)
        return struct.unpack("<HHHH", buf)

    def _capbits(self, ev: int, maxcode: int) -> set[int]:
        buf = bytearray(maxcode // 8 + 1)
        try:
            fcntl.ioctl(self.fd, EVIOCGBIT(ev, len(buf)), buf)
        except OSError:
            return set()
        return _bits(bytes(buf))

    def _absinfo(self, axis: int) -> AbsInfo:
        buf = bytearray(24)
        fcntl.ioctl(self.fd, EVIOCGABS(axis), buf)
        return AbsInfo(*struct.unpack("<6i", buf))

    def read_events(self) -> list[InputEvent]:
        """Drain pending events (non-blocking). Raises OSError(ENODEV) when the
        device disappears."""
        try:
            data = os.read(self.fd, EVENT_SIZE * 64)
        except BlockingIOError:
            return []
        out = []
        for off in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
            _, _, etype, code, value = struct.unpack_from(EVENT_FMT, data, off)
            out.append(InputEvent(etype, code, value))
        return out


def list_devices() -> list[tuple[str, str]]:
    """[(path, name)] for every /dev/input/event* we can open."""
    out = []
    for entry in sorted(os.listdir("/dev/input"), key=lambda s: (len(s), s)):
        if not entry.startswith("event"):
            continue
        path = f"/dev/input/{entry}"
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError:
            continue
        try:
            buf = bytearray(256)
            fcntl.ioctl(fd, EVIOCGNAME(len(buf)), buf)
            out.append((path, bytes(buf).split(b"\0", 1)[0].decode(errors="replace")))
        finally:
            os.close(fd)
    return out


def find_device(name_substr: str, *, require_gamepad: bool = True,
                exclude: set[str] | None = None) -> str | None:
    """Path of the first device whose name contains name_substr and (if
    require_gamepad) exposes a gamepad button (BTN_A..) and ABS_X."""
    for path, name in list_devices():
        if name_substr.lower() not in name.lower():
            continue
        if exclude and path in exclude:
            continue
        if not require_gamepad:
            return path
        try:
            dev = EvdevDevice(path)
        except OSError:
            continue
        try:
            if BTN_A in dev.keys and ABS_X in dev.axes:
                return path
        finally:
            dev.close()
    return None
