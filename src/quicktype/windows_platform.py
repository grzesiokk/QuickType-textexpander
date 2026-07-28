from __future__ import annotations

import ctypes
import sys
import time
import winreg
from ctypes import wintypes
from pathlib import Path

from .constants import AUTOSTART_VALUE_NAME

CF_UNICODETEXT = 13
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UIA_IS_PASSWORD_PROPERTY_ID = 30019
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SW_RESTORE = 9

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def autostart_command(executable: Path | None = None) -> str:
    target = Path(executable or sys.executable).resolve()
    return f'"{target}" --minimized'


def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
        return bool(value)
    except FileNotFoundError:
        return False


def set_autostart(enabled: bool, executable: Path | None = None) -> None:
    if enabled:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(
                key,
                AUTOSTART_VALUE_NAME,
                0,
                winreg.REG_SZ,
                autostart_command(executable),
            )
        return

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            access=winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, AUTOSTART_VALUE_NAME)
    except FileNotFoundError:
        pass


def repair_autostart_if_enabled() -> None:
    if not is_autostart_enabled():
        return
    expected = autostart_command()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        current, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
    if current != expected:
        set_autostart(True)


def read_clipboard_text() -> str:
    for attempt in range(3):
        if user32.OpenClipboard(None):
            break
        if attempt == 2:
            return ""
        time.sleep(0.01)
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def restore_foreground_window(window: int) -> bool:
    if not window or not user32.IsWindow(window):
        return False
    if user32.IsIconic(window):
        user32.ShowWindow(window, SW_RESTORE)
    return bool(user32.SetForegroundWindow(window))


def process_name_from_window(window: int) -> str:
    if not window:
        return ""
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
    if not process_id.value:
        return ""
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        process_id.value,
    )
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return Path(buffer.value).name
    finally:
        kernel32.CloseHandle(handle)


class PasswordFieldDetector:
    """Best-effort UI Automation password-field detection."""

    def __init__(self) -> None:
        self._automation = None
        self._initialized = False
        self._com_initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        try:
            import comtypes
            import comtypes.client

            comtypes.CoInitialize()
            self._com_initialized = True
            automation_module = comtypes.client.GetModule("UIAutomationCore.dll")
            self._automation = comtypes.client.CreateObject(
                automation_module.CUIAutomation,
                interface=automation_module.IUIAutomation,
            )
        except Exception:
            self._automation = None

    def close(self) -> None:
        self._automation = None
        if not self._com_initialized:
            return
        try:
            import comtypes

            comtypes.CoUninitialize()
        finally:
            self._com_initialized = False

    def is_password_field(self) -> bool:
        self.initialize()
        if self._automation is None:
            return False
        try:
            focused = self._automation.GetFocusedElement()
            return bool(focused.GetCurrentPropertyValue(UIA_IS_PASSWORD_PROPERTY_ID))
        except Exception:
            return False
