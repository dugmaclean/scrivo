"""Quick finish: graphics-card backup, plain sentences, unchanged-word pauses."""
import asyncio
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aiohttp.test_utils import TestClient, TestServer

import dictation
from dictation import Pipeline, Store, Tier, Unavailable, plain_words, recognition_tiers, whisper_tier
from server import make_app
from streaming import LiveRecognition

KEY = 'x' * 32


def room(seconds, loud, seed=1):
    """Noisy audio: a real room never gives the digital quiet the loudness rule needs."""
    rng = random.Random(seed)
    return b''.join(int(rng.gauss(0, loud)).to_bytes(2, 'little', signed=True)
                    for _ in range(int(16000 * seconds)))


class PlainWords(unittest.TestCase):
    def test_everyday_sentence_is_final_as_heard(self):
        text = 'Remind me to call the school about the field trip form before Wednesday.'
        self.assertEqual(plain_words(text), text)

    def test_clock_time_is_written_with_a_colon_and_weights_are_left_alone(self):
        self.assertEqual(plain_words('Dinner is at 6.30, so be home by 6.'), 'Dinner is at 6:30, so be home by 6.')
        self.assertEqual(plain_words('The part weighs 6.30 pounds.'), 'The part weighs 6.30 pounds.')

    def test_hesitation_correction_layout_stutter_and_length_go_to_the_writer(self):
        for text in ('Um, send it Monday.', 'Send it Monday, actually Tuesday.', 'Send it Monday no Tuesday.',
                     'Milk comma eggs comma bread.', 'Send the the report.', 'send it monday',
                     'I was just about to say', ' '.join(['Word'] * 46) + '.', ''):
            self.assertIsNone(plain_words(text), text)

    def test_saved_word_heard_in_another_spelling_goes_to_the_writer(self):
        saved = ['Quik Notes', 'Jon', 'Kenzie', 'Alex Rivera']
        self.assertIsNone(plain_words('Please send a message to quick notes tomorrow.', saved))
        self.assertIsNone(plain_words('Please send a message to John tomorrow.', saved))
        self.assertIsNone(plain_words('Please send a message to alex rivera tomorrow.', saved))

    def test_saved_word_already_spelled_right_is_still_final_as_heard(self):
        saved = ['Quik Notes', 'Jon', 'Kenzie', 'Alex Rivera']
        for text in ('Please send a message to Quik Notes tomorrow.', 'Tell Jon and Kenzie we are leaving.',
                     'Remind me to call the school about the field trip form before Wednesday.'):
            self.assertEqual(plain_words(text, saved), text)

    def test_pipeline_sends_a_misheard_saved_word_to_the_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'words.sqlite3')
            store.add_words(['Jon'])
            asked = []
            def write(raw, words):
                asked.append(list(words))
                return raw.replace('John', 'Jon')
            hear = Tier('hear', lambda audio, words: 'Please call John tomorrow.')
            down = Tier('down', lambda *a: (_ for _ in ()).throw(Unavailable()))
            pipeline = Pipeline(store, [hear, down, down], [Tier('write', write), down, down], quick_plain=True)
            store.save('a' * 32, b'audio')
            result = pipeline.process('a' * 32)
            self.assertEqual(result['text'], 'Please call Jon tomorrow.')
            self.assertEqual(result['writer'], 'write')
            self.assertEqual(asked, [['Jon']])


