# sc-dualsense

A small Linux daemon that makes a Steam Controller show up as a Sony DualSense.
It reads the gamepad that Steam Input creates for the Steam Controller and
feeds it into a virtual DualSense built with the kernel's `/dev/uhid`
interface.

I wrote it for Kingdom Come: Deliverance II running under Proton, which would
not accept the Steam Controller any other way. It may help with other games
that have a working PlayStation input path under Proton but a broken Xbox one.

## Why it exists

KCD2 reads Xbox (XInput) controllers through Microsoft's GameInput service,
which does not work under Proton. No Xbox-style controller ever connects,
including the virtual Xbox pad that Steam Input creates. The game also has a
native Sony path (it logs as `scepad0` in `kcd.log`), and that path does work
under Proton by reading a DualSense over hidraw.

That path needs two things:

1. `DisableHidraw=0` in the game's Proton prefix. It goes in `system.reg`
   under the key `System\CurrentControlSet\Services\winebus`.
2. A real DualSense HID device. This daemon provides one. It creates a
   virtual `054c:0ce6` device through `/dev/uhid`, the kernel's
   `hid-playstation` driver binds to it and exposes `/dev/hidrawN`, and the
   game reads that node.

The Steam Controller has no gamepad HID mode of its own. Steam Input turns it
into a virtual "Microsoft X-Box 360 pad", and the daemon reads that evdev
device. That also means the daemon does not care which Steam Controller model
you have. It was written against the 2025 Steam Controller (USB `28de:1302`,
Puck dongle `28de:1304`), which the kernel `hid-steam` driver does not
support as of Linux 7.2.

## Requirements

- Linux with the `uhid` and `hid-playstation` kernel modules. Most desktop
  distributions ship both.
- Python 3.9 or newer. The daemon uses only the standard library, so there is
  nothing to install with pip.
- Steam running, with Steam Input enabled for the Steam Controller.
- Write access to `/dev/uhid` for the user running the daemon. See below.
- Read access to the virtual DualSense's hidraw node for the game. The
  `steam-devices` udev rules (packaged by most distributions, often installed
  with Steam) already grant this.

### /dev/uhid access

`/dev/uhid` is usually owned by root with no access for normal users. One way
to open it up for the logged-in user is a udev rule:

```
# /etc/udev/rules.d/60-uhid.rules
KERNEL=="uhid", GROUP="input", MODE="0660", TAG+="uaccess"
```

Then run `sudo udevadm control --reload && sudo udevadm trigger`. Some
packages, such as Sunshine, install a rule like this already. Check with
`ls -l /dev/uhid` and `getfacl /dev/uhid`.

## Install

Clone the repository wherever you like. The systemd unit below assumes
`~/.local/share/sc-dualsense`:

```
git clone https://github.com/fisherjoey/sc-dualsense ~/.local/share/sc-dualsense
```

The `bin/sc-dualsense` launcher runs the package from the checkout, so there
is no install step.

## Steam settings

- Steam > Settings > Controller: turn **off** "Enable Steam Input for
  PlayStation controllers". This one is required. When it is on, Steam takes
  over the virtual DualSense and adds `0x054c/0x0ce6` to the game's
  `SDL_GAMECONTROLLER_IGNORE_DEVICES` list. Proton's winebus skips any hidraw
  device on that list, so the game never sees the pad and `scepad0` stays
  disconnected. Steam builds the list when the game launches, so relaunch the
  game after changing this setting. It does not affect the Steam Controller
  itself. To check from outside the game, run
  `tr '\0' '\n' < /proc/$(pgrep -f KingdomCome.exe | head -1)/environ | grep -c 0x0ce6`.
  It should print 0.
- Give the Steam Controller a gamepad layout for the desktop (Steam >
  Settings > Controller > Desktop Layout, then pick the Gamepad template).
  On KDE Plasma under Wayland, Steam Input cannot always tell which window has
  focus and falls back to the desktop layout. If that layout is keyboard and
  mouse, the virtual Xbox pad stops sending input and the game loses the
  controller.
