from __future__ import annotations

import ctypes
import os
import queue
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Mapping

from .constants import INJECTED_EVENT_MARKER
from .hotkeys import (
    CLIPBOARD_CAPTURE_HOTKEY_SPECS,
    DEFAULT_CLIPBOARD_CAPTURE_HOTKEY,
    DEFAULT_QUICK_ACCESS_HOTKEY,
    hotkey_matches,
    normalize_clipboard_capture_hotkey,
    normalize_quick_access_hotkey,
)
from .matcher import ExpansionAction, SnippetMatcher
from .models import Snippet, SnippetContentFormat, snippet_applies_to_process
from .template_engine import TOKEN_RE, render_template
from .windows_platform import PasswordFieldDetector, process_name_from_window, read_clipboard_text

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0
WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
WM_XBUTTONDOWN = 0x020B

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_DELETE = 0x2E
VK_N = 0x4E
VK_V = 0x56
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_RMENU = 0xA5

LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1
PM_NOREMOVE = 0x0000

NAVIGATION_KEYS = {
    VK_ESCAPE,
    VK_PRIOR,
    VK_NEXT,
    VK_END,
    VK_HOME,
    VK_LEFT,
    VK_UP,
    VK_RIGHT,
    VK_DOWN,
    VK_DELETE,
}
MODIFIER_KEYS = {VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, VK_RMENU}
MOUSE_RESET_MESSAGES = {WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_XBUTTONDOWN}

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallNextHookEx.restype = LRESULT
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.GetMessageW.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
    wintypes.UINT,
]
user32.PeekMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetKeyboardState.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
user32.GetKeyboardState.restype = wintypes.BOOL
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.GetKeyboardLayout.restype = wintypes.HKL
user32.ToUnicodeEx.argtypes = [
    wintypes.UINT,
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_ubyte),
    wintypes.LPWSTR,
    ctypes.c_int,
    wintypes.UINT,
    wintypes.HKL,
]
user32.ToUnicodeEx.restype = ctypes.c_int
user32.GetKeyState.argtypes = [ctypes.c_int]
user32.GetKeyState.restype = wintypes.SHORT
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


@dataclass(frozen=True, slots=True)
class ExpansionTask:
    action: ExpansionAction
    foreground_window: int
    require_active: bool = True
    values: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class TextInsertionTask:
    text: str
    foreground_window: int
    require_active: bool = False


@dataclass(frozen=True, slots=True)
class RichPasteTask:
    expansion_task: ExpansionTask
    cursor_from_end: int = 0
    cursor_present: bool = False


