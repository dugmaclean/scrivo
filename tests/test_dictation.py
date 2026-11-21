import asyncio
import io
import json
import tempfile
import unittest
import uuid
import wave
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from dictation import AllFailed, Pipeline, Store, Tier, import_words, validate_audio
from server import make_app


def wav():
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b'\x00\x00' * 1600)
    return out.getvalue()


def fail(value, words):
    raise RuntimeError('private provider error must never appear')


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'db')
        self.id = uuid.uuid4().hex
        self.store.save(self.id, wav())

    def test_two_failures_reuse_original_audio(self):
        heard = []
        def recognize(audio, words):
            heard.append(audio)
            return 'Alex said hello'
        p = Pipeline(self.store, [Tier('first', fail), Tier('second', fail), Tier('third', recognize)],
                     [Tier('one', fail), Tier('two', fail), Tier('three', lambda text, words: text + '.')])
        result = p.process(self.id)
        self.assertEqual(heard, [wav()])
        self.assertEqual(result['text'], 'Alex said hello.')
        self.assertEqual(len(result['recognition_failures']), 2)
        self.assertEqual(len(result['writing_failures']), 2)

    def test_all_cleanup_failures_preserve_recognized_words(self):
        p = Pipeline(self.store, [Tier(str(i), lambda a, w: 'Keep these words') for i in range(3)],
                     [Tier(str(i), fail) for i in range(3)])
        result = p.process(self.id)
        self.assertEqual(result['text'], 'Keep these words')
        self.assertEqual(result['status'], 'raw')
        self.assertEqual(self.store.get(self.id)['raw'], 'Keep these words')

    def test_no_cloud_without_explicit_permission(self):
        called = []
        p = Pipeline(self.store, [Tier(str(i), lambda a, w: 'local') for i in range(3)],
                     [Tier('cloud', lambda t, w: called.append(t), cloud=True), Tier('b', fail), Tier('c', fail)])
        self.assertEqual(p.process(self.id)['text'], 'local')
        self.assertEqual(called, [])

    def test_failed_recognition_retains_audio_for_retry(self):
        p = Pipeline(self.store, [Tier(str(i), fail) for i in range(3)], [Tier(str(i), fail) for i in range(3)])
        with self.assertRaises(AllFailed):
            p.process(self.id)
        self.assertEqual(self.store.get(self.id)['audio'], wav())
        self.assertEqual(self.store.get(self.id)['status'], 'pending')

    def test_writing_retry_keeps_saved_recognition_when_recognizers_go_offline(self):
        p = Pipeline(self.store, [Tier(str(i), lambda a, w: 'Send it Tuesday') for i in range(3)],
                     [Tier(str(i), fail) for i in range(3)])
        self.assertEqual(p.process(self.id)['status'], 'raw')
        p.recognition = [Tier(str(i), fail) for i in range(3)]
        p.writing[2] = Tier('recovered-writer', lambda text, words: text + '.')
        self.assertEqual(p.process(self.id)['text'], 'Send it Tuesday.')
        self.assertEqual(self.store.get(self.id)['raw'], 'Send it Tuesday')

    def test_dictionary_import_deduplicates_without_losing_existing(self):
        self.store.add_words(['Alex'])
        self.store.add_words(import_words('word\nRobin\nrobin\n', 'csv'))
        self.assertEqual(self.store.words(), ['Alex', 'Robin'])
        self.assertEqual(import_words('{"words":["Haiku","Alex"]}', 'json'), ['Haiku', 'Alex'])

    def test_malformed_dictionary_and_audio_rejected(self):
        for data in ['{}', '[42]', '{"words":"bad"}']:
            with self.assertRaises(ValueError):
                import_words(data, 'json')
        with self.assertRaises(ValueError):
            validate_audio(b'not wav')
        validate_audio(wav())


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'db')
        self.pipe = Pipeline(self.store, [Tier(str(i), lambda a, w: 'Hello Alex') for i in range(3)],
                             [Tier(str(i), fail) for i in range(3)])
        self.client = TestClient(TestServer(make_app(self.store, self.pipe, 'x'*32)))
        await self.client.start_server()
        self.headers = {'Authorization': 'Bearer ' + 'x'*32}

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def test_auth_audio_recovery_and_import(self):
        r = await self.client.get('/v1/dictionary')
        self.assertEqual(r.status, 401)
        r = await self.client.post('/v1/dictate', data=wav(), headers=self.headers)
        self.assertEqual(r.status, 200)
        result = await r.json()
        self.assertEqual(result['text'], 'Hello Alex')
        self.assertNotIn('private provider', json.dumps(result))
        r = await self.client.get('/v1/recordings/' + result['id'], headers=self.headers)
        self.assertNotIn('audio', await r.json())
        r = await self.client.post('/v1/dictionary/import', json={'data': 'Robin\nHaiku'}, headers=self.headers)
        self.assertEqual((await r.json())['words'], ['Haiku', 'Robin'])

    async def test_cloud_warning_and_invalid_audio(self):
        r = await self.client.post('/v1/dictate?writer=haiku', data=wav(), headers=self.headers)
        self.assertEqual(r.status, 400)
        r = await self.client.post('/v1/dictate', data=b'broken', headers=self.headers)
        self.assertEqual(r.status, 400)

    async def test_recognition_failure_returns_retry_recording(self):
        self.pipe.recognition = [Tier(str(i), fail) for i in range(3)]
        r = await self.client.post('/v1/dictate', data=wav(), headers=self.headers)
        self.assertEqual(r.status, 503)
        result = await r.json()
        self.assertTrue(result['recording_saved'])
        self.pipe.recognition[2] = Tier('third', lambda a, w: 'Recovered words')
        r = await self.client.post('/v1/recordings/' + result['id'] + '/retry', headers=self.headers)
        self.assertEqual((await r.json())['text'], 'Recovered words')


if __name__ == '__main__':
    unittest.main()
