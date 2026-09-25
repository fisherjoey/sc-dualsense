"""Minimal /dev/uhid protocol wrapper (stdlib only).

Struct layouts follow include/uapi/linux/uhid.h. struct uhid_event is
__attribute__((packed)): a u32 type followed directly by the union payload.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass

# enum uhid_event_type
UHID_DESTROY = 1
UHID_START = 2
UHID_STOP = 3
UHID_OPEN = 4
UHID_CLOSE = 5
UHID_OUTPUT = 6
UHID_GET_REPORT = 9
UHID_GET_REPORT_REPLY = 10
UHID_CREATE2 = 11
UHID_INPUT2 = 12
UHID_SET_REPORT = 13
UHID_SET_REPORT_REPLY = 14

# enum uhid_report_type
UHID_FEATURE_REPORT = 0
UHID_OUTPUT_REPORT = 1
UHID_INPUT_REPORT = 2

BUS_USB = 0x03
BUS_BLUETOOTH = 0x05

HID_MAX_DESCRIPTOR_SIZE = 4096
UHID_DATA_MAX = 4096

_CREATE2_FMT = "<L128s64s64sHHLLLL4096s"
EVENT_SIZE = struct.calcsize(_CREATE2_FMT)  # 4376, the largest union member

EVENT_NAMES = {
    UHID_START: "START", UHID_STOP: "STOP", UHID_OPEN: "OPEN", UHID_CLOSE: "CLOSE",
    UHID_OUTPUT: "OUTPUT", UHID_GET_REPORT: "GET_REPORT", UHID_SET_REPORT: "SET_REPORT",
}


@dataclass
class UhidEvent:
    type: int
    # UHID_START
    dev_flags: int = 0
    # UHID_OUTPUT / SET_REPORT payload
    data: bytes = b""
    rtype: int = 0
    # GET_REPORT / SET_REPORT
    id: int = 0
    rnum: int = 0

    @property
    def name(self) -> str:
        return EVENT_NAMES.get(self.type, f"type{self.type}")


class UhidDevice:
    """A virtual HID device backed by /dev/uhid."""

    def __init__(self, *, name: str, rd: bytes, bus: int, vendor: int, product: int,
                 version: int = 0, country: int = 0, phys: str = "", uniq: str = "",
                 path: str = "/dev/uhid"):
        if not (0 < len(rd) <= HID_MAX_DESCRIPTOR_SIZE):
            raise ValueError("bad report descriptor size")
        self.name, self.rd, self.bus = name, rd, bus
        self.vendor, self.product, self.version, self.country = vendor, product, version, country
        self.phys, self.uniq = phys, uniq
        self.fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        self.started = False
        self.opened = 0

    def fileno(self) -> int:
        return self.fd

    def create(self) -> None:
        ev = struct.pack(
            _CREATE2_FMT, UHID_CREATE2,
            self.name.encode(), self.phys.encode(), self.uniq.encode(),
            len(self.rd), self.bus, self.vendor, self.product, self.version, self.country,
            self.rd,
        )
        os.write(self.fd, ev)

    def destroy(self) -> None:
        try:
            os.write(self.fd, struct.pack("<L", UHID_DESTROY))
        except OSError:
            pass

    def close(self) -> None:
        self.destroy()
        os.close(self.fd)

    def send_input(self, report: bytes) -> None:
        """UHID_INPUT2: deliver one HID input report (report id as first byte)."""
        os.write(self.fd, struct.pack("<LH", UHID_INPUT2, len(report)) + report)

    def get_report_reply(self, req_id: int, data: bytes, err: int = 0) -> None:
        os.write(self.fd, struct.pack("<LLHH", UHID_GET_REPORT_REPLY, req_id, err, len(data)) + data)

    def set_report_reply(self, req_id: int, err: int = 0) -> None:
        os.write(self.fd, struct.pack("<LLH", UHID_SET_REPORT_REPLY, req_id, err))

    def read_event(self) -> UhidEvent:
        """Blocking read of one kernel->user event (use select() first)."""
        buf = os.read(self.fd, EVENT_SIZE)
        (etype,) = struct.unpack_from("<L", buf, 0)
        ev = UhidEvent(type=etype)
        if etype == UHID_START:
            (ev.dev_flags,) = struct.unpack_from("<Q", buf, 4)
            self.started = True
        elif etype == UHID_STOP:
            self.started = False
        elif etype == UHID_OPEN:
            self.opened += 1
        elif etype == UHID_CLOSE:
            self.opened = max(0, self.opened - 1)
        elif etype == UHID_OUTPUT:
            # struct uhid_output_req { __u8 data[4096]; __u16 size; __u8 rtype; }
            size, rtype = struct.unpack_from("<HB", buf, 4 + UHID_DATA_MAX)
            ev.data = bytes(buf[4:4 + min(size, UHID_DATA_MAX)])
            ev.rtype = rtype
        elif etype == UHID_GET_REPORT:
            # struct uhid_get_report_req { __u32 id; __u8 rnum; __u8 rtype; }
            ev.id, ev.rnum, ev.rtype = struct.unpack_from("<LBB", buf, 4)
        elif etype == UHID_SET_REPORT:
            # struct uhid_set_report_req { __u32 id; __u8 rnum; __u8 rtype; __u16 size; __u8 data[]; }
            ev.id, ev.rnum, ev.rtype, size = struct.unpack_from("<LBBH", buf, 4)
            ev.data = bytes(buf[12:12 + min(size, UHID_DATA_MAX)])
        return ev
