"""The part of the computer client that talks to the Scrivo server.

Nothing here is Windows-only, so the same code is checked on Linux
and then run unchanged on Windows. It sends the microphone's sound while the user
is still speaking, reads the words heard so far for the speaking bar, and keeps
the recording on this computer whenever the server cannot be reached.

The same server holds the dictionary and the three writing models
(`writer=saved`), so the computer shares both with every phone without a copy.
Only the Python standard library is used. Words are never written to the log.
"""
import array
import http.client
import io
import json
import os
import queue
import ssl
import threading
import time
import urllib.parse
import uuid
import wave
from pathlib import Path

RATE = 16000
CHUNK = 1600                 # bytes in 50 ms of 16 kHz mono 16-bit sound
WAIT = 1800                  # seconds to wait for the words of a very long dictation
KEEP = 10                    # finished recordings kept on this computer


def load_config(path):
    """The private server address and key, from the settings file."""
    # Notepad and Windows PowerShell may save the file with a byte-order mark: accept it.
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    endpoint, token = data.get('endpoint', ''), data.get('token', '')
    parts = urllib.parse.urlsplit(endpoint)
    local = parts.hostname in ('127.0.0.1', 'localhost')
    if parts.scheme != 'https' and not (parts.scheme == 'http' and local):
        raise ValueError('The server address must be a secure (https) one.')
    if len(token) < 32:
        raise ValueError('The private key for the server is missing.')
    data['endpoint'] = endpoint.rstrip('/')
    return data


