from __future__ import annotations

from dataclasses import dataclass

DEFAULT_QUICK_ACCESS_HOTKEY = "ctrl_alt_space"
DISABLED_HOTKEY = "disabled"


@dataclass(frozen=True, slots=True)
class HotkeySpec:
    control: bool
    alt: bool
    shift: bool


HOTKEY_SPECS: dict[str, HotkeySpec | None] = {
    DEFAULT_QUICK_ACCESS_HOTKEY: HotkeySpec(control=True, alt=True, shift=False),
    "ctrl_shift_space": HotkeySpec(control=True, alt=False, shift=True),
    "alt_shift_space": HotkeySpec(control=False, alt=True, shift=True),
    DISABLED_HOTKEY: None,
}


def normalize_quick_access_hotkey(value: str | None) -> str:
    return value if value in HOTKEY_SPECS else DEFAULT_QUICK_ACCESS_HOTKEY


def hotkey_matches(
    hotkey: str,
    *,
    control: bool,
    alt: bool,
    shift: bool,
    right_alt: bool,
    windows: bool,
) -> bool:
    spec = HOTKEY_SPECS.get(normalize_quick_access_hotkey(hotkey))
    if spec is None or right_alt or windows:
        return False
    return (control, alt, shift) == (spec.control, spec.alt, spec.shift)