class GraphicsCardTier(unittest.TestCase):
    def test_tiers_use_the_card_first_only_when_its_libraries_are_present(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual([t.name for t in recognition_tiers(folder)], ['small.en', 'small', 'tiny.en'])
            Path(folder, 'libcublas.so.12').write_bytes(b'')
            self.assertEqual([t.name for t in recognition_tiers(folder)],
                             ['small.en-graphics-card', 'small.en', 'tiny.en'])
        self.assertEqual(len(recognition_tiers(None)), 3)

    def test_a_failed_card_hands_over_and_is_not_tried_again_for_a_minute(self):
        loads = []
        def broken(folder):
            loads.append(folder)
            raise OSError('card unavailable')
        audio = dictation.io.BytesIO()
        with dictation.wave.open(audio, 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b'\0' * 3200)
        with mock.patch.object(dictation, 'load_cuda_libraries', broken):
            card = whisper_tier('small.en', cuda='/nowhere')
            backup = Tier('backup', lambda a, w: 'Backup words.')
            for _ in range(3):
                self.assertEqual(dictation.first_success([card, backup], audio.getvalue(), [], False)[:2],
                                 ('Backup words.', 'backup'))
            self.assertEqual(loads, ['/nowhere'])   # one attempt, then straight to the backup
            with mock.patch.object(dictation.time, 'monotonic', return_value=dictation.time.monotonic() + 61):
                with self.assertRaises(OSError):
                    card.run(audio.getvalue(), [])
            self.assertEqual(len(loads), 2)

    def test_missing_libraries_are_refused(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(Unavailable):
            dictation.load_cuda_libraries(folder)


class QuickFinish(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'db')
        self.say = 'Send the report to the crew tomorrow.'
        self.wrote = []
        def write(text, words):
            self.wrote.append(text)
            return 'Written: ' + text
        self.pipeline = Pipeline(self.store, [Tier(str(i), lambda a, w: self.say) for i in range(3)],
                                 [Tier(str(i), write) for i in range(3)], quick_plain=True)

    async def post(self, body):
        client = TestClient(TestServer(make_app(self.store, self.pipeline, KEY)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        response = await client.post('/v1/stream', data=body, headers={'Authorization': 'Bearer ' + KEY})
        self.assertEqual(response.status, 200)
        return await response.json()

    async def test_plain_sentence_returns_without_any_writing_request(self):
        body = await self.post(room(2, 3000))
        self.assertEqual((body['text'], body['writer'], body['status']), (self.say, 'not-needed', 'ready'))
        self.assertEqual(self.wrote, [])
        self.assertEqual(self.store.get(body['id'])['text'], self.say)

    async def test_saved_words_are_still_applied_to_a_plain_sentence(self):
        self.store.add_entries([{'phrase': 'the crew', 'replacement': 'Angel and the crew', 'is_snippet': False}])
        body = await self.post(room(2, 3000))
        self.assertIn('Angel and the crew', body['text'])

    async def test_a_correction_still_goes_to_the_chosen_writer(self):
        self.say = 'Send it Monday, actually Tuesday.'
        body = await self.post(room(2, 3000))
        self.assertEqual(body['text'], 'Written: ' + self.say)
        self.assertEqual(self.wrote[-1], self.say)

    async def test_every_sentence_is_written_when_quick_finish_is_off(self):
        self.pipeline.quick_plain = False
        body = await self.post(room(2, 3000))
        self.assertEqual(body['text'], 'Written: ' + self.say)

    async def test_finish_times_are_numbers_only(self):
        body = await self.post(room(2, 3000))
        self.assertEqual(set(body['finish_seconds']),
                         {'recognition_calls', 'slowest_recognition', 'recognition', 'writing'})
        self.assertTrue(all(isinstance(v, (int, float)) for v in body['finish_seconds'].values()))

    async def test_unchanged_words_in_a_noisy_room_start_the_writing_draft(self):
        # Steady room noise: the loudness rule never sees a pause here.
        self.say = 'Send it Monday, actually Tuesday.'
        live = LiveRecognition(self.pipeline, self.pipeline.writing)
        noise = room(3, 3000)
        for at in range(0, len(noise), 6400):
            await live.feed(noise[at:at + 6400])
            await live.join()
        self.assertIsNotNone(live.draft, 'two identical readings must start home writing')
        self.assertEqual(live.draft[0], self.say)
        raw = await live.finish()
        self.assertEqual((await live.written(raw))[0], 'Written: ' + self.say)
        self.assertEqual(self.wrote, [self.say])   # one request, reused at the finish

    async def test_quick_readings_come_more_often_and_slow_ones_do_not(self):
        live = LiveRecognition(self.pipeline)
        await live.feed(room(1, 3000))
        await live.join()
        self.assertEqual(live.step, 12800)
        slow = LiveRecognition(Pipeline(self.store, [Tier(str(i), lambda a, w: (__import__('time').sleep(.3), 'Slow.')[1])
                                                     for i in range(3)], self.pipeline.writing))
        await slow.feed(room(1, 3000))
        await slow.join()
        self.assertEqual(slow.step, 32000)


if __name__ == '__main__':
    unittest.main()
