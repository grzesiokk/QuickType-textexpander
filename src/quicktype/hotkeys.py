from __future__ import annotations

from dataclasses import dataclass

DEFAULT_QUICK_ACCESS_HOTKEY = "ctrl_alt_space"
DEFAULT_CLIPBOARD_CAPTURE_HOTKEY = "ctrl_alt_n"
DISABLED_HOTKEY = "disabled"


@dataclass(frozen=True, slots=True)
class HotkeySpec:
    control: bool
    alt: bool
    shift: bool


QUICK_ACCESS_HOTKEY_SPECS: dict[str, HotkeySpec | None] = {
    DEFAULT_QUICK_ACCESS_HOTKEY: HotkeySpec(control=True, alt=True, shift=False),
    "ctrl_shift_space": HotkeySpec(control=True, alt=False, shift=True),
    "alt_shift_space": HotkeySpec(control=False, alt=True, shift=True),
    DISABLED_HOTKEY: None,
}
HOTKEY_SPECS = QUICK_ACCESS_HOTKEY_SPECS

CLIPBOARD_CAPTURE_HOTKEY_SPECS: dict[str, HotkeySpec | None] = {
    DEFAULT_CLIPBOARD_CAPTURE_HOTKEY: HotkeySpec(
        control=True,
        alt=True,
        shift=False,
    ),
    "alt_shift_n": HotkeySpec(control=False, alt=True, shift=True),
    DISABLED_HOTKEY: None,
}


def normalize_quick_access_hotkey(value: str | None) -> str:
    return (
        value
        if value in QUICK_ACCESS_HOTKEY_SPECS
        else DEFAULT_QUICK_ACCESS_HOTKEY
    )


def normalize_clipboard_capture_hotkey(value: str | None) -> str:
    return (
        value
        if value in CLIPBOARD_CAPTURE_HOTKEY_SPECS
        else DEFAULT_CLIPBOARD_CAPTURE_HOTKEY
    )


def hotkey_matches(
    hotkey: str,
    *,
    control: bool,
    alt: bool,
    shift: bool,
    right_alt: bool,
    windows: bool,
    specs: dict[str, HotkeySpec | None] = QUICK_ACCESS_HOTKEY_SPECS,
) -> bool:
    spec = specs.get(hotkey)
    if spec is None or right_alt or windows:
        return False
    return (control, alt, shift) == (spec.control, spec.alt, spec.shift)
