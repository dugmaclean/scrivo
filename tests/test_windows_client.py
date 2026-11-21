"""The computer client's sending code against a stand-in server on this machine.

No real service, credential, microphone or screen is used; the listener is
loopback-only and closed before the test ends.
"""
import http.server
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'windows'))
import dictation_client as dc

KEY = 'k' * 40


class Home(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    mode = 'ok'
    seen = []

    def log_message(self, *args):
        pass

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        if self.headers.get('Transfer-Encoding') == 'chunked':
            data = b''
            while True:
                size = int(self.rfile.readline().strip(), 16)
                piece = self.rfile.read(size)
                self.rfile.readline()
                if not size:
                    return data
                data += piece
        return self.rfile.read(int(self.headers.get('Content-Length') or 0))

    def do_GET(self):
        if self.headers.get('Authorization') != 'Bearer ' + KEY:
            return self.reply(401, {})
        if self.path.startswith('/v1/streams/'):
            return self.reply(200, {'text': 'words while speaking', 'final': False})
        self.reply(404, {})

    def do_POST(self):
        if self.headers.get('Authorization') != 'Bearer ' + KEY:
            return self.reply(401, {})
        try:
            data = self.body()
        except ValueError:
            return        # the client hung up mid-piece (a cancel)
        Home.seen.append((self.path.split('?')[0], len(data), self.path))
        if self.path.startswith('/v1/stream') and Home.mode == 'stream-down':
            self.close_connection = True
            return self.connection.close()
        if Home.mode == 'all-down':
            return self.reply(500, {})
        if Home.mode == 'no-model':
            return self.reply(503, {'id': 'abc', 'status': 'retry', 'recording_saved': True})
        self.reply(200, {'id': 'abc', 'text': 'Final words.', 'writer': 'home'})


class ClientTest(unittest.TestCase):
    def setUp(self):
        Home.mode, Home.seen = 'ok', []
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Home)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.endpoint = 'http://127.0.0.1:%d' % self.server.server_address[1]
        self.scratch = tempfile.TemporaryDirectory()
        self.keep = Path(self.scratch.name) / 'recordings'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.scratch.cleanup()

    def speak(self, pieces=20, cancel=False):
        done, box, heard = threading.Event(), {}, []
        piece = dc.Dictation(self.endpoint, KEY, self.keep, heard.append,
                             lambda result: (box.update(result), done.set())).start()
        for _ in range(pieces):
            piece.feed(b'\x10\x00' * (dc.CHUNK // 2))
            time.sleep(0.02)
        piece.cancel() if cancel else piece.finish()
        self.assertTrue(done.wait(10))
        return piece, box, heard

    def test_words_come_back_and_were_sent_while_speaking(self):
        piece, box, heard = self.speak()
        self.assertEqual((box['status'], box['text'], box['writer']), ('ready', 'Final words.', 'home'))
        path, size, full = Home.seen[0]
        self.assertEqual((path, size), ('/v1/stream', 20 * dc.CHUNK))
        self.assertIn('writer=saved', full)                 # the saved model picks, shared with the phone
        self.assertRegex(full, r'session=[0-9a-f]{32}$')
        self.assertIn('words while speaking', heard)
        self.assertFalse(self.keep.exists() and list(self.keep.iterdir()))

    def test_stream_failure_sends_the_whole_recording_once(self):
        Home.mode = 'stream-down'
        piece, box, heard = self.speak()
        self.assertEqual(box['status'], 'ready')
        self.assertEqual([p for p, _, _ in Home.seen][-1], '/v1/dictate')
        self.assertEqual(Home.seen[-1][1], 20 * dc.CHUNK + 44)   # a complete sound file
        self.assertFalse(list(self.keep.glob('*.wav')))          # home has it; no copy left here

    def test_home_unreachable_keeps_the_recording_here(self):
        Home.mode = 'all-down'
        piece, box, heard = self.speak()
        self.assertEqual(box['status'], 'kept')
        kept = list(self.keep.glob('recording-*.wav'))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].stat().st_size, 20 * dc.CHUNK + 44)

    def test_no_model_answering_reports_recording_kept_at_home(self):
        Home.mode = 'no-model'
        piece, box, heard = self.speak()
        self.assertEqual(box['status'], 'retry')

    def test_cancel_returns_nothing_and_keeps_nothing(self):
        piece, box, heard = self.speak(cancel=True)
        self.assertEqual(box, {'status': 'cancelled'})
        self.assertFalse(self.keep.exists() and list(self.keep.iterdir()))

    def test_wrong_key_is_not_a_result(self):
        done, box = threading.Event(), {}
        piece = dc.Dictation(self.endpoint, 'x' * 40, self.keep, None, lambda r: (box.update(r), done.set())).start()
        piece.feed(b'\x10\x00' * 800)
        piece.finish()
        self.assertTrue(done.wait(10))
        self.assertEqual(box['status'], 'kept')

    def test_recording_has_no_length_limit(self):
        piece = dc.Dictation(self.endpoint, KEY, self.keep)
        piece.pcm = bytearray(dc.RATE * 2 * 600)
        self.assertTrue(piece.feed(b'\0' * 100))

    def test_only_ten_recordings_are_kept(self):
        self.keep.mkdir()
        for n in range(14):
            (self.keep / ('recording-20260101-0000%02d.wav' % n)).write_bytes(b'x')
        piece = dc.Dictation(self.endpoint, KEY, self.keep)
        piece.pcm = bytearray(b'\1\0' * 100)
        piece.keep()
        self.assertEqual(len(list(self.keep.glob('*.wav'))), dc.KEEP)

    def test_config_refuses_an_open_address_or_a_missing_key(self):
        path = Path(self.scratch.name) / 'config.json'
        path.write_text(json.dumps({'endpoint': 'http://example.com', 'token': KEY}))
        self.assertRaises(ValueError, dc.load_config, path)
        path.write_text(json.dumps({'endpoint': 'https://example.com', 'token': 'short'}))
        self.assertRaises(ValueError, dc.load_config, path)
        path.write_text(json.dumps({'endpoint': 'https://example.com/', 'token': KEY}))
        self.assertEqual(dc.load_config(path)['endpoint'], 'https://example.com')

    def test_config_saved_by_notepad_with_a_byte_order_mark_loads(self):
        path = Path(self.scratch.name) / 'config.json'
        path.write_bytes(b'\xef\xbb\xbf' + json.dumps({'endpoint': 'https://example.com', 'token': KEY}).encode())
        self.assertEqual(dc.load_config(path)['token'], KEY)

    def test_live_words_show_the_newest_whole_words(self):
        text = ' '.join('word%d' % n for n in range(100))
        shown = dc.tail(text, 5, 50)
        self.assertEqual(len(shown.split('\n')), 5)
        self.assertTrue(shown.endswith('word99'))
        self.assertTrue(all(len(row) <= 50 for row in shown.split('\n')))

    def test_sound_bars_rise_with_a_voice_and_rest_in_quiet(self):
        meter = dc.Meter(5)
        for _ in range(30):
            meter.push(0.01)
        self.assertLess(max(meter.bars), 0.2)
        meter.push(0.5)
        self.assertGreater(meter.bars[-1], 0.8)
        self.assertGreater(dc.level(b'\x00\x20' * 400), dc.level(b'\x10\x00' * 400))


