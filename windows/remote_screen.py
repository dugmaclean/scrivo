"""Dictation while a remote screen (a game-streaming app such as Moonlight) is in front.

Moonlight, set to "capture system keys: always", puts a keyboard watch of its own
at the head of Windows' list while its window is in front and keeps Ctrl and the
Windows key from everything else, so the dictation program would never see
Ctrl+Windows. Windows' raw keyboard reports and Windows' own idea of which keys
are down miss those keys too.

Two things fix it, both only while such a window is in front:

- A fresh watch of ours goes in front of Moonlight's a moment after Moonlight
  comes to the front (Moonlight puts its own in each time it is chosen), and again
  every two seconds while it stays there. Ours passes every key on, so Moonlight
  still gets them; ours only notices Ctrl+Windows.
- The finished words are typed as key presses instead of pasted: Moonlight sends
  key presses to the remote computer, but not this computer's clipboard, so Ctrl+V
  would paste the remote computer's own clipboard. The words still go on this
  computer's clipboard as well.

Typing follows the US keyboard, which is how the remote computer reads the keys
Moonlight sends. Curly quotes, dashes and the like become their plain forms;
anything else that has no key is left out and counted.
"""
import ctypes
import time
import unicodedata

REMOTE = {'moonlight.exe': 'Moonlight'}       # take Ctrl and Windows for the far computer; no shared clipboard
# The keys Moonlight's own watch takes while it is in front (Windows keys, Alt, Ctrl, Print Screen,
# Tab, Esc): Windows' own key state never sees them, so only our watch knows whether they are held.
CAPTURED = frozenset({0x5B, 0x5C, 0xA4, 0xA5, 0xA2, 0xA3, 0x2C, 0x09, 0x1B})

SHIFT, ENTER, TAB = 0xA0, 0x0D, 0x09
_KEYS = {' ': (0x20, False), '\n': (ENTER, False), '\t': (TAB, False)}
for _c in range(26):
    _KEYS[chr(97 + _c)] = (0x41 + _c, False)
    _KEYS[chr(65 + _c)] = (0x41 + _c, True)
for _n, (_plain, _shifted) in enumerate(zip('0123456789', ')!@#$%^&*(')):
    _KEYS[_plain], _KEYS[_shifted] = (0x30 + _n, False), (0x30 + _n, True)
for _vk, _plain, _shifted in ((0xBA, ';', ':'), (0xBB, '=', '+'), (0xBC, ',', '<'), (0xBD, '-', '_'),
                              (0xBE, '.', '>'), (0xBF, '/', '?'), (0xC0, '`', '~'), (0xDB, '[', '{'),
                              (0xDC, '\\', '|'), (0xDD, ']', '}'), (0xDE, "'", '"')):
    _KEYS[_plain], _KEYS[_shifted] = (_vk, False), (_vk, True)

# Scan codes of the US keyboard. Moonlight reads the scan code, not the key name, so a
# typed key without one is dropped on the way to the remote computer.
SCAN = {0x20: 0x39, ENTER: 0x1C, TAB: 0x0F, SHIFT: 0x2A, 0xBA: 0x27, 0xBB: 0x0D, 0xBC: 0x33, 0xBD: 0x0C,
        0xBE: 0x34, 0xBF: 0x35, 0xC0: 0x29, 0xDB: 0x1A, 0xDC: 0x2B, 0xDD: 0x1B, 0xDE: 0x28, 0xA2: 0x1D, 0x5B: 0x5B}
SCAN.update(zip(range(0x30, 0x3A), (0x0B, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A)))
SCAN.update(zip(range(0x41, 0x5B), (0x1E, 0x30, 0x2E, 0x20, 0x12, 0x21, 0x22, 0x23, 0x17, 0x24, 0x25, 0x26, 0x32,
                                    0x31, 0x18, 0x19, 0x10, 0x13, 0x1F, 0x14, 0x16, 0x2F, 0x11, 0x2D, 0x15, 0x2C)))
EXTENDED = {0x5B}                             # the Windows key's scan code is one of the extended ones

