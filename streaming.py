"""Bounded incremental recognition: words become final at pauses during capture.

This uses the existing three local recognition tiers. It does not claim a native
streaming acoustic model. While capture continues, audio up to the latest pause
is recognized once and kept; only the short remainder is read again. Finishing
therefore recognizes the last few words, never the whole recording. Only one
decode runs at once and waiting audio is coalesced, so a slow model cannot build
an unbounded inference queue.

When the speaker pauses, the chosen local writing tiers may start cleaning the
words heard so far. That draft is used only if the final recognized words are
exactly the same; otherwise writing runs again on the final words.

A phone in a real room rarely records true quiet, so loudness alone misses many
pauses. When recognition is quick (the graphics card), the words are read again
every 0.4 seconds, and two readings in a row with the same words also count as
a pause for starting the local writing draft.
"""
import asyncio
import io
import time
import wave
from dictation import AllFailed, LONG_WORDS, MAX_AUDIO, collapse_loops, first_success, pieces, plain_words

FRAME = 640          # bytes in 20 ms of 16 kHz mono 16-bit audio
PAUSE = 20           # 0.4 s of quiet: words before it will not change
TRAIL = PAUSE        # the same quiet after speech: the speaker may be done
MIN_HEAD = 50        # keep at least 1 s together so short words keep context
LONG = 1200          # 24 s without a pause: cut at the quietest point instead
FLOOR = 100 * 100    # mean square of near-silent digital audio
SPEECH = 9 * FLOOR   # a recording this loud has real speech to compare against
STEP = 32000         # one second of new speech between readings on a slow model
QUICK_STEP = 12800   # 0.4 s between readings when one takes under QUICK seconds
QUICK = 0.25
WINDOW = 15000       # loudness is judged on the last five minutes of frames