def program():
    """The Windows program's own code, loaded here with Windows' interfaces stood in for."""
    import ctypes
    import importlib.machinery
    import importlib.util
    from unittest import mock
    path = str(Path(__file__).resolve().parent.parent / 'windows' / 'scrivo.pyw')
    with mock.patch.object(ctypes, 'WinDLL', lambda *a, **k: mock.MagicMock(), create=True):
        loader = importlib.machinery.SourceFileLoader('scrivo_program', path)
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
    return module


class ClockIcon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pd = program()

    def test_pointing_at_the_icon_names_the_keys_as_printed(self):
        tip = self.pd.icon_tip('ctrl+alt+space')
        self.assertIn('Ctrl+Alt+Space', tip)
        self.assertTrue(tip.startswith('Scrivo'))
        self.assertLess(len(tip), 128)                 # Windows cuts a longer one short
        self.assertEqual(self.pd.key_names('ctrl+alt+d'), 'Ctrl+Alt+D')
        self.assertLess(len(self.pd.icon_tip('ctrl+alt+shift+win+space' * 9)), 128)

    def test_the_menu_says_how_to_dictate_and_only_quit_does_anything(self):
        lines = self.pd.icon_menu('ctrl+alt+d')
        words = [w for w, _ in lines]
        self.assertTrue(any('Hold Ctrl+Alt+D' in w for w in words))
        self.assertTrue(any('tap Ctrl+Alt+D' in w for w in words))
        self.assertTrue(any('Esc' in w for w in words))
        self.assertEqual([(w, a) for w, a in lines if a], [(self.pd.QUIT, 'quit')])
        self.assertIn('Quit dictation', self.pd.QUIT)
        self.assertEqual(lines[-1][1], 'quit')         # Quit is last, under a divider
        self.assertEqual(lines[-2], ('', None))

    def test_the_icon_is_a_teal_disc_with_white_sound_bars_and_clear_corners(self):
        for size in (16, 32):
            rows = self.pd.icon_pixels(size)
            self.assertEqual((len(rows), len(rows[0])), (size, size))
            self.assertEqual(rows[0][0][3], 0)                           # corner: see-through
            self.assertEqual(rows[size // 2][size // 2][:3], (255, 255, 255))   # the tall middle bar
            edge = rows[size // 2][1]
            self.assertEqual(edge[:3], self.pd.ICON_TEAL)                 # the disc itself
            self.assertGreater(edge[3], 200)
            # between the bars the teal shows through, so they read as three bars, not a blob
            between = rows[size // 2][round(size * 0.40)]
            self.assertLess(between[0], 140)
            self.assertEqual(rows, [list(reversed(row)) for row in rows])  # the same left and right

    def test_a_light_taskbar_gets_the_deeper_teal(self):
        def light(c):
            c = [v / 255 for v in c]
            c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

        def against(a, b):
            hi, lo = sorted((light(a), light(b)), reverse=True)
            return (hi + 0.05) / (lo + 0.05)
        self.assertGreaterEqual(against(self.pd.ICON_TEAL, (0x1C, 0x20, 0x24)), 3)        # dark taskbar
        self.assertGreaterEqual(against(self.pd.ICON_TEAL_LIGHT, (0xE9, 0xEE, 0xF2)), 3)  # light taskbar
        self.assertGreaterEqual(against((255, 255, 255), self.pd.ICON_TEAL), 3)           # the bars on the disc
        self.assertEqual(self.pd.icon_pixels(16, teal=self.pd.ICON_TEAL_LIGHT)[8][1][:3], self.pd.ICON_TEAL_LIGHT)
        self.assertNotEqual(self.pd.icon_file(teal=self.pd.ICON_TEAL_LIGHT), self.pd.icon_file())
        self.assertFalse(self.pd.taskbar_is_light())      # unknown here: Windows' own default, dark

    def test_one_icon_file_holds_every_taskbar_size(self):
        import struct
        data = self.pd.icon_file()
        reserved, kind, count = struct.unpack_from('<HHH', data)
        self.assertEqual((reserved, kind, count), (0, 1, len(self.pd.ICON_SIZES)))
        end = 6 + 16 * count
        for n, size in enumerate(self.pd.ICON_SIZES):
            w, h, _, _, planes, bits, length, at = struct.unpack_from('<BBBBHHII', data, 6 + 16 * n)
            self.assertEqual((w, h, planes, bits, at), (size, size, 1, 32, end))
            head = struct.unpack_from('<IiiHH', data, at)
            self.assertEqual(head, (40, size, size * 2, 1, 32))
            self.assertEqual(length, 40 + size * size * 4 + ((size + 31) // 32) * 4 * size)
            end += length
        self.assertEqual(end, len(data))

    def test_the_icon_is_asked_to_sit_by_the_clock_only_once(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / 'icon-placed'
            marker.write_text('asked')
            self.assertFalse(self.pd.keep_in_view(marker))     # a later choice stands; nothing is read or changed


class CtrlWindowsKeys(unittest.TestCase):
    """Ctrl+Windows held alone is the dictation key; anything else is left alone."""
    @classmethod
    def setUpClass(cls):
        cls.pd = program()

    def feed(self, combo, *steps):
        out = []
        for step in steps:
            vk, down = step
            out += combo.feed(vk, down)
        return out

    def test_holding_ctrl_and_windows_starts_and_letting_go_ends(self):
        LCTRL, LWIN = 0xA2, 0x5B
        c = self.pd.Combo()
        self.assertEqual(c.feed(LCTRL, True), [])
        self.assertEqual(c.feed(LWIN, True), ['down'])
        self.assertTrue(c.active)
        self.assertEqual(c.feed(LWIN, True), [])           # the keyboard repeating: nothing new
        self.assertEqual(c.feed(LWIN, False), ['up'])
        self.assertEqual(c.feed(LCTRL, False), [])
        self.assertEqual(c.presses, 1)

    def test_either_order_and_either_side_counts(self):
        c = self.pd.Combo()
        self.assertEqual(self.feed(c, (0x5B, True), (0xA3, True)), ['down'])     # Windows first, right Ctrl
        self.assertEqual(self.feed(c, (0xA3, False), (0x5B, False)), ['up'])
        self.assertEqual(self.feed(c, (0xA2, True), (0x5C, True)), ['down'])     # right Windows key
        self.assertEqual(c.presses, 2)

    def test_a_third_key_keeps_its_usual_meaning(self):
        c = self.pd.Combo()
        # Ctrl+Windows+D (a new desktop): reported once as other, then nothing until both are let go.
        self.assertEqual(self.feed(c, (0xA2, True), (0x5B, True), (0x44, True)), ['down', 'other'])
        self.assertFalse(c.active)
        self.assertEqual(self.feed(c, (0x44, False), (0x44, True), (0x44, False)), [])
        self.assertEqual(self.feed(c, (0x5B, False), (0xA2, False)), [])
        self.assertEqual(self.feed(c, (0xA2, True), (0x5B, True)), ['down'])     # clean again afterwards

    def test_plain_ctrl_or_windows_shortcuts_never_start(self):
        c = self.pd.Combo()
        self.assertEqual(self.feed(c, (0xA2, True), (0x43, True), (0x43, False), (0xA2, False)), [])   # Ctrl+C
        self.assertEqual(self.feed(c, (0x5B, True), (0x45, True), (0x45, False), (0x5B, False)), [])   # Windows+E
        self.assertEqual(self.feed(c, (0x5B, True), (0x5B, False)), [])                                # Start
        # Shift already held: Ctrl+Shift+Windows is some other shortcut, not dictation.
        self.assertEqual(self.feed(c, (0xA0, True), (0xA2, True), (0x5B, True)), [])
        self.assertEqual(c.presses, 0)

    def test_a_key_that_never_came_back_up_is_forgotten(self):
        down = {0x4C: False}             # L of Windows+L: the lock screen swallowed its letting go
        c = self.pd.Combo(still_down=lambda vk: down.get(vk, True))
        self.feed(c, (0x5B, True), (0x4C, True), (0x5B, False))
        self.assertIn(0x4C, c.held)
        self.assertEqual(self.feed(c, (0xA2, True), (0x5B, True)), ['down'])

    def test_the_keys_watch_never_holds_a_key_back(self):
        import inspect
        body = inspect.getsource(self.pd.Keys._key)
        self.assertIn('return user32.CallNextHookEx(None, code, kind, data)', body)
        self.assertEqual(body.count('return '), 1)      # every key goes on to Windows and the other programs

    def test_another_dictation_app_keeps_its_keys_while_it_is_open(self):
        import queue
        events = queue.Queue()
        keys = self.pd.Keys(['ctrl+alt+space'], events)
        self.assertFalse(keys.combo_on)                  # off until the other app is known to be closed
        self.assertEqual(keys.names(), 'ctrl+win')
        keys.combo = 'ctrl+alt+space'
        self.assertEqual(keys.names(), 'ctrl+alt+space')  # while the other app is open the spare keys are named
        keys.combo_on = True
        self.assertEqual(keys.names(), 'ctrl+win')
        self.assertEqual(self.pd.OTHER_APP, 'wispr flow.exe')


class TinyBar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pd = program()

    def test_it_starts_at_the_bottom_middle_just_above_the_taskbar(self):
        area = (0, 0, 1920, 1032)                        # a 1080 screen less a 48 high taskbar
        x, y = self.pd.place(None, area, (68, 18), 10)
        self.assertEqual((x + 34, y + 18), (960, 1022))

    def test_a_saved_spot_is_kept_inside_the_screen(self):
        area = (0, 0, 1920, 1032)
        self.assertEqual(self.pd.place((300, 500), area, (68, 18), 10), (266, 482))
        self.assertEqual(self.pd.place((5, 2000), area, (68, 18), 10), (0, 1014))        # off the edge: pulled back
        self.assertEqual(self.pd.place((5000, -50), area, (68, 18), 10), (1852, 0))
        second = (1920, 0, 4480, 1392)                                                   # a second screen
        self.assertEqual(self.pd.place((3000, 700), second, (68, 18), 10), (2966, 682))

    def test_the_menu_names_ctrl_windows_and_the_tiny_bar(self):
        words = [w for w, _ in self.pd.icon_menu('ctrl+win')]
        self.assertTrue(any('Hold Ctrl+Win' in w for w in words))
        self.assertTrue(any('tiny bar' in w for w in words))
        self.assertFalse(any('another dictation app' in w for w in words))
        words = [w for w, _ in self.pd.icon_menu('ctrl+alt+space', True)]
        self.assertTrue(any('Hold Ctrl+Alt+Space' in w for w in words))
        self.assertTrue(any('close it to use it here' in w for w in words))
        self.assertFalse(any('tiny bar' in w for w in words))
        self.assertIn('Ctrl+Win', self.pd.icon_tip('ctrl+win'))

    def test_the_start_finish_and_cancel_sounds_are_short_and_soft(self):
        import io
        import wave
        made = {}
        for name, notes in self.pd.SOUNDS.items():
            data = self.pd.tone(notes)
            with wave.open(io.BytesIO(data)) as audio:
                frames = audio.readframes(audio.getnframes())
                seconds = audio.getnframes() / audio.getframerate()
            samples = [int.from_bytes(frames[i:i + 2], 'little', signed=True) for i in range(0, len(frames), 2)]
            self.assertLess(seconds, 0.2)                       # a cue, not a tune
            self.assertLess(max(abs(v) for v in samples), 32767 * 0.16)   # quiet next to his voice
            self.assertLess(abs(samples[-1]), 400)              # fades out: no click at the end
            made[name] = data
        self.assertEqual(len(set(made.values())), 3)           # three different cues
        self.assertLess(self.pd.SOUNDS['start'][0][0], self.pd.SOUNDS['start'][-1][0])    # start rises
        self.assertGreater(self.pd.SOUNDS['cancel'][0][0], self.pd.SOUNDS['cancel'][-1][0])  # cancel falls


if __name__ == '__main__':
    unittest.main()
