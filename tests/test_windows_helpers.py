import ctypes
from pathlib import Path

from quicktype.hook import (
    INJECTED_EVENT_MARKER,
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    KeyboardHookEngine,
    VK_RETURN,
    _text_inputs,
)
from quicktype.windows_platform import autostart_command


def test_autostart_command_quotes_executable() -> None:
    assert autostart_command(Path(r"C:\My Apps\QuickType.exe")) == (
        r'"C:\My Apps\QuickType.exe" --minimized'
    )


def test_unicode_input_uses_utf16_units_and_marker() -> None:
    inputs = _text_inputs("ą😀")
    # ą = one UTF-16 unit, 😀 = surrogate pair; every unit has down + up.
    assert len(inputs) == 6
    assert all(item.ki.dwExtraInfo == INJECTED_EVENT_MARKER for item in inputs)
    assert all(item.ki.dwFlags & KEYEVENTF_UNICODE for item in inputs)
    assert inputs[1].ki.dwFlags & KEYEVENTF_KEYUP


def test_newline_is_sent_as_return_key() -> None:
    inputs = _text_inputs("\n")
    assert len(inputs) == 2
    assert inputs[0].ki.wVk == VK_RETURN
    assert not (inputs[0].ki.dwFlags & KEYEVENTF_UNICODE)


def test_global_hook_can_start_and_stop() -> None:
    engine = KeyboardHookEngine([])
    engine.start()
    try:
        assert engine._hook_thread_id
        assert bool(engine._keyboard_hook)
    finally:
        engine.stop()
