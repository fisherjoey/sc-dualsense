"""evdev -> DualSense mapping profiles."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import evdev
from .dualsense import DualSenseState


def _stick(v: float) -> int:
    return max(0, min(255, round(v * 255)))


@dataclass
class Mapping:
    name: str
    # evdev key code -> DualSense button name
    keys: dict[int, str] = field(default_factory=dict)
    # evdev abs code -> DualSense axis: lx ly rx ry l2 r2 dpad_x dpad_y
    axes: dict[int, str] = field(default_factory=dict)
    invert: set[int] = field(default_factory=set)

    def apply_key(self, state: DualSenseState, code: int, pressed: bool) -> None:
        target = self.keys.get(code)
        if target is None:
            return
        if target.startswith("dpad_"):
            # digital dpad buttons: dpad_up/down/left/right
            axis, sign = {"dpad_up": ("dpad_y", -1), "dpad_down": ("dpad_y", 1),
                          "dpad_left": ("dpad_x", -1), "dpad_right": ("dpad_x", 1)}[target]
            cur = getattr(state, axis)
            if pressed:
                setattr(state, axis, sign)
            elif cur == sign:
                setattr(state, axis, 0)
            return
        state.set_button(target, pressed)

    def apply_abs(self, state: DualSenseState, code: int, norm: float) -> None:
        target = self.axes.get(code)
        if target is None:
            return
        if code in self.invert:
            norm = 1.0 - norm
        if target in ("dpad_x", "dpad_y"):
            setattr(state, target, -1 if norm < 0.25 else 1 if norm > 0.75 else 0)
        else:
            setattr(state, target, _stick(norm))


# Steam Input's virtual "Microsoft X-Box 360 pad" (also any xpad-style pad).
# Note the kernel naming quirk: BTN_X (0x133) is the physical West button and
# BTN_Y (0x134) the physical North button on Xbox-layout pads.
XPAD = Mapping(
    name="xpad",
    keys={
        evdev.BTN_A: "cross", evdev.BTN_B: "circle", evdev.BTN_X: "square", evdev.BTN_Y: "triangle",
        evdev.BTN_TL: "l1", evdev.BTN_TR: "r1",
        # Back/View -> touchpad click: KCD2 opens the map with it on DualSense (Create is unused).
        evdev.BTN_SELECT: "touchpad", evdev.BTN_START: "options", evdev.BTN_MODE: "ps",
        evdev.BTN_THUMBL: "l3", evdev.BTN_THUMBR: "r3",
        evdev.BTN_DPAD_UP: "dpad_up", evdev.BTN_DPAD_DOWN: "dpad_down",
        evdev.BTN_DPAD_LEFT: "dpad_left", evdev.BTN_DPAD_RIGHT: "dpad_right",
    },
    axes={
        evdev.ABS_X: "lx", evdev.ABS_Y: "ly", evdev.ABS_RX: "rx", evdev.ABS_RY: "ry",
        evdev.ABS_Z: "l2", evdev.ABS_RZ: "r2",
        evdev.ABS_HAT0X: "dpad_x", evdev.ABS_HAT0Y: "dpad_y",
    },
)

PROFILES = {"xpad": XPAD}
