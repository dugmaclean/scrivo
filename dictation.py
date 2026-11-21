"""Scrivo dictation core: recognition tiers, writing tiers, saved words and recordings.

Written from scratch for this project; no implementation is copied from other projects."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Dictation has no practical length limit. Three hours is only a guard against
# a recording that was never finished; no real dictation reaches it.
MAX_SECONDS = 3 * 3600
MAX_AUDIO = 44 + 32000 * MAX_SECONDS
# Recordings are kept so a failed dictation can always be retried.
# SCRIVO_MAX_STORED_GB sets the storage cap (default 20 gigabytes).
MAX_STORED_AUDIO = int(float(os.getenv('SCRIVO_MAX_STORED_GB', '20')) * 1_000_000_000)


class Unavailable(Exception):
    pass


def validate_audio(audio: bytes) -> None:
    if not audio or len(audio) > MAX_AUDIO:
        raise ValueError('Recordings must be shorter than three hours.')
    try:
        with wave.open(io.BytesIO(audio)) as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, 16000):
                raise ValueError('Use mono 16-bit WAV audio at 16000 samples per second.')
            frames = w.getnframes()
            if not 0 < frames <= 16000 * MAX_SECONDS:
                raise ValueError('Recordings must be shorter than three hours.')
            if len(w.readframes(frames)) != frames * 2:
                raise ValueError('The recording is incomplete.')
    except (wave.Error, EOFError) as e:
        raise ValueError('The recording is not readable WAV audio.') from e


def import_words(data: str, kind: str) -> list[str]:
    """Accept exported text, JSON word lists or CSV with word/term headers."""
    if len(data.encode('utf-8')) > 1_000_000:
        raise ValueError('The word list is too large.')
    if kind == 'json':
        obj = json.loads(data)
        values = obj.get('words') if isinstance(obj, dict) else obj
        if not isinstance(values, list):
            raise ValueError('Expected a list of words.')
    elif kind == 'csv':
        reader = csv.DictReader(io.StringIO(data))
        if not reader.fieldnames or not {'word', 'term'}.intersection(reader.fieldnames):
            raise ValueError('The word list needs a word or term column.')
        values = [row.get('word') or row.get('term') for row in reader]
    elif kind == 'text':
        values = data.splitlines()
    else:
        raise ValueError('Choose text, json or csv.')
    words = []
    for word in values:
        if not isinstance(word, str):
            raise ValueError('Every dictionary entry must be text.')
        word = word.strip()
        if not word:
            continue
        if len(word) > 120 or any(ord(c) < 32 for c in word):
            raise ValueError('A dictionary entry is too long or contains control characters.')
        if word.casefold() not in {w.casefold() for w in words}:
            words.append(word)
        if len(words) > 5000:
            raise ValueError('Import at most 5000 words at once.')
    return words


def import_entries(data: str, kind: str) -> list[dict]:
    """Keep imported spelling variants, corrections and snippets intact."""
    if len(data.encode('utf-8')) > 1_000_000:
        raise ValueError('The dictionary is too large.')
    obj = json.loads(data) if kind == 'json' else None
    if not isinstance(obj, dict) or 'entries' not in obj:
        return [{'phrase': word, 'replacement': None, 'is_snippet': False}
                for word in import_words(data, kind)]
    values = obj['entries']
    if not isinstance(values, list) or len(values) > 5000:
        raise ValueError('Expected at most 5000 dictionary entries.')
    entries = []
    for entry in values:
        if not isinstance(entry, dict) or set(entry) != {'phrase', 'replacement', 'is_snippet'}:
            raise ValueError('Each entry needs a phrase, replacement and shortcut flag.')
        phrase, replacement, snippet = entry['phrase'], entry['replacement'], entry['is_snippet']
        if (not isinstance(phrase, str) or not phrase.strip() or len(phrase) > 120
                or any(ord(c) < 32 for c in phrase) or type(snippet) is not bool):
            raise ValueError('Check the dictionary phrase or shortcut flag.')
        if replacement is not None and (not isinstance(replacement, str) or not replacement.strip()
                or len(replacement) > 2000 or any(ord(c) < 32 and c not in '\n\r\t' for c in replacement)):
            raise ValueError('Check the dictionary replacement.')
        if snippet and replacement is None:
            raise ValueError('A shortcut needs its replacement text.')
        entries.append(dict(phrase=phrase, replacement=replacement, is_snippet=snippet))
    return entries


def apply_entries(text, entries):
    # One substitution pass: expansions never recursively trigger another entry.
    replacements = {e['phrase'].casefold(): e['replacement'] for e in entries if e['replacement']}
    if not replacements:
        return text
    pattern = r'(?<!\w)(?:' + '|'.join(re.escape(p) for p in sorted(replacements, key=len, reverse=True)) + r')(?!\w)'
    return re.sub(pattern, lambda m: replacements.get(m[0].casefold(), m[0]), text, flags=re.IGNORECASE)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS words(word TEXT PRIMARY KEY COLLATE NOCASE);
                CREATE TABLE IF NOT EXISTS dictionary_entries(
                    phrase TEXT PRIMARY KEY, replacement TEXT, is_snippet INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS recordings(
                    id TEXT PRIMARY KEY, audio BLOB NOT NULL, created REAL NOT NULL,
                    raw TEXT, text TEXT, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(name TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
        path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def setting(self, name, default=None):
        """One saved choice shared by every device, such as the three writing models."""
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE name=?', (name,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, name, value):
        with self.connect() as db:
            db.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value',
                       (name, json.dumps(value)))

    def words(self):
        with self.connect() as db:
            return [row[0] for row in db.execute('SELECT word FROM words ORDER BY word')]

    def add_words(self, words):
        with self.connect() as db:
            db.executemany('INSERT OR IGNORE INTO words VALUES (?)', [(w,) for w in words])
        return self.words()

    def entries(self):
        with self.connect() as db:
            return [dict(phrase=p, replacement=r, is_snippet=bool(s)) for p, r, s in db.execute(
                'SELECT phrase,replacement,is_snippet FROM dictionary_entries ORDER BY phrase')]

    def add_entries(self, entries):
        with self.connect() as db:
            # Conflicting existing entries are never silently overwritten.
            for e in entries:
                old = db.execute('SELECT replacement,is_snippet FROM dictionary_entries WHERE phrase=?', (e['phrase'],)).fetchone()
                if old is not None and old != (e['replacement'], int(e['is_snippet'])):
                    raise ValueError('An existing dictionary phrase has a different replacement.')
                db.execute('INSERT OR IGNORE INTO dictionary_entries VALUES (?,?,?)',
                           (e['phrase'], e['replacement'], int(e['is_snippet'])))
                hint = e['phrase'] if e['is_snippet'] else (e['replacement'] or e['phrase'])
                db.execute('INSERT OR IGNORE INTO words VALUES (?)', (hint,))
        return self.words()

    def save(self, id, audio):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            stored = db.execute('SELECT COALESCE(SUM(LENGTH(audio)),0) FROM recordings').fetchone()[0]
            if stored + len(audio) > MAX_STORED_AUDIO:
                raise ValueError('Recording storage is full. Existing recordings were kept.')
            db.execute('INSERT INTO recordings VALUES (?,?,?,NULL,NULL,?)', (id, audio, time.time(), 'pending'))

    def get(self, id):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM recordings WHERE id=?', (id,)).fetchone()
            if row is None:
                raise KeyError(id)
            return dict(row)

    def result(self, id, raw, text, status):
        with self.connect() as db:
            db.execute('UPDATE recordings SET raw=?,text=?,status=? WHERE id=?', (raw, text, status, id))


# Words that mean the speaker hesitated, corrected himself or asked for layout.
# The list is deliberately wide: a false match only costs the usual writing time.
CLEANUP_CUES = re.compile(
    r"\b(?:u+[hm]+|e+r+m*|h+m+|a+h+|you know|i mean|actually|no|nope|wait|sorry|oops|scratch that|"
    r"rather|make that|correction|instead|never ?mind|delete|undo|new ?line|new paragraph|bullet|"
    r"comma|period|full stop|question mark|exclamation|colon|semicolon|quote|unquote|dash|hyphen|"
    r"first|firstly|second|secondly|third|number one|list|like|kind of|sort of|basically)\b", re.I)
CLOCK = re.compile(r"\b(at|by|around|about|until|till|before|after|from|to)\s+(1[0-2]|[1-9])\.([0-5]\d)\b", re.I)


def sound_key(text):
    """A rough spelling-free key: the consonants a phrase is heard as."""
    key = re.sub(r'[^a-z]', '', text.lower())
    for old, new in (('ph', 'f'), ('ck', 'k'), ('gh', ''), ('c', 'k'), ('q', 'k'), ('z', 's'), ('x', 'ks')):
        key = key.replace(old, new)
    key = re.sub(r'[aeiouyh]', '', key)
    return re.sub(r'(.)\1+', r'\1', key)


def misheard_saved_word(text, words):
    """True when the text holds something that sounds like a saved word but is
    spelled another way ("cooper netties" for a saved "Kubernetes").

    In testing, finishing plain sentences without the
    writing model left two of five saved names in their everyday spelling. Such
    a sentence goes to the writing model, which is given the saved spellings.
    """
    saved = {}
    for word in words:
        parts = word.split()
        key = sound_key(word)
        if 1 <= len(parts) <= 3 and len(key) >= 2:
            saved.setdefault((len(parts), key), set()).add(' '.join(parts))
    if not saved:
        return False
    spoken = re.findall(r"[A-Za-z][A-Za-z'’-]*", text)
    for size in (1, 2, 3):
        for start in range(len(spoken) - size + 1):
            piece = ' '.join(spoken[start:start + size])
            spellings = saved.get((size, sound_key(piece)))
            if spellings and piece not in spellings:
                return True
    return False


_TOKEN = re.compile(r"[^\W_]+(?:'[^\W_]+)?")


def _runs(text, size=6, times=3):
    """Spans (start, end, n) of words where one phrase of n words repeats times+ in a row."""
    spans = list(re.finditer(r"\S+", text))
    keys = [' '.join(_TOKEN.findall(m.group().lower())) for m in spans]
    found, at = [], 0
    while at < len(keys):
        best = None
        for n in range(1, size + 1):
            if not all(keys[at:at + n]) or at + n * times > len(keys):
                continue
            count = 1
            while keys[at + count * n:at + (count + 1) * n] == keys[at:at + n]:
                count += 1
            if count >= times:
                best = (at, at + count * n, n)
                break
        if best:
            found.append(best)
            at = best[1]
        else:
            at += 1
    return spans, found


def looped(text):
    """True when one word or short phrase runs on three or more times in a row."""
    return bool(_runs(text or '')[1])


def collapse_loops(text):
    """Keep a single copy of any word or short phrase that runs on 3+ times in a row."""
    if not text:
        return text
    spans, found = _runs(text)
    if not found:
        return text
    out, last = [], 0
    for start, end, n in found:
        out.append(text[last:spans[start].start()])
        keep = text[spans[start].start():spans[start + n - 1].end()]
        tail = spans[end - 1].group()
        mark = tail[len(tail.rstrip('.,;:!?')):]
        out.append(keep.rstrip(',;:') + mark if mark and not keep.endswith(mark) else keep)
        last = spans[end - 1].end()
    out.append(text[last:])
    return re.sub(r"\s+", ' ', ''.join(out)).strip()


# A sentence end followed by a lowercase word. Streaming hears a dictation in
# pieces and a piece often begins mid-thought, so everyday
# sentences could finish as "...today. we're ready... what time...".
_LOW_START = re.compile(r"(?<![\w.])([^\s.?!]*[.?!]['\")\]]*)(\s+)([a-z])")
_NOT_AN_END = {'mr.', 'mrs.', 'ms.', 'dr.', 'st.', 'vs.', 'etc.', 'approx.', 'no.', 'jr.', 'sr.'}


def sentence_starts(text):
    """Capitalize a sentence that starts lowercase after a full stop, question or exclamation mark.

    An abbreviation such as a.m., e.g. or Dr. is not a sentence end.
    """
    def fix(match):
        before = match.group(1).lower().rstrip('\'")]')
        if before in _NOT_AN_END or before.count('.') > 1 or len(before) < 2:
            return match.group(0)
        return match.group(1) + match.group(2) + match.group(3).upper()
    return _LOW_START.sub(fix, text) if text else text


# A spoken restart: two or more words said again a few words later with no
# sentence end between ("the inspection should be, inspector should be there").
_RESTART = re.compile(r"\b(\w+[\s,]+\w+)\b[\s,]+(?:[\w']+[\s,]+){0,3}?\1\b", re.I)


def plain_words(raw, words=()):
    """The recognized words as final text when they need no rewriting, else None.

    Measured on ten everyday sentences: a local writer returned
    eight unchanged, removed one spoken correction and changed one clock time.
    A short sentence with no hesitation, correction or layout word is therefore
    finished at once, and only the rest waits for the chosen writing model.
    A sentence that may hold a saved word in another spelling also waits.
    """
    text = ' '.join(raw.split())
    if (not text or len(text.split()) > 45 or CLEANUP_CUES.search(text)
            or re.search(r"\b(\w+)\W+\1\b", text, re.I)        # a stutter, even with a comma between
            or _RESTART.search(text)                            # a restart of the same words
            or text[-1] not in '.?!' or not text[0].isupper()   # not a finished sentence
            or misheard_saved_word(text, words)):
        return None
    return sentence_starts(CLOCK.sub(r"\1 \2:\3", text))


@dataclass
class Tier:
    name: str
    run: Callable
    cloud: bool = False


# A writing model sometimes talks instead of tidying: a short request can come back as
# "I don't see any dictation text yet. Paste the text you want cleaned up...", or behind a
# paragraph about how the model would handle it. A tidy-up keeps the words that were said
# (fillers and corrected words go, a few small words may come in); a reply brings in mostly
# new words.
TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
# Small words a tidy-up may add: "gonna" becomes "going to", "wanna" "want to", joins and contractions.
TIDY_WORDS = frozenset("""a an the and or but so to of in on at for with is are was were be been it it's its
    i i'm i'll i've i'd you you're we we're they he she that this going want got have has had will would
    do does did not don't can't won't isn't aren't didn't doesn't let's there their there's""".split())