class KeyboardHookEngine:
    def __init__(
        self,
        snippets: list[Snippet] | None = None,
        *,
        on_expansion: Callable[[Snippet], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_quick_access: Callable[[int], None] | None = None,
        on_clipboard_capture: Callable[[], None] | None = None,
        on_form_request: Callable[[ExpansionAction, int], None] | None = None,
        on_rich_request: Callable[[ExpansionTask], None] | None = None,
        quick_access_hotkey: str = DEFAULT_QUICK_ACCESS_HOTKEY,
        clipboard_capture_hotkey: str = DEFAULT_CLIPBOARD_CAPTURE_HOTKEY,
        excluded_processes: set[str] | None = None,
    ) -> None:
        self.matcher = SnippetMatcher([])
        self._snippets = tuple(snippets or [])
        self.on_expansion = on_expansion
        self.on_error = on_error
        self.on_quick_access = on_quick_access
        self.on_clipboard_capture = on_clipboard_capture
        self.on_form_request = on_form_request
        self.on_rich_request = on_rich_request
        self._quick_access_hotkey = normalize_quick_access_hotkey(
            quick_access_hotkey
        )
        self._clipboard_capture_hotkey = (
            normalize_clipboard_capture_hotkey(
                clipboard_capture_hotkey
            )
        )
        self._active = True
        self._active_lock = threading.Lock()
        self._tasks: queue.Queue[
            ExpansionTask | RichPasteTask | TextInsertionTask | None
        ] = queue.Queue()
        self._hook_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._hook_thread_id = 0
        self._keyboard_hook = wintypes.HHOOK()
        self._mouse_hook = wintypes.HHOOK()
        self._keyboard_callback = HOOKPROC(self._keyboard_proc)
        self._mouse_callback = HOOKPROC(self._mouse_proc)
        self._last_window = 0
        self._last_process_excluded = False
        self._excluded_processes = {
            process.casefold() for process in (excluded_processes or set()) if process
        }
        self._suppressed_keyups: set[int] = set()
        self._quick_access_pressed = False
        self._clipboard_capture_pressed = False
        self._password_detector = PasswordFieldDetector()
        self._started = threading.Event()
        self._startup_error: str | None = None

    @property
    def active(self) -> bool:
        with self._active_lock:
            return self._active

    def set_active(self, active: bool) -> None:
        with self._active_lock:
            self._active = active
        self.matcher.clear()

    def replace_snippets(self, snippets: list[Snippet]) -> None:
        with self._active_lock:
            self._snippets = tuple(snippets)
            self._last_window = 0
        self.matcher.clear()

    def set_excluded_processes(self, processes: set[str]) -> None:
        with self._active_lock:
            self._excluded_processes = {
                process.casefold() for process in processes if process
            }
            self._last_window = 0
            self._last_process_excluded = False
        self.matcher.clear()

    def set_quick_access_hotkey(self, hotkey: str) -> None:
        with self._active_lock:
            self._quick_access_hotkey = normalize_quick_access_hotkey(hotkey)
            self._quick_access_pressed = False

    def set_clipboard_capture_hotkey(self, hotkey: str) -> None:
        with self._active_lock:
            self._clipboard_capture_hotkey = (
                normalize_clipboard_capture_hotkey(hotkey)
            )
            self._clipboard_capture_pressed = False

    def start(self) -> None:
        if self._hook_thread and self._hook_thread.is_alive():
            return
        self._started.clear()
        self._startup_error = None
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="QuickTypeExpansionWorker",
            daemon=True,
        )
        self._hook_thread = threading.Thread(
            target=self._hook_loop,
            name="QuickTypeKeyboardHook",
            daemon=True,
        )
        self._worker_thread.start()
        self._hook_thread.start()
        self._started.wait(timeout=3)
        if self._startup_error:
            raise RuntimeError(self._startup_error)
        if not self._started.is_set():
            raise RuntimeError("Windows keyboard hook did not start")

    def stop(self) -> None:
        if self._hook_thread_id:
            user32.PostThreadMessageW(self._hook_thread_id, WM_QUIT, 0, 0)
        self._tasks.put(None)
        if self._hook_thread:
            self._hook_thread.join(timeout=2)
        if self._worker_thread:
            self._worker_thread.join(timeout=2)
        self._hook_thread = None
        self._worker_thread = None
        self._hook_thread_id = 0

    def _hook_loop(self) -> None:
        self._hook_thread_id = int(kernel32.GetCurrentThreadId())
        message = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_NOREMOVE)
        module = kernel32.GetModuleHandleW(None)
        self._keyboard_hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            self._keyboard_callback,
            module,
            0,
        )
        self._mouse_hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL,
            self._mouse_callback,
            module,
            0,
        )
        if not self._keyboard_hook:
            error = ctypes.get_last_error()
            self._startup_error = f"SetWindowsHookExW failed ({error})"
            self._started.set()
            return

        self._started.set()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                pass
        finally:
            if self._keyboard_hook:
                user32.UnhookWindowsHookEx(self._keyboard_hook)
            if self._mouse_hook:
                user32.UnhookWindowsHookEx(self._mouse_hook)
            self._keyboard_hook = wintypes.HHOOK()
            self._mouse_hook = wintypes.HHOOK()

    def _keyboard_proc(self, code: int, message: int, data_pointer: int) -> int:
        if code < HC_ACTION:
            return int(user32.CallNextHookEx(self._keyboard_hook, code, message, data_pointer))
        if message in (WM_KEYUP, WM_SYSKEYUP):
            data = ctypes.cast(data_pointer, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if data.flags & LLKHF_INJECTED or data.dwExtraInfo == INJECTED_EVENT_MARKER:
                return int(user32.CallNextHookEx(self._keyboard_hook, code, message, data_pointer))
            virtual_key = int(data.vkCode)
            if virtual_key == VK_SPACE and self._quick_access_pressed:
                self._quick_access_pressed = False
                return 1
            if virtual_key == VK_N and self._clipboard_capture_pressed:
                self._clipboard_capture_pressed = False
                return 1
            if virtual_key in self._suppressed_keyups:
                self._suppressed_keyups.discard(virtual_key)
                return 1
            return int(user32.CallNextHookEx(self._keyboard_hook, code, message, data_pointer))
        if message not in (WM_KEYDOWN, WM_SYSKEYDOWN):
            return int(user32.CallNextHookEx(self._keyboard_hook, code, message, data_pointer))

        data = ctypes.cast(data_pointer, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if data.flags & LLKHF_INJECTED or data.dwExtraInfo == INJECTED_EVENT_MARKER:
            return int(user32.CallNextHookEx(self._keyboard_hook, code, message, data_pointer))
        virtual_key = int(data.vkCode)
        if (
            virtual_key == VK_N
            and self.on_clipboard_capture is not None
            and self._clipboard_capture_modifier_is_down()
        ):
            self.matcher.clear()
            if not self._clipboard_capture_pressed:
                self._clipboard_capture_pressed = True
                self.on_clipboard_capture()
            return 1
        if (
            virtual_key == VK_SPACE
            and self.on_quick_access is not None
            and self._quick_access_modifier_is_down()
        ):
            self.matcher.clear()
            if not self._quick_access_pressed:
                self._quick_access_pressed = True
                foreground = int(user32.GetForegroundWindow() or 0)
                if not self._is_own_window(foreground):
                    self.on_quick_access(foreground)
            return 1

        if not self.active:
            self.matcher.clear()
            return int(user32.CallNextHookEx(self._keyboard_hook, code, message, data_pointer))

        foreground = int(user32.GetForegroundWindow() or 0)
        if foreground != self._last_window:
            self.matcher.clear()
            self._last_window = foreground
            process_name = process_name_from_window(foreground).casefold()
            with self._active_lock:
                self._last_process_excluded = process_name in self._excluded_processes
                applicable_snippets = [
                    snippet
                    for snippet in self._snippets
                    if snippet_applies_to_process(snippet, process_name)
                ]
            self.matcher.replace_snippets(applicable_snippets)
        if self._last_process_excluded:
            self.matcher.clear()
            return int(user32.CallNextHookEx(self._keyboard_hook, code, message, data_pointer))
        if self._is_own_window(foreground):
            self.matcher.clear()
            return int(user32.CallNextHookEx(self._keyboard_hook, code, message, data_pointer))

        if virtual_key == VK_BACK:
            self.matcher.backspace()
            return int(user32.CallNextHookEx(self._keyboard_hook, code, message, data_pointer))
        if virtual_key in NAVIGATION_KEYS:
            self.matcher.clear()
            return int(user32.CallNextHookEx(self._keyboard_hook, code, message, data_pointer))
        if virtual_key in MODIFIER_KEYS:
            return int(user32.CallNextHookEx(self._keyboard_hook, code, message, data_pointer))

        if self._shortcut_modifier_is_down():
            self.matcher.clear()
            return int(user32.CallNextHookEx(self._keyboard_hook, code, message, data_pointer))

        action: ExpansionAction | None
        if virtual_key in (VK_TAB, VK_RETURN):
            action = self.matcher.feed_special_separator(virtual_key)
        else:
            character = self._virtual_key_to_character(virtual_key, int(data.scanCode))
            if not character:
                self.matcher.clear()
                return int(user32.CallNextHookEx(self._keyboard_hook, code, message, data_pointer))
            action = self.matcher.feed_character(character)

        if action is None:
            return int(user32.CallNextHookEx(self._keyboard_hook, code, message, data_pointer))
        if self.on_form_request is not None and _template_may_require_form(
            action.snippet.expansion
        ):
            self.on_form_request(action, foreground)
        else:
            self._tasks.put_nowait(
                ExpansionTask(action=action, foreground_window=foreground)
            )
        self._suppressed_keyups.add(virtual_key)
        return 1

    def _mouse_proc(self, code: int, message: int, data_pointer: int) -> int:
        if code >= HC_ACTION and message in MOUSE_RESET_MESSAGES:
            data = ctypes.cast(data_pointer, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if not (data.flags & LLMHF_INJECTED) and data.dwExtraInfo != INJECTED_EVENT_MARKER:
                self.matcher.clear()
        return int(user32.CallNextHookEx(self._mouse_hook, code, message, data_pointer))

    def _worker_loop(self) -> None:
        self._password_detector.initialize()
        try:
            while True:
                task = self._tasks.get()
                if task is None:
                    return
                try:
                    if isinstance(task, TextInsertionTask):
                        self._perform_text_insertion(task)
                    elif isinstance(task, RichPasteTask):
                        self._perform_rich_paste(task)
                    else:
                        self._perform_expansion(task)
                except Exception as error:
                    if isinstance(task, ExpansionTask):
                        self._restore_suppressed_input(task.action)
                    elif isinstance(task, RichPasteTask):
                        self._restore_suppressed_input(
                            task.expansion_task.action
                        )
                    if self.on_error:
                        self.on_error(str(error))
        finally:
            self._password_detector.close()

    def _perform_expansion(self, task: ExpansionTask) -> None:
        if (
            (task.require_active and not self.active)
            or int(user32.GetForegroundWindow() or 0) != task.foreground_window
            or self._password_detector.is_password_field()
        ):
            self._restore_suppressed_input(task.action)
            return

        if task.action.snippet.content_format == SnippetContentFormat.RICH:
            if self.on_rich_request is None:
                raise RuntimeError("Rich text expansion is unavailable.")
            self.on_rich_request(task)
            return

        rendered = render_template(
            task.action.snippet.expansion,
            clipboard_provider=read_clipboard_text,
            values=dict(task.values),
            match_groups=dict(task.action.match_groups),
            snippet_provider=self._snippet_provider(task.foreground_window),
        )
        if rendered.issues:
            raise ValueError(rendered.issues[0].message)
        inputs: list[INPUT] = []
        for _ in range(task.action.delete_count):
            inputs.extend(_virtual_key_inputs(VK_BACK))
        inputs.extend(_text_inputs(rendered.text))
        if task.action.success_suffix_text:
            inputs.extend(_text_inputs(task.action.success_suffix_text))
        if task.action.success_suffix_vk is not None:
            inputs.extend(_virtual_key_inputs(task.action.success_suffix_vk))
        suffix_distance = len(task.action.success_suffix_text)
        if task.action.success_suffix_vk is not None:
            suffix_distance += 1
        for _ in range(
            rendered.cursor_from_end
            + (suffix_distance if rendered.cursor_present else 0)
        ):
            inputs.extend(_virtual_key_inputs(VK_LEFT))

        if inputs and not _send_inputs(inputs):
            raise RuntimeError("SendInput did not accept all keyboard events")
        if self.on_expansion:
            self.on_expansion(task.action.snippet)

    def _perform_rich_paste(self, task: RichPasteTask) -> None:
        expansion = task.expansion_task
        if (
            (expansion.require_active and not self.active)
            or int(user32.GetForegroundWindow() or 0) != expansion.foreground_window
            or self._password_detector.is_password_field()
        ):
            self._restore_suppressed_input(expansion.action)
            return
        inputs: list[INPUT] = []
        for _ in range(expansion.action.delete_count):
            inputs.extend(_virtual_key_inputs(VK_BACK))
        inputs.extend(_paste_inputs())
        if expansion.action.success_suffix_text:
            inputs.extend(_text_inputs(expansion.action.success_suffix_text))
        if expansion.action.success_suffix_vk is not None:
            inputs.extend(_virtual_key_inputs(expansion.action.success_suffix_vk))
        suffix_distance = len(expansion.action.success_suffix_text)
        if expansion.action.success_suffix_vk is not None:
            suffix_distance += 1
        for _ in range(
            task.cursor_from_end
            + (suffix_distance if task.cursor_present else 0)
        ):
            inputs.extend(_virtual_key_inputs(VK_LEFT))
        if not _send_inputs(inputs):
            raise RuntimeError("SendInput did not accept the rich paste sequence")
        if self.on_expansion:
            self.on_expansion(expansion.action.snippet)

    def _perform_text_insertion(self, task: TextInsertionTask) -> None:
        if (
            (task.require_active and not self.active)
            or int(user32.GetForegroundWindow() or 0) != task.foreground_window
            or self._password_detector.is_password_field()
        ):
            return
        if not _send_inputs(_text_inputs(task.text)):
            raise RuntimeError("SendInput did not accept all keyboard events")

    def _restore_suppressed_input(self, action: ExpansionAction) -> None:
        inputs: list[INPUT] = []
        if action.fallback_text:
            inputs.extend(_text_inputs(action.fallback_text))
        if action.fallback_vk is not None:
            inputs.extend(_virtual_key_inputs(action.fallback_vk))
        if inputs:
            _send_inputs(inputs)

    def _is_own_window(self, window: int) -> bool:
        if not window:
            return False
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
        return int(process_id.value) == os.getpid()

    def _shortcut_modifier_is_down(self) -> bool:
        control = bool(user32.GetKeyState(VK_CONTROL) & 0x8000)
        alt = bool(user32.GetKeyState(VK_MENU) & 0x8000)
        right_alt = bool(user32.GetKeyState(VK_RMENU) & 0x8000)
        windows = bool(
            (user32.GetKeyState(VK_LWIN) & 0x8000)
            or (user32.GetKeyState(VK_RWIN) & 0x8000)
        )
        altgr = control and alt and right_alt
        return windows or ((control or alt) and not altgr)

    def _quick_access_modifier_is_down(self) -> bool:
        control = bool(user32.GetKeyState(VK_CONTROL) & 0x8000)
        alt = bool(user32.GetKeyState(VK_MENU) & 0x8000)
        shift = bool(user32.GetKeyState(VK_SHIFT) & 0x8000)
        right_alt = bool(user32.GetKeyState(VK_RMENU) & 0x8000)
        windows = bool(
            (user32.GetKeyState(VK_LWIN) & 0x8000)
            or (user32.GetKeyState(VK_RWIN) & 0x8000)
        )
        with self._active_lock:
            hotkey = self._quick_access_hotkey
        return hotkey_matches(
            hotkey,
            control=control,
            alt=alt,
            shift=shift,
            right_alt=right_alt,
            windows=windows,
        )

    def _clipboard_capture_modifier_is_down(self) -> bool:
        control = bool(user32.GetKeyState(VK_CONTROL) & 0x8000)
        alt = bool(user32.GetKeyState(VK_MENU) & 0x8000)
        shift = bool(user32.GetKeyState(VK_SHIFT) & 0x8000)
        right_alt = bool(user32.GetKeyState(VK_RMENU) & 0x8000)
        windows = bool(
            (user32.GetKeyState(VK_LWIN) & 0x8000)
            or (user32.GetKeyState(VK_RWIN) & 0x8000)
        )
        with self._active_lock:
            hotkey = self._clipboard_capture_hotkey
        return hotkey_matches(
            hotkey,
            control=control,
            alt=alt,
            shift=shift,
            right_alt=right_alt,
            windows=windows,
            specs=CLIPBOARD_CAPTURE_HOTKEY_SPECS,
        )

    def expand_directly(self, snippet: Snippet, foreground_window: int) -> bool:
        if not foreground_window or self._is_own_window(foreground_window):
            return False
        process_name = process_name_from_window(foreground_window).casefold()
        with self._active_lock:
            if process_name in self._excluded_processes:
                return False
        if not snippet_applies_to_process(snippet, process_name):
            return False
        action = ExpansionAction(snippet=snippet, delete_count=0)
        if self.on_form_request is not None and _template_may_require_form(
            snippet.expansion
        ):
            self.on_form_request(action, foreground_window)
            return True
        self._tasks.put_nowait(
            ExpansionTask(
                action=action,
                foreground_window=foreground_window,
                require_active=False,
            )
        )
        return True

    def expand_action(
        self,
        action: ExpansionAction,
        foreground_window: int,
        *,
        values: Mapping[str, str] | None = None,
        require_active: bool = True,
    ) -> None:
        self._tasks.put_nowait(
            ExpansionTask(
                action=action,
                foreground_window=foreground_window,
                require_active=require_active,
                values=tuple((values or {}).items()),
            )
        )

    def paste_rich(
        self,
        task: ExpansionTask,
        *,
        cursor_from_end: int,
        cursor_present: bool,
    ) -> None:
        self._tasks.put_nowait(
            RichPasteTask(
                expansion_task=task,
                cursor_from_end=cursor_from_end,
                cursor_present=cursor_present,
            )
        )

    def insert_text(self, text: str, foreground_window: int) -> bool:
        if not text or not foreground_window or self._is_own_window(foreground_window):
            return False
        process_name = process_name_from_window(foreground_window).casefold()
        with self._active_lock:
            if process_name in self._excluded_processes:
                return False
        self._tasks.put_nowait(
            TextInsertionTask(
                text=text,
                foreground_window=foreground_window,
            )
        )
        return True

    def cancel_action(self, action: ExpansionAction) -> None:
        self._restore_suppressed_input(action)

    def available_snippet_provider(
        self,
        foreground_window: int,
    ) -> Callable[[str], str | None]:
        return self._snippet_provider(foreground_window)

    def _snippet_provider(
        self,
        foreground_window: int,
    ) -> Callable[[str], str | None]:
        process_name = process_name_from_window(foreground_window).casefold()
        with self._active_lock:
            available = {
                snippet.abbreviation: snippet.expansion
                for snippet in self._snippets
                if snippet.enabled
                and snippet_applies_to_process(snippet, process_name)
            }
        return available.get

    def _virtual_key_to_character(self, virtual_key: int, scan_code: int) -> str:
        keyboard_state = (ctypes.c_ubyte * 256)()
        if not user32.GetKeyboardState(keyboard_state):
            return ""
        keyboard_state[virtual_key] |= 0x80
        foreground = user32.GetForegroundWindow()
        thread_id = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        layout = user32.GetKeyboardLayout(thread_id)
        buffer = ctypes.create_unicode_buffer(8)
        result = user32.ToUnicodeEx(
            virtual_key,
            scan_code,
            keyboard_state,
            buffer,
            len(buffer),
            0,
            layout,
        )
        if result <= 0:
            return ""
        return buffer.value[:result]


def _keyboard_input(virtual_key: int, scan_code: int, flags: int) -> INPUT:
    return INPUT(
        type=INPUT_KEYBOARD,
        ki=KEYBDINPUT(
            wVk=virtual_key,
            wScan=scan_code,
            dwFlags=flags,
            time=0,
            dwExtraInfo=INJECTED_EVENT_MARKER,
        ),
    )


def _virtual_key_inputs(virtual_key: int) -> list[INPUT]:
    return [
        _keyboard_input(virtual_key, 0, 0),
        _keyboard_input(virtual_key, 0, KEYEVENTF_KEYUP),
    ]


def _paste_inputs() -> list[INPUT]:
    return [
        _keyboard_input(VK_CONTROL, 0, 0),
        _keyboard_input(VK_V, 0, 0),
        _keyboard_input(VK_V, 0, KEYEVENTF_KEYUP),
        _keyboard_input(VK_CONTROL, 0, KEYEVENTF_KEYUP),
    ]


def _text_inputs(text: str) -> list[INPUT]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    inputs: list[INPUT] = []
    for character in normalized:
        if character == "\n":
            inputs.extend(_virtual_key_inputs(VK_RETURN))
            continue
        if character == "\t":
            inputs.extend(_virtual_key_inputs(VK_TAB))
            continue
        encoded = character.encode("utf-16-le", errors="surrogatepass")
        for index in range(0, len(encoded), 2):
            code_unit = encoded[index] | (encoded[index + 1] << 8)
            inputs.append(_keyboard_input(0, code_unit, KEYEVENTF_UNICODE))
            inputs.append(_keyboard_input(0, code_unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    return inputs


def _send_inputs(inputs: list[INPUT]) -> bool:
    for offset in range(0, len(inputs), 256):
        batch = inputs[offset : offset + 256]
        array_type = INPUT * len(batch)
        array = array_type(*batch)
        sent = int(user32.SendInput(len(batch), array, ctypes.sizeof(INPUT)))
        if sent != len(batch):
            return False
    return True


def _template_may_require_form(template: str) -> bool:
    return any(
        match.group(1).strip().startswith(
            ("input:", "choice:", "check:", "snippet:")
        )
        for match in TOKEN_RE.finditer(template)
    )
