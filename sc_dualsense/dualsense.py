"""DualSense (USB) HID identity, report descriptor, feature reports and the
64-byte input report (report id 0x01) builder.

Descriptor and feature-report payloads were captured from a physical USB
DualSense (see ds_data.py); the report layout follows InputPlumber's ds5
target and struct dualsense_input_report in drivers/hid/hid-playstation.c.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import ds_data, uhid

VID = 0x054C
PID = 0x0CE6
VERSION = 0x8111
NAME = "Sony Interactive Entertainment DualSense Wireless Controller"

INPUT_REPORT_ID = 0x01
INPUT_REPORT_SIZE = 64
OUTPUT_REPORT_ID = 0x02

FEATURE_CALIBRATION = 0x05
FEATURE_PAIRING_INFO = 0x09
FEATURE_FIRMWARE_INFO = 0x20

# Fake, locally-administered MAC. hid-playstation reads it from the pairing
# report, uses it as the HID uniq, and rejects a second device with the same
# MAC, so this must differ from any real DualSense plugged into the same machine.
MAC = bytes.fromhex("025cd5c0ffee")  # stored LSB-first in the report, like hardware


def mac_uniq(mac: bytes = MAC) -> str:
    """Kernel formats the MAC reversed (%pMR)."""
    return ":".join(f"{b:02x}" for b in reversed(mac))


USB_DESCRIPTOR = ds_data.USB_DESCRIPTOR

# Feature report replies (first byte = report id). hid-playstation requests
# these during probe and fails the device if they are missing.
FEATURE_REPORTS = {
    FEATURE_PAIRING_INFO: ds_data.FEATURE_PAIRING_INFO_DATA,
    FEATURE_FIRMWARE_INFO: ds_data.FEATURE_FIRMWARE_INFO_DATA,
    FEATURE_CALIBRATION: ds_data.FEATURE_CALIBRATION_DATA,
}
assert FEATURE_REPORTS[FEATURE_PAIRING_INFO][1:7] == MAC
assert len(FEATURE_REPORTS[FEATURE_PAIRING_INFO]) == 20
assert len(FEATURE_REPORTS[FEATURE_FIRMWARE_INFO]) == 64
assert len(FEATURE_REPORTS[FEATURE_CALIBRATION]) == 41

# Buttons (name -> (byte offset in report, bit mask)). Offsets include the
# report-id byte: byte 8 = buttons[0], 9 = buttons[1], 10 = buttons[2].
BUTTONS = {
    "square": (8, 0x10), "cross": (8, 0x20), "circle": (8, 0x40), "triangle": (8, 0x80),
    "l1": (9, 0x01), "r1": (9, 0x02), "l2": (9, 0x04), "r2": (9, 0x08),
    "create": (9, 0x10), "options": (9, 0x20), "l3": (9, 0x40), "r3": (9, 0x80),
    "ps": (10, 0x01), "touchpad": (10, 0x02), "mute": (10, 0x04),
}

# Hat switch nibble: N, NE, E, SE, S, SW, W, NW, none.
_HAT = {
    (0, -1): 0, (1, -1): 1, (1, 0): 2, (1, 1): 3,
    (0, 1): 4, (-1, 1): 5, (-1, 0): 6, (-1, -1): 7, (0, 0): 8,
}

CENTER = 0x80
TRIGGER_DIGITAL_THRESHOLD = 30  # 0..255


@dataclass
class DualSenseState:
    lx: int = CENTER
    ly: int = CENTER
    rx: int = CENTER
    ry: int = CENTER
    l2: int = 0
    r2: int = 0
    dpad_x: int = 0  # -1 left, 0, 1 right
    dpad_y: int = 0  # -1 up, 0, 1 down
    buttons: set[str] = field(default_factory=set)

    def neutral(self) -> None:
        self.lx = self.ly = self.rx = self.ry = CENTER
        self.l2 = self.r2 = 0
        self.dpad_x = self.dpad_y = 0
        self.buttons.clear()

    def set_button(self, name: str, pressed: bool) -> None:
        if name not in BUTTONS:
            raise KeyError(name)
        (self.buttons.add if pressed else self.buttons.discard)(name)

    def pack(self, seq: int) -> bytes:
        r = bytearray(INPUT_REPORT_SIZE)
        r[0] = INPUT_REPORT_ID
        r[1], r[2], r[3], r[4] = self.lx, self.ly, self.rx, self.ry
        r[5], r[6] = self.l2, self.r2
        r[7] = seq & 0xFF
        r[8] = _HAT[(self.dpad_x, self.dpad_y)]
        for name in self.buttons:
            off, mask = BUTTONS[name]
            r[off] |= mask
        # Analog triggers also assert the digital L2/R2 bits like hardware does.
        if self.l2 >= TRIGGER_DIGITAL_THRESHOLD:
            r[9] |= BUTTONS["l2"][1]
        if self.r2 >= TRIGGER_DIGITAL_THRESHOLD:
            r[9] |= BUTTONS["r2"][1]
        # Accelerometer: ~1 g on Z so the pad reads as lying flat (8192 LSB/g).
        r[26], r[27] = 0x00, 0x20
        # Touch points: bit 7 set = no contact.
        r[33] = 0x80
        r[37] = 0x80
        # Battery status: high nibble 0x2 = full; USB power + data plugged.
        r[53] = 0x28
        r[54] = 0x08
        return bytes(r)


def make_uhid_device(*, uniq: str | None = None) -> uhid.UhidDevice:
    return uhid.UhidDevice(
        name=NAME, rd=USB_DESCRIPTOR, bus=uhid.BUS_USB,
        vendor=VID, product=PID, version=VERSION,
        uniq=uniq if uniq is not None else mac_uniq(),
    )


def handle_uhid_event(dev: uhid.UhidDevice, ev: uhid.UhidEvent, log=None) -> None:
    """Service the kernel's requests. Output reports (rumble/LEDs) are ignored."""
    if ev.type == uhid.UHID_GET_REPORT:
        data = FEATURE_REPORTS.get(ev.rnum)
        if log:
            log("uhid: GET_REPORT id=0x%02x type=%d -> %s" % (ev.rnum, ev.rtype, "ok" if data else "EIO"))
        if data is None:
            dev.get_report_reply(ev.id, b"", err=5)  # EIO
        else:
            dev.get_report_reply(ev.id, data)
    elif ev.type == uhid.UHID_SET_REPORT:
        if log:
            log("uhid: SET_REPORT id=0x%02x type=%d len=%d" % (ev.rnum, ev.rtype, len(ev.data)))
        dev.set_report_reply(ev.id, 0)
    elif ev.type == uhid.UHID_OUTPUT and log:
        log("uhid: OUTPUT report id=0x%02x len=%d" % (ev.data[0] if ev.data else 0, len(ev.data)))