def wav(pcm):
    out = io.BytesIO()
    with wave.open(out, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(RATE)
        audio.writeframes(pcm)
    return out.getvalue()


def level(pcm):
    """Loudness of one piece of sound, 0.0 to 1.0 on a rough speech scale."""
    samples = array.array('h', pcm[:len(pcm) // 2 * 2])
    if not samples:
        return 0.0
    mean = sum(s * s for s in samples) / len(samples)
    return min(1.0, (mean ** 0.5) / 6000.0)


class Meter:
    """Bar heights for the speaking bar: measured against the room's own hum."""

    def __init__(self, bars=9):
        self.bars = [0.0] * bars
        self.floor = 0.02
        self.peak = 0.15

    def push(self, loudness):
        # The quietest recent sound is the room; the loudest is the speaker.
        self.floor = min(loudness, self.floor * 1.02 + 0.0005) if loudness > 0 else self.floor
        self.peak = max(loudness, self.peak * 0.995, self.floor + 0.05)
        value = max(0.0, min(1.0, (loudness - self.floor) / (self.peak - self.floor)))
        self.bars = self.bars[1:] + [value]

    def settle(self, rate=0.90):
        self.bars = [b * rate for b in self.bars]


def tail(text, lines, width):
    """The newest words that fit the small live-words box, whole words only."""
    rows, row = [], ''
    for word in text.split():
        if row and len(row) + 1 + len(word) > width:
            rows.append(row)
            row = word
        else:
            row = (row + ' ' + word).strip()
    if row:
        rows.append(row)
    return '\n'.join(rows[-lines:])


class Connection:
    def __init__(self, endpoint, token, timeout):
        parts = urllib.parse.urlsplit(endpoint)
        if parts.scheme == 'https':
            self.http = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=timeout,
                                                    context=ssl.create_default_context())
        else:
            self.http = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
        self.headers = {'Authorization': 'Bearer ' + token}

    def close(self):
        try:
            self.http.close()
        except Exception:
            pass


def ask(endpoint, token, method, path, body=None, timeout=15):
    """One short question to the server. Returns (status, parsed answer or None)."""
    link = Connection(endpoint, token, timeout)
    try:
        headers = dict(link.headers)
        if body is not None:
            headers['Content-Type'] = 'application/octet-stream'
        link.http.request(method, path, body=body, headers=headers)
        reply = link.http.getresponse()
        data = reply.read(2_000_000)
        try:
            return reply.status, json.loads(data)
        except ValueError:
            return reply.status, None
    finally:
        link.close()


class Dictation:
    """One spoken piece: sound goes to the server as it is captured; words come back.

    feed() takes sound from the microphone thread. finish() asks for the final
    words; cancel() throws the piece away. The result arrives through on_done
    as a dict with status ready, kept (server unreachable, recording saved here),
    retry (the server has the recording but no model answered) or cancelled.
    """

    def __init__(self, endpoint, token, keep_dir, on_preview=None, on_done=None, writer='saved'):
        self.endpoint, self.token = endpoint, token
        self.keep_dir = Path(keep_dir)
        self.on_preview = on_preview or (lambda text: None)
        self.on_done = on_done or (lambda result: None)
        self.writer = writer
        self.session = uuid.uuid4().hex
        self.pieces = queue.Queue()
        self.pcm = bytearray()
        self.cancelled = False
        self.ended = threading.Event()
        self.finished_at = None
        self.link = None
        self.threads = [threading.Thread(target=self._send, daemon=True),
                        threading.Thread(target=self._previews, daemon=True)]

    def start(self):
        for thread in self.threads:
            thread.start()
        return self

    def feed(self, pcm):
        if self.ended.is_set() or not pcm:
            return True
        self.pcm += pcm
        self.pieces.put(bytes(pcm))
        return True

    def finish(self):
        if not self.ended.is_set():
            self.finished_at = time.monotonic()
            self.ended.set()
            self.pieces.put(None)

    def cancel(self):
        self.cancelled = True
        self.ended.set()
        self.pieces.put(None)
        if self.link:
            self.link.close()

    def join(self, timeout=None):
        self.threads[0].join(timeout)

    # -- internals -------------------------------------------------------
    def _send(self):
        result = None
        try:
            result = self._stream()
        except Exception as error:
            result = {'status': 'failed', 'reason': type(error).__name__}
        if self.cancelled:
            result = {'status': 'cancelled'}
        elif result.get('status') == 'failed':
            # The server did not answer. Wait for the rest of the sound, keep it here,
            # then try once more with the whole recording.
            self.ended.wait()
            if self.cancelled:
                result = {'status': 'cancelled'}
            else:
                result = self._whole(result.get('reason'))
        if self.finished_at and result.get('status') == 'ready':
            result['seconds_after_finish'] = round(time.monotonic() - self.finished_at, 2)
        self.ended.set()
        self.on_done(result)

    def _stream(self):
        self.link = Connection(self.endpoint, self.token, WAIT)
        http_ = self.link.http
        try:
            http_.putrequest('POST', '/v1/stream?writer=%s&session=%s' % (self.writer, self.session))
            http_.putheader('Authorization', 'Bearer ' + self.token)
            http_.putheader('Content-Type', 'application/octet-stream')
            http_.putheader('Transfer-Encoding', 'chunked')
            http_.endheaders()
            while True:
                piece = self.pieces.get()
                if piece is None:
                    break
                http_.send(b'%x\r\n%s\r\n' % (len(piece), piece))
            if self.cancelled:
                return {'status': 'cancelled'}
            http_.send(b'0\r\n\r\n')
            reply = http_.getresponse()
            data = reply.read(2_000_000)
            return self._answer(reply.status, data)
        finally:
            self.link.close()

    @staticmethod
    def _answer(status, data):
        try:
            body = json.loads(data)
        except ValueError:
            body = {}
        if status == 200 and isinstance(body.get('text'), str):
            return {'status': 'ready', 'text': body['text'], 'writer': body.get('writer'),
                    'id': body.get('id'), 'home_seconds': body.get('finish_seconds')}
        if status == 503 and body.get('recording_saved'):
            return {'status': 'retry', 'id': body.get('id')}
        return {'status': 'failed', 'reason': 'http %s' % status}

    def _whole(self, reason):
        kept = self.keep()
        if kept is None:
            return {'status': 'failed', 'reason': 'nothing recorded'}
        try:
            status, body = ask(self.endpoint, self.token, 'POST', '/v1/dictate?writer=' + self.writer,
                               kept.read_bytes(), timeout=WAIT)
            result = self._answer(status, json.dumps(body or {}).encode())
        except Exception as error:
            result = {'status': 'failed', 'reason': type(error).__name__}
        if result['status'] in ('ready', 'retry'):
            kept.unlink(missing_ok=True)       # the server holds this recording now
            return result
        return {'status': 'kept', 'file': str(kept), 'reason': result.get('reason') or reason}

    def keep(self):
        """Save this recording on the computer; tidy all but the newest few."""
        if len(self.pcm) < 2:
            return None
        self.keep_dir.mkdir(parents=True, exist_ok=True)
        path = self.keep_dir / time.strftime('recording-%Y%m%d-%H%M%S.wav')
        path.write_bytes(wav(bytes(self.pcm[:len(self.pcm) // 2 * 2])))
        old = sorted(self.keep_dir.glob('recording-*.wav'), key=os.path.getmtime)
        for extra in old[:-KEEP]:
            extra.unlink(missing_ok=True)
        return path

    def _previews(self):
        last = ''
        while not self.ended.wait(0.3):
            try:
                status, body = ask(self.endpoint, self.token, 'GET', '/v1/streams/' + self.session, timeout=5)
            except Exception:
                continue
            text = (body or {}).get('text') if status == 200 else None
            if isinstance(text, str) and text != last and not self.ended.is_set():
                last = text
                self.on_preview(text)


def send_file(config, path, keep_dir, pace=True, on_preview=None):
    """Dictate a saved recording exactly as the microphone would. For checks."""
    with wave.open(str(path)) as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (RATE, 1, 2):
            raise ValueError('Use mono 16-bit sound at 16000 samples per second.')
        pcm = audio.readframes(audio.getnframes())
    done, box = threading.Event(), {}
    previews = []

    def finished(result):
        box.update(result)
        done.set()

    def heard(text):
        previews.append(time.monotonic())
        if on_preview:
            on_preview(text)
    piece = Dictation(config['endpoint'], config['token'], keep_dir, heard, finished).start()
    began = time.monotonic()
    for start in range(0, len(pcm), CHUNK):
        piece.feed(pcm[start:start + CHUNK])
        if pace:
            time.sleep(CHUNK / 2 / RATE)
    spoken = time.monotonic() - began
    piece.finish()
    done.wait(WAIT)
    box['previews_while_speaking'] = sum(1 for at in previews if at - began <= spoken)
    return box