def wav(pcm):
    out = io.BytesIO()
    with wave.open(out, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(pcm)
    return out.getvalue()


def hear(tiers, audio, words):
    """Like first_success, but a working model hearing nothing is not a failure."""
    failures, silent = [], False
    for tier in tiers:
        try:
            text = tier.run(audio, words)
            if isinstance(text, str) and text.strip():
                return text.strip(), tier.name, failures
            silent = True
            failures.append({'model': tier.name, 'reason': 'no_words'})
        except Exception:
            # Provider errors can contain private data: retain only a safe category.
            failures.append({'model': tier.name, 'reason': 'unavailable'})
    if silent:
        return '', None, failures
    raise AllFailed(failures)


class LiveRecognition:
    def __init__(self, pipeline, writing=None):
        self.pipeline = pipeline
        self.pcm = bytearray()
        self.energy = []       # mean square of each complete 20 ms frame
        self.task = None
        self.decoded = 0       # bytes covered by self.raw
        self.done = 0          # bytes whose words are final
        self.done_text = []
        self.tail = ''         # words heard between done and decoded
        self.raw = None
        self.failures = []
        self.recognizer = None
        self.words = pipeline.store.words()
        self.writing = writing  # local tiers for a draft at pauses, or None
        self.draft = None       # (recognized words, writing task)
        self.step = STEP        # new audio needed before the words are read again
        self.heard_before = None  # the previous reading, to notice unchanged words
        self.timing = {'recognition_calls': 0, 'slowest_recognition': 0.0}
        self.piece_drafts = {}  # settled piece of a long dictation -> its writing task
        self.levels_at = None   # (frame count, levels) of the last loudness reading

    def _measure(self):
        start = len(self.energy) * FRAME
        end = len(self.pcm) // FRAME * FRAME
        if end > start:
            samples = memoryview(bytes(self.pcm[start:end])).cast('h').tolist()
            half = FRAME // 2
            for at in range(0, len(samples), half):
                self.energy.append(sum(x * x for x in samples[at:at + half]) // half)

    def _levels(self):
        if not self.energy:
            return FLOOR, False
        # Sorting every frame of a long dictation several times a second slowed the
        # live words; the last five minutes, read again every second, are enough.
        count = len(self.energy)
        if self.levels_at is None or count - self.levels_at[0] >= 50 or count < 50:
            recent = sorted(self.energy[-WINDOW:])
            loud = recent[len(recent) * 95 // 100]
            # Quiet means a twentieth of the loud speech level, never below the floor.
            self.levels_at = (count, (max(FLOOR, loud // 400), loud >= SPEECH))
        return self.levels_at[1]

    def _voiced(self, first, last):
        level, speech = self._levels()
        return not speech or sum(e > level for e in self.energy[first:last]) >= 3

    def _cut(self, first, last):
        """Frame where the words before it are settled, or None."""
        level, speech = self._levels()
        best, run, voiced = None, 0, 0
        for at in range(first if speech else last, last):
            if self.energy[at] > level:
                voiced += 1
                run = 0
                continue
            run += 1
            start = at - run + 1
            if run == PAUSE and voiced >= 10 and start - first >= MIN_HEAD:
                best = start + PAUSE // 2
        if best is None and last - first > LONG:
            # A long unbroken passage, or a quiet microphone where no pause can be
            # told apart: split where it is quietest, well inside it, so the live
            # words never mean reading the whole recording again.
            low, high = first + LONG // 2, last - 50
            sums = [sum(self.energy[at:at + 10]) for at in range(low, high)]
            best = low + sums.index(min(sums)) + 5
        return best

    def _note(self, name, failures):
        self.recognizer = name or self.recognizer
        self.failures = failures

    def _join_text(self, tail=''):
        self.tail = tail
        # A run-on repeat can straddle two pieces heard separately.
        self.raw = collapse_loops(' '.join(self.done_text + ([tail] if tail else []))) or None

    async def _hear(self, begin, end):
        before = time.monotonic()
        text, name, failures = await asyncio.to_thread(
            hear, self.pipeline.recognition, wav(bytes(self.pcm[begin:end])), self.words)
        took = time.monotonic() - before
        self.timing['recognition_calls'] += 1
        self.timing['slowest_recognition'] = max(self.timing['slowest_recognition'], round(took, 3))
        # Read more often only while reading is quick, so a slow model is never flooded.
        self.step = QUICK_STEP if took < QUICK else STEP
        self._note(name, failures)
        return text

    async def _decode(self, size, final=False):
        try:
            cut = None if final else self._cut(self.done // FRAME, size // FRAME)
            if cut is not None:
                edge = cut * FRAME
                near, far = sorted((self.decoded // FRAME, cut))
                if self.decoded > self.done and not any(
                        e > self._levels()[0] for e in self.energy[near:far]):
                    head = self.tail   # already heard; only quiet lies between
                else:
                    head = await self._hear(self.done, edge)
                if head:
                    self.done_text.append(head)
                self.done = edge
                self._join_text()
            if self.done == size or (self.done_text and not self._voiced(self.done // FRAME, size // FRAME)):
                tail = ''   # only quiet follows the settled words
            else:
                tail = await self._hear(self.done, size)
            self._join_text(tail)
            self.decoded = size
        except AllFailed as error:
            self.failures = error.failures
            return
        if not final:
            # The same words twice in a row: nothing new was said in between.
            unchanged = bool(self.raw) and self.raw == self.heard_before
            self.heard_before = self.raw
            self._start_draft(size, unchanged)

    def _start_draft(self, size, unchanged=False):
        last = size // FRAME
        if not self.writing or not self.raw:
            return
        if len(self.raw.split()) > LONG_WORDS:
            self._draft_pieces()
            return   # a long dictation is never written in one request
        if self.pipeline.quick_plain and plain_words(self.raw, self.words):
            return   # these words need no rewriting, so no writing request is spent
        if not unchanged and (last < TRAIL or not self._levels()[1]
                              or self._voiced(last - TRAIL, last)
                              or self._voiced(self.decoded // FRAME, last)):
            return   # still speaking, or words not recognized yet
        if self.draft and (self.draft[0] == self.raw or not self.draft[1].done()):
            return   # one local writing request at a time
        task = asyncio.create_task(asyncio.to_thread(
            first_success, self.writing, self.raw, self.words, False))
        task.add_done_callback(lambda done: done.cancelled() or done.exception())
        self.draft = (self.raw, task)

    def _draft_pieces(self):
        """Write settled pieces of a long dictation during speech, one at a
        time, so finishing only has to write the last piece."""
        if any(not task.done() for task in self.piece_drafts.values()):
            return
        for piece in pieces(' '.join(self.done_text))[:-1]:
            if piece not in self.piece_drafts:
                task = asyncio.create_task(asyncio.to_thread(
                    first_success, self.writing, piece, self.words, False))
                task.add_done_callback(lambda done: done.cancelled() or done.exception())
                self.piece_drafts[piece] = task
                return

    async def pieces_written(self):
        """{piece words: (text, writer, failures)} for pieces written during speech."""
        ready = {}
        for piece, task in list(self.piece_drafts.items()):
            try:
                ready[piece] = await asyncio.shield(task)
            except AllFailed:
                pass   # the normal writing path tries this piece again
        return ready

    async def feed(self, chunk):
        if len(self.pcm) + len(chunk) + 44 > MAX_AUDIO:
            raise ValueError('The recording is too long. The phone copy is kept.')
        self.pcm.extend(chunk)
        self._measure()
        if self.task and self.task.done():
            await self.task
            self.task = None
        if self.task is None:
            fresh = len(self.pcm) - self.decoded
            first, last = self.decoded // FRAME, len(self.pcm) // FRAME
            # First partial after one second; update at most every second of new
            # speech, and at once when the speaker pauses. Quiet alone is not reread.
            if ((fresh >= self.step and (not self.raw or self._voiced(first, last)))
                    or (fresh >= PAUSE * FRAME and self._levels()[1]
                        and not self._voiced(last - PAUSE, last)
                        and self._voiced(first, last - PAUSE))):
                self.task = asyncio.create_task(self._decode(len(self.pcm) // 2 * 2))
            else:
                self._start_draft(len(self.pcm))

    async def join(self):
        if self.task:
            # Never leave model inference running after the request has ended.
            try:
                await asyncio.shield(self.task)
            except asyncio.CancelledError:
                await self.task
                raise
            finally:
                self.task = None

    async def finish(self):
        await self.join()
        if not self.pcm or len(self.pcm) % 2:
            raise ValueError('The recording has incomplete audio samples.')
        size = len(self.pcm)
        if self.decoded != size:
            if (self.raw and self._levels()[1]
                    and not self._voiced(self.decoded // FRAME, size // FRAME)):
                # Only quiet followed the recognized words: they are already final.
                self.decoded = size
            else:
                # The final tail must be recognized; a stale partial is never final text.
                self.task = asyncio.create_task(self._decode(size, final=True))
                await self.join()
        if self.decoded != size or not self.raw:
            raise AllFailed(self.failures)
        return self.raw

    async def written(self, raw):
        """The draft's result when it cleaned exactly these final words, else None."""
        if not self.draft or self.draft[0] != raw:
            return None
        try:
            return await asyncio.shield(self.draft[1])
        except AllFailed:
            return None   # the normal writing path decides, including any allowed backup

    def release(self):
        """Hand back writing requests that are still running, for the service to join."""
        draft, self.draft = self.draft, None
        running = [task for task in self.piece_drafts.values() if not task.done()]
        self.piece_drafts = {}
        if draft and not draft[1].done():
            running.append(draft[1])
        return running