- The game's own Steam Input setting can stay as it is once PlayStation
  support is off.
- Unplug any physical DualSense before launching, or the game may pick that
  one instead.

The game (CryEngine) only scans for controllers at startup, so start the
daemon first and then launch the game. Because the game sees a DualSense, it
shows PlayStation button prompts. There is no setting to change that.

## Usage

```
bin/sc-dualsense run            # bridge Steam's virtual X-Box pad to a virtual DualSense
bin/sc-dualsense selftest       # virtual DualSense with scripted inputs, no controller needed
bin/sc-dualsense watch          # print evdev events from the DualSense (or --device NAME|/dev/input/eventN)
bin/sc-dualsense list           # list evdev devices
```

Options for `run`:

- `--source SUBSTR`: part of the source device name. Defaults to
  `X-Box 360 pad`.
- `--profile xpad`: the button mapping to use. `xpad` is the only one so far.
- `--grab`: open the source exclusively so nothing else reads it.
- `--rate HZ`: report rate once input settles. Defaults to 250.

If the source device disappears, the daemon keeps the virtual pad alive and
reconnects when the source comes back. Steam re-creating its virtual pad does
not need a restart.

### Checking that it works

1. Run `bin/sc-dualsense selftest` in one terminal. The log should show
   `virtual DualSense ready: ... hidraw=['hidrawN'] evdev=['eventN', ...]`.
2. In another terminal, run `evtest /dev/input/eventN` (or
   `bin/sc-dualsense watch --device /dev/input/eventN`). The buttons, dpad,
   sticks and triggers should cycle through.
3. Run `bin/sc-dualsense run`, press buttons on the Steam Controller, and
   check the same way.
4. Launch KCD2. Its `kcd.log`, in the game's install directory inside the
   Proton prefix, should contain
   `CScePad::SetControllerType(), m_ControllerType: DualSense`.

## Running it as a systemd user service

`systemd/sc-dualsense.service` runs the bridge as a user service. Its
`ExecStart` points at `%h/.local/share/sc-dualsense/bin/sc-dualsense`. If you
cloned the repository somewhere else, edit that line first.

```
mkdir -p ~/.config/systemd/user
ln -sf ~/.local/share/sc-dualsense/systemd/sc-dualsense.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now sc-dualsense
journalctl --user -u sc-dualsense -f
```

## Button mapping (profile `xpad`)

| Steam virtual pad | DualSense |
|---|---|
| A / B / X / Y | cross / circle / square / triangle |
| LB / RB | L1 / R1 |
| LT / RT (analog) | L2 / R2 (analog, digital bit above 30/255) |
| left stick, right stick, stick clicks | left stick, right stick, L3 / R3 |
| dpad | dpad |
| Back (View) / Start / Guide | touchpad click / Options / PS |

Steam Input's layout for the controller decides how the trackpads, grips and
gyro map onto those Xbox controls. You can tune all of that in Steam without
touching this code.

## Known limits

- Only tested with Kingdom Come: Deliverance II under Proton.
- The game shows PlayStation button prompts.
- Input only. Rumble, lightbar, adaptive triggers, the touchpad surface and
  motion sensors are not forwarded.
- The DualSense identity (report descriptor, calibration and firmware reports)
  was captured from one USB DualSense. The MAC addresses in the pairing report
  are made-up placeholders.
- Needs Steam Input running, because the source is Steam's virtual Xbox pad.

## Code layout

- `sc_dualsense/uhid.py`: the `/dev/uhid` protocol (CREATE2, INPUT2, GET/SET_REPORT replies)
- `sc_dualsense/dualsense.py`: device identity, input report 0x01 builder, feature report replies
- `sc_dualsense/ds_data.py`: report descriptor and feature reports captured from a physical USB DualSense
- `sc_dualsense/evdev.py`: standard-library evdev reader (ioctl capabilities, absinfo, grab)
- `sc_dualsense/mapping.py`: evdev to DualSense mapping profiles
- `sc_dualsense/bridge.py`: main loop, selftest, watch

## License

MIT. See [LICENSE](LICENSE).
