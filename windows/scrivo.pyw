"""Scrivo on Windows: hold or tap Ctrl+Windows, speak, words land at the cursor.

A tiny see-through teal bar always waits at the bottom of the screen (it steps
aside for full-screen apps and can be dragged anywhere; its spot is remembered).
Pressing the keys, or clicking the tiny bar, grows it into the speaking bar with
an X, moving sound bars and a check, and the words heard so far in small type
above it. A short soft sound marks the start and the finish.

  Hold Ctrl+Windows and speak, let go to finish.
  Or tap Ctrl+Windows (or click the tiny bar), speak as long as you like, and tap
  again (or click the check).
  Esc or the X throws the piece away.
  Ctrl+Windows with any other key (Ctrl+Windows+D and the like) keeps its usual
  meaning, and the Start menu never opens from the dictation keys.

Some other dictation apps also answer Ctrl+Windows (OTHER_APP below). While one
of them is open it keeps that key and its own bar, so nothing is ever typed
twice: this program then answers only Ctrl+Alt+Space and its tiny bar stays
hidden. Ctrl+Alt+Space works at all times. The other app is never touched.

The finished words are pasted where the cursor is and stay on the clipboard, so
with no text box selected they are simply ready to paste. A small teal icon by
the clock names the keys and offers Quit.

Only the Python standard library and Windows' own interfaces are used.
The log holds states and timings, never words.
"""
import collections
import ctypes
import json
import logging
import logging.handlers
import math
import os
import queue
import struct
import sys
import threading
import time
import traceback
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes as wt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dictation_client as dc

NAME = 'Scrivo'
CONFIG = Path(os.environ.get('APPDATA', '.')) / NAME / 'config.json'
LOCAL = Path(os.environ.get('LOCALAPPDATA', '.')) / NAME
SHORTCUTS = ['ctrl+alt+space', 'ctrl+alt+d']   # first free one wins
COMBO = 'ctrl+win'                              # the dictation keys: Left Ctrl + Left Windows
OTHER_APP = 'wispr flow.exe'                    # another dictation app on Ctrl+Windows: while it runs, the key stays its own
HOLD = 0.35                                     # held this long: let go to finish
MASK = 0xE8                                     # an unassigned key: tells Windows the Windows key was used
MINE = 0x50444943                               # marks our own key presses so the watch ignores them
CTRLS, WINS = (0x11, 0xA2, 0xA3), (0x5B, 0x5C)

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
winmm = ctypes.WinDLL('winmm')
shell32 = ctypes.WinDLL('shell32', use_last_error=True)
log = logging.getLogger('dictation')

# ---- Windows interfaces --------------------------------------------------
PTR = ctypes.c_size_t
WM_HOTKEY, WM_APP = 0x0312, 0x8000
MODS = {'alt': 1, 'ctrl': 2, 'shift': 4, 'win': 8}
VKS = {'alt': 0x12, 'ctrl': 0x11, 'shift': 0x10, 'win': 0x5B}
KEYS = dict({'space': 0x20}, **{chr(c).lower(): c for c in range(0x41, 0x5B)},
            **{'f%d' % n: 0x6F + n for n in range(1, 13)})


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [('wFormatTag', wt.WORD), ('nChannels', wt.WORD), ('nSamplesPerSec', wt.DWORD),
                ('nAvgBytesPerSec', wt.DWORD), ('nBlockAlign', wt.WORD), ('wBitsPerSample', wt.WORD),
                ('cbSize', wt.WORD)]


class WAVEHDR(ctypes.Structure):
    _fields_ = [('lpData', ctypes.c_void_p), ('dwBufferLength', wt.DWORD), ('dwBytesRecorded', wt.DWORD),
                ('dwUser', PTR), ('dwFlags', wt.DWORD), ('dwLoops', wt.DWORD),
                ('lpNext', ctypes.c_void_p), ('reserved', PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD), ('dwFlags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wt.WORD), ('wScan', wt.WORD), ('dwFlags', wt.DWORD), ('time', wt.DWORD),
                ('dwExtraInfo', PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', wt.DWORD), ('u', _INPUTUNION)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', wt.UINT), ('dwTime', wt.DWORD)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('rcMonitor', wt.RECT), ('rcWork', wt.RECT), ('dwFlags', wt.DWORD)]


class GUID(ctypes.Structure):
    _fields_ = [('a', wt.DWORD), ('b', wt.WORD), ('c', wt.WORD), ('d', ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('hWnd', ctypes.c_void_p), ('uID', wt.UINT), ('uFlags', wt.UINT),
                ('uCallbackMessage', wt.UINT), ('hIcon', ctypes.c_void_p), ('szTip', wt.WCHAR * 128),
                ('dwState', wt.DWORD), ('dwStateMask', wt.DWORD), ('szInfo', wt.WCHAR * 256),
                ('uVersion', wt.UINT), ('szInfoTitle', wt.WCHAR * 64), ('dwInfoFlags', wt.DWORD),
                ('guidItem', GUID), ('hBalloonIcon', ctypes.c_void_p)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('vkCode', wt.DWORD), ('scanCode', wt.DWORD), ('flags', wt.DWORD), ('time', wt.DWORD),
                ('dwExtraInfo', PTR)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD), ('th32ProcessID', wt.DWORD),
                ('th32DefaultHeapID', PTR), ('th32ModuleID', wt.DWORD), ('cntThreads', wt.DWORD),
                ('th32ParentProcessID', wt.DWORD), ('pcPriClassBase', wt.LONG), ('dwFlags', wt.DWORD),
                ('szExeFile', wt.WCHAR * 260)]


WNDPROC = getattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE)(
    ctypes.c_ssize_t, ctypes.c_void_p, wt.UINT, PTR, ctypes.c_ssize_t)