_SAME = {'‘': "'", '’': "'", '‚': "'", '‛': "'", '′': "'", '“': '"', '”': '"',
         '„': '"', '″': '"', '–': '-', '—': '-', '―': '-', '−': '-', '‐': '-',
         '‑': '-', '…': '...', ' ': ' ', ' ': ' ', ' ': ' ', ' ': ' ', ' ': ' ',
         '\r\n': '\n', '\r': '\n', '×': 'x', '•': '-'}


def plain(text):
    """The words as the keyboard can type them: (typable text, how many characters had no key)."""
    for old, new in _SAME.items():
        text = text.replace(old, new)
    out, skipped = [], 0
    for ch in text:
        if ch in _KEYS:
            out.append(ch)
            continue
        bare = ''.join(c for c in unicodedata.normalize('NFKD', ch) if not unicodedata.combining(c))
        if bare and all(c in _KEYS for c in bare):
            out.append(bare)                  # é -> e, ﬁ -> fi
        else:
            skipped += 1
    return ''.join(out), skipped


def keystrokes(text):
    """Key presses that type `text`: a list of (key, scan code, up) in order. Shift only around capitals."""
    events, shifted = [], False
    for ch in plain(text)[0]:
        vk, shift = _KEYS[ch]
        if shift != shifted:
            events.append((SHIFT, SCAN[SHIFT], not shift))
            shifted = shift
        events += [(vk, SCAN[vk], False), (vk, SCAN[vk], True)]
    if shifted:
        events.append((SHIFT, SCAN[SHIFT], True))
    return events


class HeadWatch:
    """When our keyboard watch must be put (again) in front of the remote screen's own.

    `tick` is called a few times a second with whether a remote screen is in front. It
    answers 'install' a moment after one comes to the front and every few seconds after,
    'remove' once none is in front, otherwise None.
    """
    SETTLE, AGAIN = 0.3, 2.0

    def __init__(self):
        self.since, self.last, self.on = None, None, False

    def tick(self, now, remote):
        if not remote:
            self.since = None
            if self.on:
                self.on = False
                return 'remove'
            return None
        if self.since is None:
            self.since = now
        if now - self.since < self.SETTLE:
            return None
        if not self.on or now - self.last >= self.AGAIN:
            self.on, self.last = True, now
            return 'install'
        return None


# ---- Windows only from here ----------------------------------------------
_names = {}


def window_program(hwnd):
    """The program file name (lower case) of the window, or ''. Remembered per window."""
    if not hwnd:
        return ''
    if hwnd in _names:
        return _names[hwnd]
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
    name = ''
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x1000, False, pid.value)      # read its name only
    if handle:
        buf, size = ctypes.create_unicode_buffer(520), ctypes.c_ulong(520)
        if kernel32.QueryFullProcessImageNameW(ctypes.c_void_p(handle), 0, buf, ctypes.byref(size)):
            name = buf.value.rsplit('\\', 1)[-1].lower()
        kernel32.CloseHandle(ctypes.c_void_p(handle))
    if len(_names) > 256:
        _names.clear()
    _names[hwnd] = name
    return name


def remote_in_front():
    """The remote screen program in front ('Moonlight'), or None."""
    return REMOTE.get(window_program(ctypes.windll.user32.GetForegroundWindow()))


def send(events, mine, INPUT, KEYBDINPUT):
    """Send key presses with their scan codes, marked as ours so our own watch ignores them."""
    batch = (INPUT * len(events))()
    for item, (vk, scan, up) in zip(batch, events):
        item.type = 1
        item.u.ki = KEYBDINPUT(vk, scan, (2 if up else 0) | (1 if vk in EXTENDED else 0), 0, mine)
    return ctypes.windll.user32.SendInput(len(events), batch, ctypes.sizeof(INPUT)) == len(events)


def type_into(text, mine, INPUT, KEYBDINPUT, pause=0.004, still_front=remote_in_front):
    """Type the words key by key into the remote screen. Stops if it leaves the front.

    Returns (characters typed, characters with no key, finished).
    """
    words, skipped = plain(text)
    typed = 0
    for ch in words:
        if not still_front():
            return typed, skipped, False
        send(keystrokes(ch), mine, INPUT, KEYBDINPUT)     # Shift, if any, is let go inside each one
        typed += 1
        time.sleep(pause)
    return typed, skipped, True
