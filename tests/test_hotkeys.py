import pytest

from quicktype.hotkeys import (
    DEFAULT_QUICK_ACCESS_HOTKEY,
    hotkey_matches,
    normalize_quick_access_hotkey,
)


def test_unknown_hotkey_falls_back_to_default() -> None:
    assert normalize_quick_access_hotkey("invalid") == DEFAULT_QUICK_ACCESS_HOTKEY
    assert normalize_quick_access_hotkey(None) == DEFAULT_QUICK_ACCESS_HOTKEY


@pytest.mark.parametrize(
    ("hotkey", "control", "alt", "shift", "expected"),
    [
        ("ctrl_alt_space", True, True, False, True),
        ("ctrl_alt_space", True, True, True, False),
        ("ctrl_shift_space", True, False, True, True),
        ("alt_shift_space", False, True, True, True),
        ("disabled", True, True, False, False),
    ],
)
def test_hotkey_presets_require_exact_modifiers(
    hotkey: str,
    control: bool,
    alt: bool,
    shift: bool,
    expected: bool,
) -> None:
    assert (
        hotkey_matches(
            hotkey,
            control=control,
            alt=alt,
            shift=shift,
            right_alt=False,
            windows=False,
        )
        is expected
    )


def test_altgr_and_windows_modifier_are_rejected() -> None:
    assert not hotkey_matches(
        "ctrl_alt_space",
        control=True,
        alt=True,
        shift=False,
        right_alt=True,
        windows=False,
    )
    assert not hotkey_matches(
        "ctrl_alt_space",
        control=True,
        alt=True,
        shift=False,
        right_alt=False,
        windows=True,
    )