HOOKPROC = getattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE)(
    ctypes.c_ssize_t, ctypes.c_int, PTR, ctypes.c_ssize_t)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [('style', wt.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', ctypes.c_int),
                ('cbWndExtra', ctypes.c_int), ('hInstance', ctypes.c_void_p), ('hIcon', ctypes.c_void_p),
                ('hCursor', ctypes.c_void_p), ('hbrBackground', ctypes.c_void_p),
                ('lpszMenuName', wt.LPCWSTR), ('lpszClassName', wt.LPCWSTR)]


def _api():
    H = ctypes.c_void_p
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    kernel32.GetModuleHandleW.restype = H
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wt.ATOM
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, H, H, H, H]
    user32.CreateWindowExW.restype = H
    user32.DefWindowProcW.argtypes = [H, wt.UINT, PTR, ctypes.c_ssize_t]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.DestroyWindow.argtypes = [H]
    user32.PostMessageW.argtypes = [H, wt.UINT, PTR, ctypes.c_ssize_t]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
    user32.RegisterWindowMessageW.restype = wt.UINT
    user32.SetTimer.argtypes = [H, PTR, wt.UINT, H]
    user32.SetTimer.restype = PTR
    user32.KillTimer.argtypes = [H, PTR]
    user32.CreatePopupMenu.restype = H
    user32.AppendMenuW.argtypes = [H, wt.UINT, PTR, wt.LPCWSTR]
    user32.TrackPopupMenu.argtypes = [H, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, H, H]
    user32.TrackPopupMenu.restype = ctypes.c_int
    user32.DestroyMenu.argtypes = [H]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
    user32.LoadImageW.argtypes = [H, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
    user32.LoadImageW.restype = H
    user32.DestroyIcon.argtypes = [H]
    shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = wt.BOOL
    winmm.waveInOpen.argtypes = [ctypes.POINTER(H), wt.UINT, ctypes.POINTER(WAVEFORMATEX), PTR, PTR, wt.DWORD]
    for name in ('waveInPrepareHeader', 'waveInUnprepareHeader', 'waveInAddBuffer'):
        getattr(winmm, name).argtypes = [H, ctypes.POINTER(WAVEHDR), wt.UINT]
    for name in ('waveInStart', 'waveInReset', 'waveInClose'):
        getattr(winmm, name).argtypes = [H]
    user32.RegisterHotKey.argtypes = [H, ctypes.c_int, wt.UINT, wt.UINT]
    user32.UnregisterHotKey.argtypes = [H, ctypes.c_int]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), H, wt.UINT, wt.UINT]
    user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), H, wt.UINT, wt.UINT, wt.UINT]
    user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, PTR, PTR]
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.OpenClipboard.argtypes = [H]
    user32.SetClipboardData.argtypes = [wt.UINT, H]
    user32.SetClipboardData.restype = H
    user32.GetClipboardData.argtypes = [wt.UINT]
    user32.GetClipboardData.restype = H
    kernel32.GlobalAlloc.argtypes = [wt.UINT, PTR]
    kernel32.GlobalAlloc.restype = H
    kernel32.GlobalLock.argtypes = [H]
    kernel32.GlobalLock.restype = H
    kernel32.GlobalUnlock.argtypes = [H]
    kernel32.GlobalFree.argtypes = [H]
    kernel32.CreateMutexW.argtypes = [H, wt.BOOL, wt.LPCWSTR]
    kernel32.CreateMutexW.restype = H
    user32.GetForegroundWindow.restype = H
    user32.SetForegroundWindow.argtypes = [H]
    user32.GetParent.argtypes = [H]
    user32.GetParent.restype = H
    user32.MonitorFromWindow.argtypes = [H, wt.DWORD]
    user32.MonitorFromWindow.restype = H
    user32.GetMonitorInfoW.argtypes = [H, ctypes.POINTER(MONITORINFO)]
    user32.GetWindowLongPtrW.argtypes = [H, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = PTR
    user32.SetWindowLongPtrW.argtypes = [H, ctypes.c_int, PTR]
    user32.SetWindowLongPtrW.restype = PTR
    user32.GetWindowThreadProcessId.argtypes = [H, ctypes.POINTER(wt.DWORD)]
    user32.GetWindowThreadProcessId.restype = wt.DWORD
    user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
    user32.OpenInputDesktop.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    user32.OpenInputDesktop.restype = H
    user32.CloseDesktop.argtypes = [H]
    user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    kernel32.GetTickCount.restype = wt.DWORD
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, H, wt.DWORD]
    user32.SetWindowsHookExW.restype = H
    user32.CallNextHookEx.argtypes = [H, ctypes.c_int, PTR, ctypes.c_ssize_t]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.UnhookWindowsHookEx.argtypes = [H]
    user32.SetWindowPos.argtypes = [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
    user32.GetWindowRect.argtypes = [H, ctypes.POINTER(wt.RECT)]
    user32.GetClassNameW.argtypes = [H, wt.LPWSTR, ctypes.c_int]
    user32.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
    user32.MonitorFromPoint.restype = H
    shell32.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]
    kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = H
    kernel32.Process32FirstW.argtypes = [H, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [H, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.CloseHandle.argtypes = [H]
    winmm.PlaySoundW.argtypes = [ctypes.c_void_p, H, wt.DWORD]


def shortcut(text):
    """('ctrl+alt+space') -> (modifier bits, key, every key that must stay down)."""
    parts = text.lower().replace(' ', '').split('+')
    return sum(MODS[p] for p in parts[:-1]), KEYS[parts[-1]], [VKS[p] for p in parts[:-1]] + [KEYS[parts[-1]]]


def key_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def press(*steps):
    """Send key presses: (vk, True) is down, (vk, False) is up."""
    events = (INPUT * len(steps))()
    for item, (vk, down) in zip(events, steps):
        item.type = 1
        item.u.ki = KEYBDINPUT(vk, 0, 0 if down else 2, 0, 0)
    return user32.SendInput(len(steps), events, ctypes.sizeof(INPUT)) == len(steps)


def clipboard(text=None):
    """Read the clipboard's words, or put words on it. Returns the words or success."""
    for _ in range(20):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        return None if text is None else False
    try:
        if text is None:
            handle = user32.GetClipboardData(13)
            if not handle:
                return None
            address = kernel32.GlobalLock(handle)
            try:
                return ctypes.wstring_at(address) if address else None
            finally:
                kernel32.GlobalUnlock(handle)
        data = text.encode('utf-16-le') + b'\0\0'
        handle = kernel32.GlobalAlloc(2, len(data))
        address = kernel32.GlobalLock(handle)
        ctypes.memmove(address, data, len(data))
        kernel32.GlobalUnlock(handle)
        user32.EmptyClipboard()
        if not user32.SetClipboardData(13, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    finally:
        user32.CloseClipboard()


def idle_seconds():
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def desktop_open():
    """False while the lock screen has the keyboard."""
    handle = user32.OpenInputDesktop(0, False, 0x0100)
    if not handle:
        return False
    user32.CloseDesktop(handle)
    return True


def work_area():
    """Left, top, right, bottom of the usable part of the screen in use."""
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    monitor = user32.MonitorFromWindow(user32.GetForegroundWindow(), 2)
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        r = info.rcWork
        return r.left, r.top, r.right, r.bottom
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1) - 48


def area_at(x, y):
    """The usable part of the screen nearest to a point: left, top, right, bottom."""
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    monitor = user32.MonitorFromPoint(wt.POINT(int(x), int(y)), 2)
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        r = info.rcWork
        return r.left, r.top, r.right, r.bottom
    return work_area()


def place(spot, area, size, gap):
    """Where the tiny bar's window goes: its saved middle-bottom point kept inside the screen.

    spot is (middle x, bottom y) or None for the bottom middle, just above the taskbar.
    Returns the window's top-left corner.
    """
    left, top, right, bottom = area
    w, h = size
    x, y = spot if spot else ((left + right) // 2, bottom - gap)
    x = min(max(int(x), left + w // 2), right - (w - w // 2))
    y = min(max(int(y), top + h), bottom)
    return x - w // 2, y - h


def running(name=OTHER_APP):
    """True while a program with this file name runs (it may be several processes)."""
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return False
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        more = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while more:
            if entry.szExeFile.lower() == name:
                return True
            more = kernel32.Process32NextW(snap, ctypes.byref(entry))
        return False
    finally:
        kernel32.CloseHandle(snap)


def full_screen():
    """True while a game, a film or a slide show fills the whole screen."""
    state = ctypes.c_int(0)
    try:
        if shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0 and state.value in (2, 3, 4):
            return True
    except Exception:
        pass
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    kind = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, kind, 64)
    if kind.value in ('Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd'):
        return False                  # the desktop itself and the taskbar
    rect, info = wt.RECT(), MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    monitor = user32.MonitorFromWindow(hwnd, 2)
    if not (user32.GetWindowRect(hwnd, ctypes.byref(rect)) and monitor
            and user32.GetMonitorInfoW(monitor, ctypes.byref(info))):
        return False
    m = info.rcMonitor
    return rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom


# ---- The start and finish sounds -----------------------------------------
SOUNDS = {'start': [(740, 0.045), (988, 0.075)],      # a soft rising pair: listening
          'finish': [(988, 0.04), (1319, 0.08)],      # a little higher: done, words on the way
          'cancel': [(587, 0.05), (440, 0.085)]}      # falling: thrown away
_sound_bytes = {}


def tone(notes, volume=0.14):
    """A short soft sound, as the bytes of a WAV file: each (pitch, seconds) note in turn."""
    pcm = bytearray()
    for pitch, length in notes:
        count = int(dc.RATE * length)
        for i in range(count):
            t = i / dc.RATE
            shape = min(1.0, i / (dc.RATE * 0.004)) * (1 - i / count) ** 2    # quick rise, soft fall
            sound = math.sin(2 * math.pi * pitch * t) + 0.18 * math.sin(4 * math.pi * pitch * t)
            pcm += struct.pack('<h', int(32767 * volume * shape * sound / 1.18))
    return dc.wav(bytes(pcm))


def play(name):
    """Play one of the cues without waiting for it. Never fails a dictation."""
    try:
        if name not in _sound_bytes:
            data = tone(SOUNDS[name])
            _sound_bytes[name] = ctypes.create_string_buffer(data, len(data))   # kept: Windows reads it while playing
        winmm.PlaySoundW(ctypes.addressof(_sound_bytes[name]), None, 0x0004 | 0x0001 | 0x0002)
    except Exception:
        pass


class Combo:
    """Ctrl and Windows held together with nothing else: the dictation keys.

    Fed every key change. It never holds a key back from Windows; it only reports
    'down' when Ctrl and Windows are both held with no other key, 'up' when either
    is let go after that, and 'other' when another key joins them, so Ctrl+Windows+D
    and the like keep their usual meaning. A key that never reported being let go
    (Windows+L locks the screen before the letters come back up) is forgotten as
    soon as `still_down` says it is up.
    """

    def __init__(self, still_down=None):
        self.held, self.active, self.spoiled, self.presses = set(), False, False, 0
        self.still_down = still_down

    def _has(self, keys):
        return any(vk in self.held for vk in keys)

    def feed(self, vk, down):
        out = []
        if down:
            if vk in self.held:
                return out                        # the keyboard repeating a held key
            if self.still_down:
                self.held = {k for k in self.held if self.still_down(k)}
            self.held.add(vk)
            mine = vk in CTRLS or vk in WINS
            others = [k for k in self.held if k not in CTRLS and k not in WINS]
            if self.active and not mine:
                self.active, self.spoiled = False, True
                out.append('other')
            elif (mine and not self.active and not self.spoiled and not others
                  and self._has(CTRLS) and self._has(WINS)):
                self.active = True
                self.presses += 1
                out.append('down')
        else:
            self.held.discard(vk)
            both = self._has(CTRLS) and self._has(WINS)
            if self.active and not both:
                self.active = False
                out.append('up')
            if not self._has(CTRLS) and not self._has(WINS):
                self.spoiled = False
        return out


class Keys(threading.Thread):
    """Owns the dictation keys: Ctrl+Windows through a keyboard watch, and Ctrl+Alt+Space.

    Ctrl+Windows has no other key, so Windows cannot register it as a shortcut;
    a low-level keyboard watch sees every key change instead and passes every key
    on untouched. While the other dictation app is open the watch reports nothing (`combo_on`
    is off) so that app alone answers its own keys. Esc is taken only while a piece
    is being spoken.
    """

    def __init__(self, choices, events):
        super().__init__(daemon=True)
        self.choices, self.events = choices, events
        self.combo, self.held, self.ready, self.tid = None, [], threading.Event(), 0
        self.watch = Combo(key_down)
        self.combo_on, self.watching, self.hook = False, False, None

    def _key(self, code, kind, data):
        try:
            if code == 0:
                info = ctypes.cast(data, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if info.dwExtraInfo != MINE and kind in (0x100, 0x101, 0x104, 0x105):
                    now = time.monotonic()
                    for what in self.watch.feed(info.vkCode, kind in (0x100, 0x104)):
                        if not self.combo_on:
                            continue
                        if what == 'down':
                            # Windows would open Start when the Windows key comes up alone.
                            user32.PostThreadMessageW(self.tid, WM_APP + 3, 0, 0)
                            self.events.put(('shortcut', ('combo', self.watch.presses, now)))
                        elif what == 'other':
                            self.events.put(('other', self.watch.presses))
        except Exception:
            pass                                  # the keyboard must never wait on us
        return user32.CallNextHookEx(None, code, kind, data)

    def run(self):
        self.tid = kernel32.GetCurrentThreadId()
        message = wt.MSG()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        for text in self.choices:
            try:
                mods, key, held = shortcut(text)
            except KeyError:
                continue
            if user32.RegisterHotKey(None, 1, mods | 0x4000, key):
                self.combo, self.held = text, held
                break
        self.proc = HOOKPROC(self._key)
        self.hook = user32.SetWindowsHookExW(13, self.proc, kernel32.GetModuleHandleW(None), 0)
        self.watching = bool(self.hook)
        log.info('keyboard watch for %s: %s', COMBO, self.watching)
        self.ready.set()
        if not self.combo and not self.watching:
            return
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_HOTKEY:
                if message.wParam == 1:
                    self.events.put(('shortcut', ('hotkey', 0, time.monotonic())))
                else:
                    self.events.put(('escape', None))
            elif message.message == WM_APP + 1:
                user32.RegisterHotKey(None, 2, 0x4000, 0x1B)
            elif message.message == WM_APP + 2:
                user32.UnregisterHotKey(None, 2)
            elif message.message == WM_APP + 3 and key_down(0x5B) | key_down(0x5C):
                mask = (INPUT * 2)()
                for item, up in zip(mask, (0, 2)):
                    item.type = 1
                    item.u.ki = KEYBDINPUT(MASK, 0, up, 0, MINE)
                user32.SendInput(2, mask, ctypes.sizeof(INPUT))
        if self.hook:
            user32.UnhookWindowsHookEx(self.hook)

    def escape(self, on):
        user32.PostThreadMessageW(self.tid, WM_APP + (1 if on else 2), 0, 0)

    def names(self):
        """The keys to press right now, as printed: Ctrl+Win while ours, else the spare shortcut."""
        return COMBO if self.combo_on else (self.combo or COMBO)

    def down(self, source='hotkey'):
        if source == 'combo':
            # Joined by another key counts as still held until both are let go: never a tap.
            return self.watch.active or self.watch.spoiled
        if source == 'click':
            return False
        return bool(self.held) and all(key_down(vk) for vk in self.held)

    def any_down(self):
        return any(key_down(vk) for vk in self.held + list(VKS.values()) + [0x5C, 0xA2, 0xA3])


class Mic(threading.Thread):
    """The default microphone, 16 kHz mono, handed over in 50 ms pieces."""

    def __init__(self, sink, failed):
        super().__init__(daemon=True)
        self.sink, self.failed, self.stopping = sink, failed, threading.Event()

    def stop(self):
        self.stopping.set()

    def run(self):
        handle = ctypes.c_void_p()
        form = WAVEFORMATEX(1, 1, dc.RATE, dc.RATE * 2, 2, 16, 0)
        error = winmm.waveInOpen(ctypes.byref(handle), 0xFFFFFFFF, ctypes.byref(form), 0, 0, 0)
        if error:
            log.info('microphone did not open: %s', error)
            self.failed()
            return
        buffers = [ctypes.create_string_buffer(dc.CHUNK) for _ in range(8)]
        headers = [WAVEHDR(ctypes.addressof(b), dc.CHUNK, 0, 0, 0, 0, None, 0) for b in buffers]
        size = ctypes.sizeof(WAVEHDR)
        try:
            for header in headers:
                winmm.waveInPrepareHeader(handle, ctypes.byref(header), size)
                winmm.waveInAddBuffer(handle, ctypes.byref(header), size)
            winmm.waveInStart(handle)
            turn = 0
            while not self.stopping.is_set():
                header = headers[turn]
                if not header.dwFlags & 1:
                    time.sleep(0.005)
                    continue
                self.sink(ctypes.string_at(header.lpData, header.dwBytesRecorded))
                header.dwBytesRecorded, header.dwFlags = 0, 2
                winmm.waveInAddBuffer(handle, ctypes.byref(header), size)
                turn = (turn + 1) % len(headers)
            # Hand over what was captured up to the very end, in order.
            winmm.waveInReset(handle)
            for step in range(len(headers)):
                header = headers[(turn + step) % len(headers)]
                if header.dwFlags & 1 and header.dwBytesRecorded:
                    self.sink(ctypes.string_at(header.lpData, header.dwBytesRecorded))
        finally:
            winmm.waveInReset(handle)
            for header in headers:
                winmm.waveInUnprepareHeader(handle, ctypes.byref(header), size)
            winmm.waveInClose(handle)


class FileMic(threading.Thread):
    """A saved recording played as if spoken. Used only by the self-check."""

    def __init__(self, sink, failed, path, ended):
        super().__init__(daemon=True)
        import wave
        with wave.open(str(path)) as audio:
            self.pcm = audio.readframes(audio.getnframes())
        self.sink, self.ended, self.stopping = sink, ended, threading.Event()

    def stop(self):
        self.stopping.set()

    def run(self):
        for start in range(0, len(self.pcm), dc.CHUNK):
            if self.stopping.is_set():
                return
            self.sink(self.pcm[start:start + dc.CHUNK])
            time.sleep(dc.CHUNK / 2 / dc.RATE)
        self.ended()


# ---- The icon by the clock -----------------------------------------------
ICON_TEAL = (0x12, 0xA5, 0x94)     # the speaking bar's check: 5 to 1 against a dark taskbar
ICON_TEAL_LIGHT = (0x00, 0x6D, 0x60)   # the design's teal for light surfaces: a light taskbar needs the deeper one
ICON_SIZES = (16, 20, 24, 32, 40, 48)
QUIT = 'Quit dictation (it starts again when you sign in)'


def key_names(combo):
    """'ctrl+alt+space' -> 'Ctrl+Alt+Space', as printed on the keys."""
    return '+'.join(part.upper() if len(part) < 3 else part.capitalize() for part in combo.split('+'))


def icon_tip(combo):
    """What pointing at the icon says. Windows shows at most 127 characters."""
    return ('%s: hold %s and speak, let go to finish' % (NAME, key_names(combo)))[:127]


def icon_menu(combo, other_has_keys=False):
    """The menu's lines, top to bottom: (words, what choosing it does). None is a plain line; '' a divider."""
    keys = key_names(combo)
    lines = [(NAME, None),
             ('', None),
             ('Hold %s, speak, let go: your words land at the cursor' % keys, None),
             ('Or tap %s, speak, then tap it again (or click the check)' % keys, None)]
    if combo == COMBO:
        lines.append(('Or click the tiny bar at the bottom of the screen; drag it to move it', None))
    if other_has_keys:
        lines.append(('Ctrl+Win belongs to another dictation app while it is open; close it to use it here', None))
    return lines + [('With no text box selected, the words are copied, ready to paste', None),
                    ('Esc or the X throws the words away', None),
                    ('', None),
                    (QUIT, 'quit')]


def taskbar_is_light():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            return bool(winreg.QueryValueEx(key, 'SystemUsesLightTheme')[0])
    except Exception:
        return False                 # Windows' own default is a dark taskbar


def icon_pixels(size, fine=4, teal=ICON_TEAL):
    """A teal disc with three white sound bars: rows of (red, green, blue, cover 0-255), top row first."""
    half = size / 2.0
    radius = half - size * 0.02
    wide = size * 0.075
    bars = [(half - size * 0.20, size * 0.11), (half, size * 0.24), (half + size * 0.20, size * 0.11)]

    def in_bar(x, y):
        for centre, tall in bars:
            # an upright bar with round ends
            near = min(max(y, half - tall + wide), half + tall - wide)
            if (x - centre) ** 2 + (y - near) ** 2 <= wide * wide:
                return True
        return False
    rows = []
    for py in range(size):
        row = []
        for px in range(size):
            disc = white = 0
            for sy in range(fine):
                for sx in range(fine):
                    x, y = px + (sx + 0.5) / fine, py + (sy + 0.5) / fine
                    if (x - half) ** 2 + (y - half) ** 2 <= radius * radius:
                        disc += 1
                        white += in_bar(x, y)
            if not disc:
                row.append((0, 0, 0, 0))
                continue
            mix = white / disc
            row.append(tuple(round(255 * mix + c * (1 - mix)) for c in teal) + (round(255 * disc / fine ** 2),))
        rows.append(row)
    return rows


def icon_file(sizes=ICON_SIZES, teal=ICON_TEAL):
    """The same drawing at every taskbar size, as the bytes of one Windows icon file."""
    images = []
    for size in sizes:
        rows = icon_pixels(size, teal=teal)
        colour = b''.join(bytes((b, g, r, a)) for row in reversed(rows) for r, g, b, a in row)
        mask = b'\0' * (((size + 31) // 32) * 4 * size)
        images.append(struct.pack('<IiiHHIIiiII', 40, size, size * 2, 1, 32, 0, len(colour) + len(mask), 0, 0, 0, 0)
                      + colour + mask)
    head, at = struct.pack('<HHH', 0, 1, len(sizes)), 6 + 16 * len(sizes)
    for size, image in zip(sizes, images):
        head += struct.pack('<BBBBHHII', size, size, 0, 0, 1, 32, len(image), at)
        at += len(image)
    return head + b''.join(images)


def keep_in_view(marker):
    """Windows 11 tucks a new icon behind the little arrow. Ask once for ours to sit by the clock.

    Only once: if it is hidden later, that choice stands. True when the setting was changed now.
    """
    if marker.exists():
        return False
    import winreg
    found = False
    try:
        top = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Control Panel\NotifyIconSettings')
    except OSError:
        return False                 # an older Windows shows it without being asked
    with top:
        for index in range(winreg.QueryInfoKey(top)[0]):
            try:
                with winreg.OpenKey(top, winreg.EnumKey(top, index), 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
                    path = winreg.QueryValueEx(key, 'ExecutablePath')[0]
                    tip = winreg.QueryValueEx(key, 'InitialTooltip')[0]
                    if path.lower().endswith('pythonw.exe') and tip.startswith(NAME):
                        winreg.SetValueEx(key, 'IsPromoted', 0, winreg.REG_DWORD, 1)
                        found = True
            except OSError:
                continue
    if found:
        marker.write_text('asked once for the icon to sit by the clock\n', encoding='utf-8')
    return found


class Tray(threading.Thread):
    """The small teal icon by the clock. It never takes the keyboard by itself; a click on it opens its menu."""
    CALL, CLOSE, TIMER = WM_APP + 10, 0x0010, 0x0113

    def __init__(self, keys, events):
        super().__init__(daemon=True)
        self.keys, self.events = keys, events        # keys() -> (the keys to press now, another app has Ctrl+Win)
        self.hwnd, self.icon, self.shown, self.tries = None, None, False, 0
        self.ready = threading.Event()

    def _data(self):
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd, data.uID = self.hwnd, 1
        data.uFlags = 1 | 2 | 4                       # message, icon, tip
        data.uCallbackMessage = self.CALL
        data.hIcon = self.icon
        data.szTip = icon_tip(self.keys()[0])
        return data

    def _add(self):
        self.shown = bool(shell32.Shell_NotifyIconW(0, ctypes.byref(self._data())))
        log.info('clock icon shown=%s', self.shown)

    def retip(self):
        """Say the keys that work now. Safe from any thread."""
        if self.shown:
            shell32.Shell_NotifyIconW(1, ctypes.byref(self._data()))

    def _remove(self):
        if self.shown:
            shell32.Shell_NotifyIconW(2, ctypes.byref(self._data()))
            self.shown = False

    def _menu(self):
        menu = user32.CreatePopupMenu()
        lines = icon_menu(*self.keys())
        for number, (words, action) in enumerate(lines, 1):
            if not words:
                user32.AppendMenuW(menu, 0x800, 0, None)
            else:
                user32.AppendMenuW(menu, 0 if action else 2, number, words)   # 2: a plain line, not a choice
        at = wt.POINT()
        user32.GetCursorPos(ctypes.byref(at))
        user32.SetForegroundWindow(self.hwnd)        # so a click elsewhere closes the menu
        chosen = user32.TrackPopupMenu(menu, 0x0002 | 0x0100 | 0x0080 | 0x0020, at.x, at.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)
        if chosen and lines[chosen - 1][1] == 'quit':
            self.events.put(('quit', None))

    def _proc(self, hwnd, message, wparam, lparam):
        try:
            if message == self.CALL and (lparam & 0xFFFF) in (0x0202, 0x0205):    # left or right button let go
                self._menu()
                return 0
            if message == self.created:              # the taskbar started again: put the icon back
                self._add()
                return 0
            if message == self.TIMER:
                self.tries += 1
                moved = keep_in_view(LOCAL / 'icon-placed')
                if moved or self.tries >= 4:
                    user32.KillTimer(hwnd, 1)
                if moved:
                    self._remove()
                    self._add()
                    log.info('clock icon asked to sit beside the clock')
                return 0
            if message == self.CLOSE:
                self._remove()
                user32.DestroyWindow(hwnd)
                return 0
            if message == 0x0002:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            log.info('clock icon: %s', traceback.format_exc())
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def run(self):
        try:
            LOCAL.mkdir(parents=True, exist_ok=True)
            path = LOCAL / 'icon.ico'
            light = taskbar_is_light()
            data = icon_file(teal=ICON_TEAL_LIGHT if light else ICON_TEAL)
            log.info('clock icon drawn for a %s taskbar', 'light' if light else 'dark')
            if not path.exists() or path.read_bytes() != data:
                path.write_bytes(data)
            size = user32.GetSystemMetrics(49) or 16
            self.icon = user32.LoadImageW(None, str(path), 1, size, size, 0x0010)
            self.proc = WNDPROC(self._proc)
            self.created = user32.RegisterWindowMessageW('TaskbarCreated')
            kind = WNDCLASSW()
            kind.lpfnWndProc, kind.hInstance = self.proc, kernel32.GetModuleHandleW(None)
            kind.lpszClassName = 'ScrivoClockIcon'
            user32.RegisterClassW(ctypes.byref(kind))
            # Never shown: it only receives the icon's clicks and the taskbar's news.
            self.hwnd = user32.CreateWindowExW(0x00000080, kind.lpszClassName, NAME, 0x80000000, 0, 0, 0, 0,
                                               None, None, kind.hInstance, None)
            if not self.hwnd or not self.icon:
                log.info('clock icon could not be made: window=%s picture=%s', bool(self.hwnd), bool(self.icon))
                return
            self._add()
            user32.SetTimer(self.hwnd, 1, 3000, None)
        except Exception:
            log.info('clock icon: %s', traceback.format_exc())
            return
        finally:
            self.ready.set()
        message = wt.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        if self.icon:
            user32.DestroyIcon(self.icon)

    def close(self):
        """Take the icon away and end. Safe to call from another thread, and twice."""
        if self.hwnd and self.is_alive():
            user32.PostMessageW(self.hwnd, self.CLOSE, 0, 0)
            self.join(1.5)


# ---- The speaking bar ----------------------------------------------------
CLEAR = '#020403'            # drawn as see-through
PILL, EDGE = '#0E1B19', '#24423C'
TEAL, TEAL_DEEP = '#7BD7BD', '#12A594'
SOFT, WORDS, AMBER = '#1D302C', '#E4F2EC', '#F2C879'
BARS = 21


class Bar:
    def __init__(self, root, on_cancel, on_finish):
        self.root, self.on_cancel, self.on_finish = root, on_cancel, on_finish
        try:
            self.s = max(1.0, user32.GetDpiForSystem() / 96.0)
        except Exception:
            self.s = 1.0
        s = self.s
        self.w, self.pill_h, self.text_h = int(308 * s), int(52 * s), int(88 * s)
        self.h = self.pill_h + self.text_h + int(8 * s)
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.configure(bg=CLEAR)
        self.win.attributes('-topmost', True)
        try:
            self.win.attributes('-transparentcolor', CLEAR)
        except tk.TclError:
            pass
        self.c = tk.Canvas(self.win, width=self.w, height=self.h, bg=CLEAR, highlightthickness=0, bd=0)
        self.c.pack()
        top = self.h - self.pill_h
        mid = top + self.pill_h // 2
        r = int(17 * s)
        self.box = self._round(0, 0, self.w, self.text_h, int(14 * s), fill=PILL, outline=EDGE, state='hidden')
        self.small = tkfont.Font(root=root, family='Segoe UI', size=9)
        self.text = self.c.create_text(int(14 * s), self.text_h - int(10 * s), anchor='sw', fill=WORDS,
                                       font=self.small, justify='left', text='', state='hidden')
        self._round(0, top, self.w, self.h - 1, self.pill_h // 2, fill=PILL, outline=EDGE)
        self.x_at = (int(9 * s) + r, mid)
        self.ok_at = (self.w - int(9 * s) - r, mid)
        self.r = r
        self.c.create_oval(self.x_at[0] - r, mid - r, self.x_at[0] + r, mid + r, fill=SOFT, outline='')
        d = int(5 * s)
        for a, b in ((-d, d), (d, -d)):
            self.c.create_line(self.x_at[0] - d, mid + a, self.x_at[0] + d, mid + b, fill=WORDS,
                               width=max(2, int(2 * s)), capstyle='round')
        self.ok = self.c.create_oval(self.ok_at[0] - r, mid - r, self.ok_at[0] + r, mid + r, fill=TEAL_DEEP, outline='')
        self.c.create_line(self.ok_at[0] - int(6 * s), mid, self.ok_at[0] - int(2 * s), mid + int(5 * s),
                           self.ok_at[0] + int(7 * s), mid - int(5 * s), fill='white',
                           width=max(2, int(2.4 * s)), capstyle='round', joinstyle='round')
        left = self.x_at[0] + r + int(14 * s)
        right = self.ok_at[0] - r - int(14 * s)
        gap = (right - left) / (BARS - 1)
        self.mid, self.tall = mid, self.pill_h * 0.30
        self.lines = [self.c.create_line(left + n * gap, mid, left + n * gap, mid, fill=TEAL,
                                         width=max(3, int(3.2 * s)), capstyle='round') for n in range(BARS)]
        self.xs = [left + n * gap for n in range(BARS)]
        self.note = self.c.create_text(self.w // 2, mid, fill=WORDS, font=('Segoe UI', 10), text='', state='hidden')
        self.shown = [0.0] * BARS
        self.meter = dc.Meter(BARS)
        self.levels = collections.deque(maxlen=64)
        self.mode, self.visible, self.phase, self.fading = 'idle', False, 0.0, 0
        self.c.bind('<Button-1>', self._click)

    def _round(self, x1, y1, x2, y2, r, **look):
        points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
                  x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.c.create_polygon(points, smooth=True, **look)

    def _click(self, event):
        for at, action in ((self.x_at, self.on_cancel), (self.ok_at, self.on_finish)):
            if (event.x - at[0]) ** 2 + (event.y - at[1]) ** 2 <= (self.r + 4) ** 2:
                action()

    def _quiet(self):
        """Never take the keyboard away from the text box being dictated into."""
        try:
            hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            style = user32.GetWindowLongPtrW(hwnd, -20)
            user32.SetWindowLongPtrW(hwnd, -20, style | 0x08000000 | 0x00000080 | 0x00000008)
        except Exception:
            pass

    def show(self, anchor=None):
        """Grow in at the tiny bar's spot (its middle-bottom point), or the bottom middle of the screen."""
        if anchor:
            x, y = place((anchor[0], anchor[1]), area_at(*anchor), (self.w, self.h), 0)
        else:
            left, top, right, bottom = work_area()
            x = left + (right - left - self.w) // 2
            y = bottom - self.h - int(18 * self.s)
        self.win.geometry('%dx%d+%d+%d' % (self.w, self.h, x, y))
        self.words('')
        self.say('')
        self.set('listening')
        self.fading += 1
        if not self.visible:
            self.visible = True
            self._alpha(0.3)
            self.win.deiconify()
            self._quiet()
            self.win.attributes('-topmost', True)
            self._fade(self.fading, [0.55, 0.8, 1.0])
            self._tick()

    def _alpha(self, value):
        try:
            self.win.attributes('-alpha', value)
        except tk.TclError:
            pass

    def _fade(self, serial, steps, then=None):
        if serial != self.fading:
            return                   # shown or hidden again meanwhile
        if not steps:
            return then() if then else None
        self._alpha(steps[0])
        self.root.after(15, lambda: self._fade(serial, steps[1:], then))

    def hide(self, then=None):
        """Shrink away quickly, then (if given) bring the tiny bar back."""
        was = self.visible
        self.visible = False
        self.mode = 'idle'
        self.fading += 1

        def gone():
            self.win.withdraw()
            if then:
                then()
        if was:
            self._fade(self.fading, [0.65, 0.3], gone)
        else:
            gone()

    def set(self, mode):
        self.mode = mode
        self.c.itemconfigure(self.ok, fill=TEAL_DEEP if mode == 'listening' else SOFT)

    def words(self, text):
        # The newest five lines, broken where the words really reach the edge.
        room, rows, row = self.w - int(28 * self.s), [], ''
        for word in text.split():
            longer = (row + ' ' + word).strip()
            if row and self.small.measure(longer) > room:
                rows.append(row)
                row = word
            else:
                row = longer
        shown = '\n'.join((rows + [row] if row else rows)[-5:])
        state = 'normal' if shown else 'hidden'
        self.c.itemconfigure(self.text, text=shown, state=state)
        self.c.itemconfigure(self.box, state=state)

    def say(self, text, colour=WORDS):
        """A short plain message in place of the sound bars."""
        self.c.itemconfigure(self.note, text=text, fill=colour, state='normal' if text else 'hidden')
        for line in self.lines:
            self.c.itemconfigure(line, state='hidden' if text else 'normal')

    def _tick(self):
        if not self.visible:
            return
        if self.mode == 'listening':
            while self.levels:
                self.meter.push(self.levels.popleft())
            targets = self.meter.bars
        elif self.mode == 'writing':
            self.phase += 0.16
            targets = [0.25 + 0.2 * math.sin(self.phase - n * 0.45) for n in range(BARS)]
        else:
            targets = [0.0] * BARS
        for n, line in enumerate(self.lines):
            now, want = self.shown[n], targets[n]
            now += (want - now) * (0.55 if want > now else 0.18)
            self.shown[n] = now
            half = max(1.0, now * self.tall)
            self.c.coords(line, self.xs[n], self.mid - half, self.xs[n], self.mid + half)
        if self.mode == 'listening':
            self.meter.settle(0.985)
        self.root.after(16, self._tick)


class Pill:
    """The tiny see-through teal bar that always waits at the bottom of the screen.

    Pointing at it makes it a little larger and solid and names the keys. A click
    starts a dictation (tap the check or the keys again to finish); a drag moves it
    anywhere and the spot is kept for next time. It never takes the keyboard, so the
    words still land in the text box that had the cursor.
    """
    REST, LOOK = (44, 9), (68, 18)            # its size at rest and while pointed at, before screen scaling
    GAP = 10                                   # above the taskbar

    def __init__(self, root, on_click, spot_file, names):
        self.root, self.on_click, self.spot_file, self.names = root, on_click, Path(spot_file), names
        try:
            self.s = max(1.0, user32.GetDpiForSystem() / 96.0)
        except Exception:
            self.s = 1.0
        self.w, self.h = int(self.LOOK[0] * self.s), int(self.LOOK[1] * self.s)
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.configure(bg=CLEAR)
        self.win.attributes('-topmost', True)
        try:
            self.win.attributes('-transparentcolor', CLEAR)
        except tk.TclError:
            pass
        self.c = tk.Canvas(self.win, width=self.w, height=self.h, bg=CLEAR, highlightthickness=0, bd=0,
                           cursor='hand2')
        self.c.pack()
        self.tip = tk.Toplevel(root)
        self.tip.withdraw()
        self.tip.overrideredirect(True)
        self.tip.attributes('-topmost', True)
        self.tip_text = tk.Label(self.tip, text='', bg=PILL, fg=WORDS, font=('Segoe UI', 9), padx=10, pady=4,
                                 highlightthickness=1, highlightbackground=EDGE)
        self.tip_text.pack()
        self.visible, self.pointed, self.press, self.dragged, self.tip_due = False, False, None, False, None
        self.spot = self._load()
        self._draw()
        self.c.bind('<Enter>', lambda e: self._point(True))
        self.c.bind('<Leave>', lambda e: self._point(False))
        self.c.bind('<ButtonPress-1>', self._down)
        self.c.bind('<B1-Motion>', self._move)
        self.c.bind('<ButtonRelease-1>', self._up)

    # -- its look ----------------------------------------------------------
    def _draw(self):
        self.c.delete('all')
        rest_w, rest_h = self.REST if not self.pointed else self.LOOK
        w, h = int(rest_w * self.s), int(rest_h * self.s)
        x1, y1 = (self.w - w) // 2, self.h - h

        def capsule(x, y, cw, ch, colour):
            # Round ends and a straight middle: Windows draws these crisply at any size.
            self.c.create_oval(x, y, x + ch, y + ch, fill=colour, outline='')
            self.c.create_oval(x + cw - ch, y, x + cw, y + ch, fill=colour, outline='')
            self.c.create_rectangle(x + ch // 2, y, x + cw - ch // 2, y + ch, fill=colour, outline='')
        capsule(x1, y1, w, h, TEAL_DEEP if self.pointed else EDGE)          # the thin edge
        capsule(x1 + 1, y1 + 1, w - 2, h - 2, PILL)
        # Resting sound bars: small teal dots that say "speak here".
        count = 7 if self.pointed else 5
        gap = max(3, round(w * (0.42 if self.pointed else 0.48) / (count - 1)))
        mid_y = y1 + h // 2
        dot = 2 if self.pointed else 1
        first = self.w // 2 - gap * (count - 1) // 2
        for n in range(count):
            cx = first + n * gap
            self.c.create_rectangle(cx - dot // 2, mid_y - dot // 2, cx - dot // 2 + dot, mid_y - dot // 2 + dot,
                                    fill=TEAL, outline='')
        self._alpha(1.0 if self.pointed else 0.7)

    def _alpha(self, value):
        try:
            self.win.attributes('-alpha', value)
        except tk.TclError:
            pass

    def _point(self, on):
        if self.press and not on:
            return                   # still dragging
        self.pointed = on
        self._draw()
        if self.tip_due:
            self.root.after_cancel(self.tip_due)
            self.tip_due = None
        if on:
            self.tip_due = self.root.after(450, self._show_tip)
        else:
            self.tip.withdraw()

    def _show_tip(self):
        self.tip_due = None
        if not (self.pointed and self.visible) or self.press:
            return
        self.tip_text.configure(text='Click or hold %s to dictate. Drag to move.' % key_names(self.names()))
        self.tip.update_idletasks()
        tw, th = self.tip.winfo_reqwidth(), self.tip.winfo_reqheight()
        x = self.win.winfo_rootx() + self.w // 2 - tw // 2
        y = self.win.winfo_rooty() - th - int(6 * self.s)
        self.tip.geometry('+%d+%d' % (x, y))
        self.tip.deiconify()
        quiet(self.tip)

    # -- where it sits -----------------------------------------------------
    def _load(self):
        try:
            data = json.loads(self.spot_file.read_text(encoding='utf-8'))
            return int(data['x']), int(data['y'])
        except Exception:
            return None

    def _save(self):
        try:
            self.spot_file.parent.mkdir(parents=True, exist_ok=True)
            self.spot_file.write_text(json.dumps({'x': self.spot[0], 'y': self.spot[1]}), encoding='utf-8')
        except Exception:
            log.info('the tiny bar spot was not saved: %s', traceback.format_exc(limit=1))

    def corner(self):
        """Top-left of its window for the saved spot, kept inside today's screens."""
        anchor = self.spot or None
        area = area_at(*anchor) if anchor else work_area_primary()
        return place(anchor, area, (self.w, self.h), int(self.GAP * self.s))

    def anchor(self):
        """Its middle-bottom point: where the speaking bar grows from."""
        x, y = self.corner()
        return x + self.w // 2, y + self.h

    def show(self):
        if self.visible:
            return
        x, y = self.corner()
        self.win.geometry('%dx%d+%d+%d' % (self.w, self.h, x, y))
        self.visible = True
        self.win.deiconify()
        quiet(self.win)
        self.on_top()

    def hide(self):
        if not self.visible:
            return
        self.visible, self.pointed, self.press = False, False, None
        self.tip.withdraw()
        self.win.withdraw()
        self._draw()

    def on_top(self):
        """Stay above other always-on-top windows without taking the keyboard."""
        try:
            hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        except Exception:
            pass

    # -- clicks and drags --------------------------------------------------
    def _down(self, event):
        self.press = (event.x_root, event.y_root, self.win.winfo_rootx(), self.win.winfo_rooty())
        self.dragged = False

    def _move(self, event):
        if not self.press:
            return
        px, py, wx, wy = self.press
        dx, dy = event.x_root - px, event.y_root - py
        if not self.dragged and dx * dx + dy * dy < (5 * self.s) ** 2:
            return
        self.dragged = True
        self.tip.withdraw()
        mid, bottom = wx + dx + self.w // 2, wy + dy + self.h
        x, y = place((mid, bottom), area_at(event.x_root, event.y_root), (self.w, self.h), 0)
        self.win.geometry('+%d+%d' % (x, y))

    def _up(self, event):
        press, self.press = self.press, None
        if not press:
            return
        if self.dragged:
            self.spot = (self.win.winfo_rootx() + self.w // 2, self.win.winfo_rooty() + self.h)
            self._save()
            log.info('tiny bar moved')
            px, py = self.win.winfo_pointerxy()
            x, y = self.win.winfo_rootx(), self.win.winfo_rooty()
            if not (x <= px < x + self.w and y <= py < y + self.h):
                self._point(False)
        else:
            self.tip.withdraw()
            self.on_click()


def quiet(win):
    """Never take the keyboard away from the text box being dictated into."""
    try:
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        style = user32.GetWindowLongPtrW(hwnd, -20)
        user32.SetWindowLongPtrW(hwnd, -20, style | 0x08000000 | 0x00000080 | 0x00000008)
    except Exception:
        pass


def work_area_primary():
    """The usable part of the main screen: the tiny bar's first home."""
    return area_at(user32.GetSystemMetrics(0) // 2, user32.GetSystemMetrics(1) // 2)


class App:
    def __init__(self, config, mic=None):
        self.config = config
        self.root = tk.Tk()
        self.root.withdraw()
        self.events = queue.Queue()
        self.keys = Keys(config.get('shortcuts') or SHORTCUTS, self.events)
        self.bar = Bar(self.root, lambda: self.events.put(('escape', None)),
                       lambda: self.events.put(('check', None)))
        self.pill = Pill(self.root, lambda: self.events.put(('click', None)), LOCAL / 'bar-spot.json',
                         lambda: self.keys.names())
        self.make_mic = mic or (lambda sink, failed: Mic(sink, failed))
        self.state, self.piece, self.mic = 'idle', None, None
        self.began, self.mode, self.serial = 0.0, None, 0
        self.source, self.press = 'hotkey', 0         # what started this piece, and which Ctrl+Win press
        self.pasted = None          # for the self-check: called after each paste
        self.tray, self.ticks = None, 0
        self.other_open, self.full, self.watched = None, False, 0
        self.timing = {}            # how quickly the last piece started listening, in seconds
        self.sounds = config.get('sounds', True)
        self.show_bar = config.get('bar', True)

    def run(self):
        self._other_check()
        self.keys.start()
        self.keys.ready.wait(5)
        if not self.keys.combo and not self.keys.watching:
            log.info('no shortcut was free: %s', ', '.join(self.keys.choices))
            return 2
        log.info('ready shortcut=%s keys=%s other_app_open=%s microphones=%s', self.keys.combo or 'none',
                 self.keys.names(), self.other_open, winmm.waveInGetNumDevs())
        self.tray = Tray(lambda: (self.keys.names(), bool(self.other_open and self.keys.watching)), self.events)
        self.tray.start()
        threading.Thread(target=self._home, daemon=True).start()
        self.root.after(10, self._poll)
        self.root.after(50, self._watch)
        self.root.mainloop()
        return 0

    def _other_check(self):
        """Ctrl+Windows is ours only while the other dictation app is closed, so words are never typed twice."""
        try:
            now = running(OTHER_APP)
        except Exception:
            now = True                 # unsure: leave the keys to the other app
        if now != self.other_open:
            self.other_open = now
            self.keys.combo_on = not now
            log.info('other app open=%s: %s is %s', now, COMBO, 'left to the other app' if now else 'ours')
            if self.tray:
                self.tray.retip()

    def _watch(self):
        """Twice a second: is the other dictation app open, is something full-screen, should the tiny bar show."""
        try:
            self.watched += 1
            if self.watched % 4 == 0:
                self._other_check()
            self.full = full_screen()
            self._pill_now()
            if self.watched % 4 == 0 and self.pill.visible:
                self.pill.on_top()
        except Exception:
            log.info('watch: %s', traceback.format_exc(limit=2))
        self.root.after(500, self._watch)

    def _pill_now(self):
        want = (self.show_bar and self.keys.combo_on and self.keys.watching and not self.full
                and self.state == 'idle')
        if want != self.pill.visible:
            log.info('tiny bar %s (other app open=%s, full screen=%s, %s)', 'shown' if want else 'hidden',
                     self.other_open, self.full, self.state)
        if want:
            self.pill.show()
        else:
            self.pill.hide()

    def close(self, why):
        """End the program tidily: nothing half spoken left running, no icon left behind."""
        if self.state in ('listening', 'writing'):
            self.cancel()
        if self.tray:
            self.tray.close()
        log.info('closed: %s', why)
        self.root.destroy()

    def _home(self):
        try:
            status, body = dc.ask(self.config['endpoint'], self.config['token'], 'GET', '/v1/health', timeout=10)
            log.info('server answered: %s %s', status, (body or {}).get('status'))
        except Exception as error:
            log.info('server did not answer at start: %s', type(error).__name__)

    # -- one piece -------------------------------------------------------
    def start(self, source='hotkey', press=0, pressed=None):
        self.serial += 1
        serial = self.serial
        self.state, self.began, self.mode = 'listening', time.monotonic(), None
        self.source, self.press = source, press
        if source == 'click':
            self.mode = 'tap'            # a click starts hands-free: the check or the keys finish
        pressed = pressed or self.began
        self.timing = {'keys_to_start': round(self.began - pressed, 3)}
        timing = self.timing
        self.piece = dc.Dictation(
            self.config['endpoint'], self.config['token'], LOCAL / 'recordings',
            on_preview=lambda text: self.events.put(('preview', (serial, text))),
            on_done=lambda result: self.events.put(('done', (serial, result)))).start()
        piece, levels = self.piece, self.bar.levels

        def sink(pcm):
            if 'keys_to_microphone' not in timing:
                timing['keys_to_microphone'] = round(time.monotonic() - pressed, 3)
            levels.append(dc.level(pcm))
            if not piece.feed(pcm):
                self.events.put(('check', None))
        self.mic = self.make_mic(sink, lambda: self.events.put(('nomic', serial)))
        self.mic.start()
        self.keys.escape(True)
        anchor = self.pill.anchor() if self.pill.visible or self.pill.spot else None
        self.pill.hide()
        self.bar.show(anchor)
        self.timing['keys_to_bar'] = round(time.monotonic() - pressed, 3)
        if self.sounds:
            play('start')
        log.info('listening (%s): bar %.3f s after the keys', source, self.timing['keys_to_bar'])

    def finish(self):
        if self.state != 'listening':
            return
        self.state = 'writing'
        self.mic.stop()
        self.mic.join(1.5)           # the last captured words are handed over first
        self.piece.finish()
        self.bar.set('writing')
        if self.sounds:
            play('finish')
        log.info('finishing after %.1f s (%s, microphone %s s after the keys)', time.monotonic() - self.began,
                 self.mode or 'tap', self.timing.get('keys_to_microphone'))

    def cancel(self, sound=True):
        if self.state not in ('listening', 'writing'):
            return
        self.mic.stop()
        self.piece.cancel()
        self.serial += 1             # a late answer for this piece is ignored
        if sound and self.sounds:
            play('cancel')
        self._rest()
        log.info('cancelled')

    def _rest(self):
        self.state = 'idle'
        self.keys.escape(False)
        self.bar.hide(then=self._pill_now)

    def _message(self, text, seconds, more=''):
        """What went wrong in the bar, and above it what became of the words and what to do next."""
        self.state = 'message'
        self.keys.escape(False)
        self.bar.set('message')
        self.bar.words(more)
        self.bar.say(text, AMBER)
        serial = self.serial
        self.root.after(int(seconds * 1000), lambda: self.serial == serial and self.state == 'message' and self._rest())

    def _done(self, result):
        status = result.get('status')
        text = (result.get('text') or '').strip()
        log.info('answer: %s writer=%s after_finish=%s', status, result.get('writer'), result.get('seconds_after_finish'))
        if status == 'ready' and text:
            self._deliver(text, time.monotonic() + 2.0)
        elif status == 'ready':
            self._message('No words heard', 1.6)
        elif status == 'kept':
            self._message('Your Scrivo server did not answer', 8, 'Nothing was typed. Your recording is saved on this '
                          'computer. Check that your Scrivo server is running, then dictate again.')
        elif status == 'retry':
            self._message('No writing model answered', 8, 'Nothing was typed. Your recording is saved on the server. '
                          'Dictate again in a moment.')
        else:
            self._message('Dictation did not finish', 6, 'Nothing was typed. Dictate again.')

    def _deliver(self, text, give_up):
        # Wait for the fingers to leave the shortcut, or Ctrl+V becomes something else.
        if self.keys.any_down() and time.monotonic() < give_up:
            self.root.after(20, lambda: self._deliver(text, give_up))
            return
        copied = clipboard(text + ' ')
        sent = copied and press((0x11, True), (0x56, True), (0x56, False), (0x11, False))
        log.info('delivery: clipboard=%s paste_sent=%s', bool(copied), bool(sent))
        if not copied:
            self._message('Could not reach the clipboard', 4)
            return
        self._rest()
        if self.pasted:
            self.pasted()

    def _poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == 'shortcut':
                    source, press, pressed = data
                    if self.state in ('idle', 'message'):
                        self.start(source, press, pressed)
                    elif self.state == 'listening' and self.mode != 'hold':
                        self.finish()
                elif kind == 'click':
                    if self.state in ('idle', 'message'):
                        self.start('click')
                elif kind == 'other':
                    # Ctrl+Windows+another key at once: that shortcut was meant, not a dictation.
                    # Later in a long hold a stray key is let through and the words are kept.
                    if (self.state == 'listening' and self.source == 'combo' and self.press == data
                            and self.mode != 'tap' and time.monotonic() - self.began < 0.6):
                        self.cancel(sound=False)
                        log.info('Ctrl+Win joined by another key: left to Windows')
                elif kind in ('check', 'escape') and self.state == 'message':
                    self._rest()             # the X and the check both put a message away
                elif kind == 'check':
                    self.finish()
                elif kind == 'escape':
                    self.cancel() if self.state in ('listening', 'writing') else None
                elif kind == 'preview' and data[0] == self.serial and self.state == 'listening':
                    self.bar.words(data[1])
                elif kind == 'done' and data[0] == self.serial and self.state == 'writing':
                    self._done(data[1])
                elif kind == 'nomic' and data == self.serial and self.state == 'listening':
                    self.piece.cancel()
                    self.serial += 1
                    self._message('The microphone is not available', 8,
                                  'Check that a microphone is plugged in and switched on, then dictate again.')
                elif kind == 'call':
                    data()
                elif kind == 'quit':
                    return self.close('Quit was chosen at the clock icon')
        except queue.Empty:
            pass
        self.ticks += 1
        if self.ticks % 100 == 0 and self.state == 'idle' and (LOCAL / 'quit').exists():
            # The installer asks the running copy to leave tidily before it starts the new one.
            (LOCAL / 'quit').unlink(missing_ok=True)
            return self.close('the installer asked')
        if self.state == 'listening':
            held = time.monotonic() - self.began
            if self.mode is None:
                if not self.keys.down(self.source):
                    self.mode = 'tap'
                elif held >= HOLD:
                    self.mode = 'hold'
            elif self.mode == 'hold' and key_down(0x1B):
                self.cancel()                # Esc while the keys are still held: Windows does not report it as a shortcut
            elif self.mode == 'hold' and not self.keys.down(self.source):
                self.finish()
        self.root.after(10, self._poll)


# ---- Starting, and the checks the installer runs -------------------------
def setup_log():
    LOCAL.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOCAL / 'log.txt', maxBytes=200_000, backupCount=1, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def only_one():
    handle = kernel32.CreateMutexW(None, False, 'Local\\ScrivoSingleInstance')
    return handle and ctypes.get_last_error() != 183


def take_focus(hwnd):
    if user32.SetForegroundWindow(hwnd) and user32.GetForegroundWindow() == hwnd:
        return True
    other = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    mine = kernel32.GetCurrentThreadId()
    if other and other != mine:
        user32.AttachThreadInput(mine, other, True)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(mine, other, False)
    return user32.GetForegroundWindow() == hwnd


def self_check(config, sample, out, expected):
    """The whole path on this desktop, only while nobody is using it.

    Opens a small text box of its own, presses the real shortcut, plays a saved
    made-up sentence in place of the microphone, presses the shortcut again and
    reads what landed in its own box. The clipboard and the window that was in
    front are put back. Nothing of the user's is typed into, read or changed.
    """
    result = {'ran': False, 'idle_seconds': round(idle_seconds()), 'desktop_open': desktop_open()}

    def save():
        Path(out).write_text(json.dumps(result, indent=2), encoding='utf-8')
    if not result['desktop_open']:
        result['why'] = 'the screen is locked'
        return save()
    if result['idle_seconds'] < 300:
        result['why'] = 'the computer is in use'
        return save()
    # The microphone itself: half a second, loudness only, nothing kept.
    heard, first = [], []
    opened = time.monotonic()

    def hear(pcm):
        first or first.append(time.monotonic() - opened)
        heard.append(pcm)
    probe = Mic(hear, lambda: heard.append(None))
    probe.start()
    time.sleep(0.6)
    probe.stop()
    probe.join(2)
    result['microphone_opened'] = bool(heard) and None not in heard
    result['microphone_pieces'] = len([h for h in heard if h])
    result['microphone_first_sound_seconds'] = round(first[0], 3) if first else None

    before_clip = clipboard()
    before_front = user32.GetForegroundWindow()
    app = App(config, mic=lambda sink, failed: FileMic(sink, failed, sample, lambda: app.events.put(('call', spoken))))
    box = tk.Toplevel(app.root)
    box.title('Scrivo check')
    box.geometry('320x60-40-80')
    entry = tk.Entry(box, font=('Segoe UI', 11))
    entry.pack(fill='both', expand=True, padx=8, pady=8)
    marks = {}

    def tap():
        if app.keys.combo_on and app.keys.watching:
            # Ctrl+Windows as fingers press it: both down, a short moment, both up.
            press((0xA2, True), (0x5B, True))
            app.root.after(80, lambda: press((0x5B, False), (0xA2, False)))
            return
        mods = [VKS[p] for p in app.keys.combo.split('+')[:-1]]
        key = shortcut(app.keys.combo)[1]
        press(*[(m, True) for m in mods], (key, True), (key, False), *[(m, False) for m in reversed(mods)])

    def begin():
        box.update()
        hwnd = user32.GetParent(box.winfo_id()) or box.winfo_id()
        result['took_focus'] = bool(take_focus(hwnd))
        entry.focus_force()
        if not result['took_focus']:
            result['why'] = 'another window kept the keyboard'
            return end()
        result['ran'] = True
        result['shortcut'] = app.keys.names()
        result['other_app_open'] = bool(app.other_open)
        result['tiny_bar_shown_before'] = app.pill.visible
        marks['start'] = time.monotonic()
        tap()
        app.root.after(1500, lambda: result.__setitem__('bar_shown', app.bar.visible) or
                       result.__setitem__('focus_kept_while_bar_shown', user32.GetForegroundWindow() == hwnd) or
                       result.__setitem__('clock_icon_shown', bool(app.tray and app.tray.shown)))
        app.root.after(60000, end)

    def spoken():
        app.root.after(350, lambda: (marks.__setitem__('finish', time.monotonic()), tap()))

    def pasted():
        marks['paste'] = time.monotonic()
        app.root.after(600, end)

    def end():
        if 'ended' in marks:
            return
        marks['ended'] = True
        text = entry.get()
        result['words_landed'] = bool(text.strip())
        result['landed_text'] = text       # the made-up check sentence, never real words
        result['exact'] = text.strip() == expected
        if 'paste' in marks and 'finish' in marks:
            result['seconds_from_finish_to_paste'] = round(marks['paste'] - marks['finish'], 2)
        result['seconds_from_keys'] = dict(app.timing)
        result['tiny_bar_back_after'] = app.pill.visible
        if before_clip is not None:
            clipboard(before_clip)
        result['clipboard_restored'] = before_clip is None or clipboard() == before_clip
        box.destroy()
        if before_front:
            user32.SetForegroundWindow(before_front)
        save()
        app.root.after(100, lambda: app.close('the check ended'))
    app.pasted = pasted
    app.root.after(700, begin)
    code = app.run()
    if code:
        result['why'] = 'no shortcut was free'
        save()


def main(argv):
    _api()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    setup_log()
    try:
        config = dc.load_config(CONFIG)
        job = json.loads(Path(argv[2]).read_text(encoding='utf-8-sig')) if len(argv) > 2 else {}
        if argv[1:2] == ['--file-check']:
            # No screen, no microphone: a saved made-up sentence through the real sending code.
            result = dc.send_file(config, job['sample'], LOCAL / 'recordings')
            status, body = dc.ask(config['endpoint'], config['token'], 'GET', '/v1/dictionary')
            entries = (body or {}).get('entries') or []
            result['dictionary_entries'] = len(entries) if status == 200 else None
            result['dictionary_has'] = {word: any(e.get('phrase') == word for e in entries)
                                        for word in job.get('look_up') or []}
            status, body = dc.ask(config['endpoint'], config['token'], 'GET', '/v1/models')
            result['writing_models'] = [slot.get('label') for slot in (body or {}).get('slots') or []]
            Path(job['out']).write_text(json.dumps(result, indent=2), encoding='utf-8')
            return 0
        if not only_one():
            log.info('already running')
            return 0
        (LOCAL / 'quit').unlink(missing_ok=True)      # an old request to leave is not for this copy
        if argv[1:2] == ['--self-check']:
            self_check(config, job['sample'], job['out'], job['expected'])
            return 0
        return App(config).run()
    except Exception:
        log.info('stopped: %s', traceback.format_exc())
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