def _tokens(text):
    return TOKEN.findall(text.lower().replace('’', "'"))


def _novel(tokens, allowed):
    return [t for t in tokens if t not in allowed and t not in TIDY_WORDS and not t.isdigit()]


# Hesitation sounds a tidy-up always removes; dropping them never counts against it.
FILLERS = frozenset('um umm uh uhh erm er ah hmm mm mhm'.split())
# Spoken numbers and times are often written as digits ("five thirty pm" becomes "5:30 p.m.").
NUMBER_WORDS = frozenset('''zero oh one two three four five six seven eight nine ten eleven twelve thirteen
    fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety
    hundred thousand million first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth
    half quarter am pm percent dollars'''.split())


def _dropped_too_much(said, tokens):
    """True when a reply leaves out most of what was said.

    A tidy-up drops fillers, corrected words and false starts, a few words at a time.
    A small model can instead shorten a request to a command ("Call the dentist") or
    answer it ("okay"); the spoken words are then worth more than its answer.
    """
    content = [t for t in dict.fromkeys(said)
               if t not in TIDY_WORDS and t not in FILLERS and t not in NUMBER_WORDS and not t.isdigit()]
    kept = set(tokens)
    dropped = [t for t in content if t not in kept]
    return len(dropped) > max(4, len(content) // 2)


def _faithful(tokens, allowed, raw_count):
    novel = _novel(tokens, allowed)
    if len(novel) >= 5 and len(novel) > 0.3 * len(tokens):
        return False
    return len(tokens) <= 2 * raw_count + 8          # a tidy-up never doubles what was said


def tidied(raw, written, words=()):
    """The written text when it is a tidy-up of these spoken words, else None.

    A paragraph of the model's own talk before or after a faithful tidy-up is dropped,
    keeping the tidy-up, when what is left still holds most of what was said.
    """
    said = _tokens(raw)
    allowed = set(said)
    for word in words or ():
        if isinstance(word, str):
            allowed.update(_tokens(word))
    text = '\n'.join(line for line in written.strip().splitlines()
                     if line.strip() not in ('<<<DICTATION', 'DICTATION>>>')).strip()
    if _faithful(_tokens(text), allowed, len(said)) and not _dropped_too_much(said, _tokens(text)):
        return text or None
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    if len(paragraphs) < 2:
        return None
    kept = []
    for paragraph in paragraphs:
        tokens = _tokens(paragraph)
        novel = _novel(tokens, allowed)
        if not (len(novel) >= 4 and len(novel) > 0.5 * len(tokens)):
            kept.append(paragraph)
    if not kept or len(kept) == len(paragraphs):
        return None
    left = '\n\n'.join(kept)
    tokens = _tokens(left)
    content = {t for t in said if t not in TIDY_WORDS}
    covered = len(content & set(tokens)) / len(content) if content else 1.0
    return left if covered >= 0.6 and _faithful(tokens, allowed, len(said)) else None


class NotATidyUp(Unavailable):
    pass


def first_success(tiers, value, words, allow_cloud):
    failures = []
    for tier in tiers:
        if tier.cloud and not allow_cloud:
            failures.append({'model': tier.name, 'reason': 'cloud_disabled'})
            continue
        try:
            result = tier.run(value, words)
            if not isinstance(result, str) or not result.strip():
                raise Unavailable()
            result = result.strip()
            if isinstance(value, str):
                # Writing: the answer must be the spoken words tidied, never the model talking back.
                result = tidied(value, result, words)
                if result is None:
                    raise NotATidyUp()
            return result, tier.name, failures
        except NotATidyUp:
            failures.append({'model': tier.name, 'reason': 'unavailable', 'why': 'replied_instead_of_tidying'})
        except Exception:
            # Provider errors can contain private prompts or credentials: retain only a safe category.
            failures.append({'model': tier.name, 'reason': 'unavailable'})
    raise AllFailed(failures)


class AllFailed(Unavailable):
    def __init__(self, failures):
        self.failures = failures


# Long dictations are cleaned up a piece at a time. One request for several
# minutes of speech ran past every writer's time allowance, so the whole
# dictation came back unpolished. A piece of about a hundred words is as quick
# as an everyday dictation, and one piece failing keeps its own words only.
PIECE_WORDS = 100
LONG_WORDS = 150          # up to this many words are still written in one request


def pieces(text, size=PIECE_WORDS):
    """Split recognized words into pieces that end at a sentence end.

    Greedy from the start: a piece ends at the first sentence end after `size`
    words (or at 3 x size words when nobody says a full stop), so the pieces of
    a settled beginning never change when more words are added after it.
    """
    words = text.split()
    out, start = [], 0
    for at, word in enumerate(words):
        count = at - start + 1
        if (count >= size and word[-1] in '.?!') or count >= 3 * size:
            out.append(' '.join(words[start:at + 1]))
            start = at + 1
    if start < len(words):
        out.append(' '.join(words[start:]))
    return out


def write_long(tiers, raw, words, allow_cloud, ready=None):
    """(text, writer, failures, unpolished piece count) for a long dictation.

    `ready` maps a piece's words to (text, writer, failures) already written
    during speech; those pieces are not written again.
    """
    ready = ready or {}
    parts, writers, failures, missed = [], {}, [], 0
    for piece in pieces(raw):
        done = ready.get(piece)
        if done is None:
            try:
                done = first_success(tiers, piece, words, allow_cloud)
            except AllFailed as error:
                failures.extend(error.failures)
                parts.append(piece)
                missed += 1
                continue
        parts.append(done[0])
        writers[done[1]] = writers.get(done[1], 0) + 1
        failures.extend(done[2])
    if not writers:
        raise AllFailed(failures)
    return ' '.join(parts), max(writers, key=writers.get), failures, missed


class Pipeline:
    def __init__(self, store, recognition, writing, quick_plain=False):
        # Up to three ordered choices per stage: the first that answers wins.
        if not 1 <= len(recognition) <= 3 or not 1 <= len(writing) <= 3:
            raise ValueError('Each stage needs one to three ordered choices.')
        self.store, self.recognition, self.writing = store, recognition, writing
        # Finish plain sentences without waiting for a writing model.
        self.quick_plain = quick_plain
        self.lock = threading.Lock()

    def process(self, id, allow_cloud=False, writing=None, ready=None):
        # One bounded local inference at a time; the HTTP layer rejects concurrent work.
        with self.lock:
            row = self.store.get(id)
            words = self.store.words()
            if row['raw']:
                raw, recognizer, rec_failures = row['raw'], 'saved-recognition', []
            else:
                raw, recognizer, rec_failures = first_success(self.recognition, row['audio'], words, False)
            # Each piece heard is already checked; a run-on repeat across two pieces is not.
            raw = collapse_loops(raw)
            # Save recognized words before writing begins, so interruption cannot lose them.
            self.store.result(id, raw, raw, 'recognized')
            plain = plain_words(raw, words) if self.quick_plain else None
            missed, count = 0, 1
            try:
                if plain:
                    text, writer, failures = plain, 'not-needed', []
                elif len(raw.split()) > LONG_WORDS:
                    count = len(pieces(raw))
                    text, writer, failures, missed = write_long(
                        writing or self.writing, raw, words, allow_cloud, ready)
                else:
                    text, writer, failures = first_success(writing or self.writing, raw, words, allow_cloud)
                status = 'ready'
            except AllFailed as e:
                text, writer, failures, status, missed = sentence_starts(raw), None, e.failures, 'raw', count
            text = apply_entries(text, self.store.entries())
            self.store.result(id, raw, text, status)
            result = {'id': id, 'text': text, 'raw': raw, 'status': status,
                      'recognizer': recognizer, 'writer': writer,
                      'recognition_failures': rec_failures, 'writing_failures': failures}
            if count > 1:
                result.update(pieces=count, unpolished_pieces=missed)
            return result


CUDA_LIBRARIES = Path(os.getenv('DICTATION_CUDA_LIBRARIES',
                                str(Path.home() / '.local/share/scrivo/cuda')))


# Models download on first use unless SCRIVO_OFFLINE=1 (then they must be cached already).
OFFLINE = os.getenv('SCRIVO_OFFLINE') == '1'


def load_cuda_libraries(folder):
    """Load the app's own copy of the graphics-card maths libraries, or raise.

    The shared Python environment has none and is left alone. Loading them by
    path here lets the speech model find them without changing the service unit.
    """
    import ctypes
    names = ['libcublasLt.so.12', 'libcublas.so.12'] + sorted(
        path.name for path in Path(folder).glob('libcudnn*.so.9'))
    if len(names) < 4:
        raise Unavailable()
    for name in names:
        ctypes.CDLL(str(Path(folder) / name), mode=ctypes.RTLD_GLOBAL)


def whisper_tier(model, threads=8, cuda=None, retry_after=60):
    """One local speech model. With cuda (a folder of maths libraries) it runs on
    the graphics card only; any failure there hands over to the next tier and the
    card is not tried again for a minute, so a busy card never slows dictation."""
    loaded = None
    failed_at = None
    loading = threading.Lock()   # warm-up and a first dictation load it once

    def load():
        from faster_whisper import WhisperModel
        if cuda is None:
            return WhisperModel(model, device='cpu', compute_type='int8', cpu_threads=threads, local_files_only=OFFLINE)
        load_cuda_libraries(cuda)
        return WhisperModel(model, device='cuda', compute_type='float16', local_files_only=OFFLINE)

    def recognize(audio, words):
        nonlocal loaded, failed_at
        validate_audio(audio)
        if cuda is not None and failed_at is not None and time.monotonic() - failed_at < retry_after:
            raise Unavailable()
        try:
            with loading:
                if loaded is None:
                    loaded = load()
                model_now = loaded
            return transcribe(model_now, audio, words)
        except Exception:
            if cuda is not None:
                loaded, failed_at = None, time.monotonic()
            raise

    def transcribe(loaded, audio, words):
        # The phone contract is already PCM WAV: avoid optional PyAV decoding.
        # Installed PyAV rejects faster-whisper's metadata_errors argument.
        import numpy as np
        with wave.open(io.BytesIO(audio)) as recording:
            samples = np.frombuffer(recording.readframes(recording.getnframes()), dtype='<i2').astype(np.float32) / 32768.0
        # Saved spellings are hints, not earlier speech: as an opening prompt the
        # list made the first pass fail and retry on longer recordings (measured).
        # Hints spell saved names equally well without the retries.
        # One steady pass only: a random second pass on a short piece of speech
        # sometimes ran for five seconds. The other two models remain the backups.
        def once(hints):
            segments, _ = loaded.transcribe(samples, language='en', vad_filter=True,
                                           hotwords=hints, temperature=0.0,
                                           condition_on_previous_text=False, beam_size=1)
            return ' '.join(segment.text.strip() for segment in segments)
        text = once(', '.join(words)[:1200] or None)
        # A pause can make the model repeat a saved hint instead of the speech
        # (one saved name nine times in a row was seen). Read it again
        # without the hints, and never pass a run-on repeat through.
        if words and looped(text):
            text = once(None)
        return collapse_loops(text)
    return Tier(model + '-graphics-card' if cuda is not None else model, recognize)


def recognition_tiers(cuda=CUDA_LIBRARIES):
    """Three ordered speech choices. With the app's graphics-card libraries in
    place the usual model runs there first and the processor models are its
    backups. On a recent graphics card small.en hears a sentence in well under a
    second; on a processor it takes one to three seconds. Larger models were
    measured to repeat long spelling-hint lists instead of the speech, so they
    are not the default. SCRIVO_RECOGNITION overrides the three models as a
    comma-separated list, for example "small.en,base.en,tiny.en"."""
    chosen = [m.strip() for m in os.getenv('SCRIVO_RECOGNITION', '').split(',') if m.strip()]
    if chosen:
        return [whisper_tier(m) for m in chosen[:3]]
    if cuda is not None and Path(cuda, 'libcublas.so.12').is_file():
        return [whisper_tier('small.en', cuda=cuda), whisper_tier('small.en'), whisper_tier('tiny.en')]
    return [whisper_tier(m) for m in ('small.en', 'small', 'tiny.en')]
