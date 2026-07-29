import ctypes
import queue
from pathlib import Path

import pytest

import quicktype.hook as hook_module
from quicktype.hook import (
    INJECTED_EVENT_MARKER,
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    KeyboardHookEngine,
    VK_RETURN,
    _text_inputs,
)
from quicktype.models import Snippet, TriggerMode
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
    engine = KeyboardHookEngine([], excluded_processes={"Notepad.EXE"})
    assert engine._excluded_processes == {"notepad.exe"}
    engine.set_excluded_processes({"Code.exe"})
    assert engine._excluded_processes == {"code.exe"}
    engine.set_clipboard_capture_hotkey("alt_shift_n")
    assert engine._clipboard_capture_hotkey == "alt_shift_n"
    engine.start()
    try:
        assert engine._hook_thread_id
        assert bool(engine._keyboard_hook)
    finally:
        engine.stop()


def test_quick_access_modifier_rejects_altgr(monkeypatch) -> None:
    class FakeUser32:
        def __init__(self, pressed: set[int]) -> None:
            self.pressed = pressed

        def GetKeyState(self, key: int) -> int:
            return 0x8000 if key in self.pressed else 0

    engine = KeyboardHookEngine([])
    monkeypatch.setattr(
        hook_module,
        "user32",
        FakeUser32({hook_module.VK_CONTROL, hook_module.VK_MENU}),
    )
    assert engine._quick_access_modifier_is_down()

    engine.set_quick_access_hotkey("ctrl_shift_space")
    monkeypatch.setattr(
        hook_module,
        "user32",
        FakeUser32({hook_module.VK_CONTROL, hook_module.VK_SHIFT}),
    )
    assert engine._quick_access_modifier_is_down()

    monkeypatch.setattr(
        hook_module,
        "user32",
        FakeUser32(
            {hook_module.VK_CONTROL, hook_module.VK_MENU, hook_module.VK_RMENU}
        ),
    )
    assert not engine._quick_access_modifier_is_down()


def test_quick_access_chord_is_suppressed_and_emitted_once_per_press(
    monkeypatch,
) -> None:
    class FakeUser32:
        def GetKeyState(self, key: int) -> int:
            return (
                0x8000
                if key in {hook_module.VK_CONTROL, hook_module.VK_MENU}
                else 0
            )

        def GetForegroundWindow(self) -> int:
            return 2468

    windows: list[int] = []
    engine = KeyboardHookEngine([], on_quick_access=windows.append)
    monkeypatch.setattr(engine, "_is_own_window", lambda _window: False)
    monkeypatch.setattr(hook_module, "user32", FakeUser32())
    event = hook_module.KBDLLHOOKSTRUCT(
        vkCode=hook_module.VK_SPACE,
        scanCode=0,
        flags=0,
        time=0,
        dwExtraInfo=0,
    )
    pointer = ctypes.addressof(event)

    assert engine._keyboard_proc(0, hook_module.WM_KEYDOWN, pointer) == 1
    assert engine._keyboard_proc(0, hook_module.WM_KEYDOWN, pointer) == 1
    assert windows == [2468]
    assert engine._keyboard_proc(0, hook_module.WM_KEYUP, pointer) == 1
    assert engine._keyboard_proc(0, hook_module.WM_KEYDOWN, pointer) == 1
    assert windows == [2468, 2468]


def test_clipboard_capture_chord_is_global_and_emitted_once_per_press(
    monkeypatch,
) -> None:
    class FakeUser32:
        def GetKeyState(self, key: int) -> int:
            return (
                0x8000
                if key in {hook_module.VK_CONTROL, hook_module.VK_MENU}
                else 0
            )

    captures: list[str] = []
    engine = KeyboardHookEngine(
        [],
        on_clipboard_capture=lambda: captures.append("capture"),
    )
    engine.set_active(False)
    monkeypatch.setattr(hook_module, "user32", FakeUser32())
    event = hook_module.KBDLLHOOKSTRUCT(
        vkCode=hook_module.VK_N,
        scanCode=0,
        flags=0,
        time=0,
        dwExtraInfo=0,
    )
    pointer = ctypes.addressof(event)

    assert engine._keyboard_proc(0, hook_module.WM_KEYDOWN, pointer) == 1
    assert engine._keyboard_proc(0, hook_module.WM_KEYDOWN, pointer) == 1
    assert captures == ["capture"]
    assert engine._keyboard_proc(0, hook_module.WM_KEYUP, pointer) == 1
    assert engine._keyboard_proc(0, hook_module.WM_KEYDOWN, pointer) == 1
    assert captures == ["capture", "capture"]


def test_direct_expansion_is_queued_without_requiring_active(monkeypatch) -> None:
    engine = KeyboardHookEngine([])
    snippet = Snippet(None, ";sig", "Regards", TriggerMode.DELIMITER)
    monkeypatch.setattr(engine, "_is_own_window", lambda _window: False)
    monkeypatch.setattr(hook_module, "process_name_from_window", lambda _window: "Notepad.exe")

    assert engine.expand_directly(snippet, 42)
    task = engine._tasks.get_nowait()
    assert task is not None
    assert task.foreground_window == 42
    assert task.action.snippet == snippet
    assert not task.require_active
    with pytest.raises(queue.Empty):
        engine._tasks.get_nowait()

    scoped = Snippet(
        None,
        ";code",
        "Code only",
        TriggerMode.IMMEDIATE,
        applications=("Code.exe",),
    )
    assert not engine.expand_directly(scoped, 42)
