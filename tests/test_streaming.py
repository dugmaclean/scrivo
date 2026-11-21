import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from aiohttp.test_utils import TestClient, TestServer
from dictation import Pipeline, Store, Tier
from server import make_app
from streaming import LiveRecognition


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'db')
        self.heard = []
        def recognize(audio, words):
            self.heard.append(len(audio))
            return 'Heard ' + str(len(audio))
        self.pipeline = Pipeline(self.store, [Tier(str(i), recognize) for i in range(3)],
                                 [Tier(str(i), lambda text, words: text + '.') for i in range(3)])

    async def test_decode_starts_before_capture_finishes_and_final_tail_is_kept(self):
        live = LiveRecognition(self.pipeline)
        await live.feed(b'\0' * 32000)
        await live.join()
        self.assertEqual(self.heard, [32044])  # no finish call has happened
        await live.feed(b'\0' * 1000)
        self.assertEqual(await live.finish(), 'Heard 33044')
        self.assertEqual(self.heard, [32044, 33044])

    async def test_real_streaming_route_recognizes_before_request_eof(self):
        self.client = TestClient(TestServer(make_app(self.store, self.pipeline, 'x' * 32)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        async def audio():
            yield b'\0' * 32000
            for _ in range(100):
                if self.heard: break
                await asyncio.sleep(.005)
            self.assertTrue(self.heard, 'Recognition must run before upload ends')
            yield b'\0' * 1000
        response = await self.client.post('/v1/stream', data=audio(), headers={'Authorization': 'Bearer ' + 'x' * 32})
        self.assertEqual(response.status, 200)
        body = await response.json()
        self.assertEqual(body['text'], 'Heard 33044.')
        self.assertEqual(len(self.store.get(body['id'])['audio']), 33044)

    async def test_all_failures_keep_audio(self):
        def fail(a, w): raise RuntimeError('private')
        self.pipeline.recognition = [Tier(str(i), fail) for i in range(3)]
        client = TestClient(TestServer(make_app(self.store, self.pipeline, 'x' * 32)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        response = await client.post('/v1/stream', data=b'\0' * 32000, headers={'Authorization': 'Bearer ' + 'x' * 32})
        self.assertEqual(response.status, 503)
        body = await response.json()
        self.assertTrue(body['recording_saved'])
        self.assertEqual(len(self.store.get(body['id'])['audio']), 32044)

    async def test_cloud_requires_explicit_permission_before_audio(self):
        client = TestClient(TestServer(make_app(self.store, self.pipeline, 'x' * 32)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        response = await client.post('/v1/stream?writer=haiku', data=b'\0' * 32000, headers={'Authorization': 'Bearer ' + 'x' * 32})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.heard, [])


class LivePreviewTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = StreamingTests.asyncSetUp
    async def test_partial_is_readable_before_upload_finishes_and_removed_afterward(self):
        client = TestClient(TestServer(make_app(self.store, self.pipeline, 'x' * 32)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        headers = {'Authorization': 'Bearer ' + 'x' * 32}
        session = 'a' * 32
        release = asyncio.Event()
        async def audio():
            yield b'\0' * 32000
            await release.wait()
            yield b'\0' * 1000
        upload = asyncio.create_task(client.post('/v1/stream?session=' + session, data=audio(), headers=headers))
        try:
            for _ in range(100):
                if self.heard: break
                await asyncio.sleep(.005)
            self.assertTrue(self.heard)
            preview = await client.get('/v1/streams/' + session, headers=headers)
            self.assertEqual(preview.status, 200)
            self.assertEqual(preview.headers.get('Cache-Control'), 'no-store')
            self.assertEqual((await preview.json())['text'], 'Heard 32044')
            self.assertFalse(upload.done(), 'Partial must reach the phone while it is still recording')
            anonymous = await client.get('/v1/streams/' + session)
            self.assertEqual(anonymous.status, 401)
            # A second phone dictating meanwhile is served, not refused, and the
            # first phone's words stay its own.
            second = await client.post('/v1/stream?session=' + 'b'*32,
                data=b'\0'*16000, headers=headers)
            self.assertEqual(second.status, 200)
            self.assertEqual((await second.json())['text'], 'Heard 16044.')
            self.assertEqual((await client.get('/v1/streams/' + 'b'*32, headers=headers)).status, 404)
            self.assertFalse(upload.done())
            self.assertEqual((await (await client.get('/v1/streams/' + session, headers=headers)).json())['text'], 'Heard 32044')
        finally:
            release.set()
            response = await upload
        result = await response.json()
        self.assertEqual(result['text'], 'Heard 33044.')
        self.assertEqual((await client.get('/v1/streams/' + session, headers=headers)).status, 404)

    async def test_only_a_runaway_number_of_dictations_is_refused(self):
        import server
        client = TestClient(TestServer(make_app(self.store, self.pipeline, 'x' * 32)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        headers = {'Authorization': 'Bearer ' + 'x' * 32}
        release = asyncio.Event()
        async def audio():
            yield b'\0' * 32000
            await release.wait()
        open_now = [asyncio.create_task(client.post('/v1/stream?session=' + format(n, '032x'), data=audio(), headers=headers))
                    for n in range(server.AT_ONCE)]
        try:
            for _ in range(200):
                if len(self.heard) >= server.AT_ONCE: break
                await asyncio.sleep(.005)
            self.assertEqual(len(self.heard), server.AT_ONCE)
            extra = await client.post('/v1/stream', data=b'\0' * 32000, headers=headers)
            self.assertEqual(extra.status, 503)
        finally:
            release.set()
            replies = await asyncio.gather(*open_now)
        self.assertEqual([r.status for r in replies], [200] * server.AT_ONCE)

    async def test_bad_session_is_refused_before_recognition(self):
        client = TestClient(TestServer(make_app(self.store, self.pipeline, 'x' * 32)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        response = await client.post('/v1/stream?session=personal-words', data=b'\0'*32000,
            headers={'Authorization': 'Bearer ' + 'x'*32})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.heard, [])

    async def test_failed_recognition_removes_preview_but_retains_audio(self):
        def fail(audio, words):
            raise RuntimeError('Synthetic failure')
        self.pipeline.recognition = [Tier(str(i), fail) for i in range(3)]
        client = TestClient(TestServer(make_app(self.store, self.pipeline, 'x'*32)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        headers = {'Authorization': 'Bearer ' + 'x'*32}
        session = 'c'*32
        reply = await client.post('/v1/stream?session=' + session,
            data=b'\0'*32000, headers=headers)
        self.assertEqual(reply.status, 503)
        result = await reply.json()
        self.assertTrue(result['recording_saved'])
        self.assertEqual(len(self.store.get(result['id'])['audio']), 32044)
        self.assertEqual((await client.get('/v1/streams/' + session, headers=headers)).status, 404)


LOUD = (b'\x88\x13\x78\xec') * 8000   # one second of a loud square wave
QUIET = b'\0' * 32000                 # one second of silence


class PauseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = StreamingTests.asyncSetUp

    async def say(self, live, *seconds):
        for second in seconds:
            await live.feed(second)
            await live.join()

    async def test_words_before_a_pause_are_recognized_once_and_finish_reads_only_the_tail(self):
        live = LiveRecognition(self.pipeline)
        await self.say(live, LOUD, LOUD, LOUD, QUIET, QUIET, QUIET)
        spoken = 3 * 32000 + 44
        self.assertEqual(self.heard, [32044, 64044, spoken], 'Quiet alone is never read again')
        await self.say(live, LOUD, LOUD)
        await live.feed(LOUD[:16000])
        text = await live.finish()
        settled = 3 * 32000 + 10 * 640            # speech plus a fifth of a second of the pause
        tail = len(live.pcm) - settled + 44
        self.assertEqual(text, 'Heard %d Heard %d' % (spoken, tail))
        self.assertEqual(self.heard[-1], tail)
        self.assertLess(tail, 6 * 32000)
        self.assertEqual(self.heard.count(spoken), 1, 'Words before the pause are recognized once')
        self.assertFalse([size for size in self.heard if size > 6 * 32000],
                         'Finishing must not read the whole recording again')

    async def test_quiet_after_the_last_words_needs_no_final_recognition(self):
        live = LiveRecognition(self.pipeline)
        await self.say(live, LOUD, LOUD, LOUD, QUIET)
        heard = list(self.heard)
        self.assertEqual(heard[-1], 3 * 32000 + 44)
        await live.feed(QUIET[:16000])
        self.assertEqual(await live.finish(), live.raw)
        self.assertEqual(self.heard, heard)

    async def test_a_model_hearing_nothing_in_one_piece_is_not_a_failure(self):
        calls = []
        def recognize(audio, words):
            calls.append(len(audio))
            return 'Words' if len(audio) >= 3 * 32000 else ''   # nothing in the short last piece
        self.pipeline.recognition = [Tier(str(i), recognize) for i in range(3)]
        live = LiveRecognition(self.pipeline)
        await self.say(live, LOUD, LOUD, LOUD, QUIET, LOUD)
        self.assertEqual(await live.finish(), 'Words')

    async def post(self, audio, writing):
        self.pipeline.writing = writing
        client = TestClient(TestServer(make_app(self.store, self.pipeline, 'x' * 32)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        response = await client.post('/v1/stream', data=audio, headers={'Authorization': 'Bearer ' + 'x' * 32})
        self.assertEqual(response.status, 200)
        return await response.json()

    async def test_writing_started_at_a_pause_is_used_when_the_words_did_not_change(self):
        wrote = []
        def write(text, words):
            wrote.append(text)
            return text + '.'
        async def audio():
            for second in (LOUD, LOUD, LOUD, QUIET, QUIET[:16000]):
                yield second          # a phone keeps sending audio as it records
                await asyncio.sleep(.05)
            for _ in range(400):
                if wrote: break
                await asyncio.sleep(.005)
            self.assertTrue(wrote, 'Home writing must begin while the upload is still open')
            yield QUIET[:16000]
        body = await self.post(audio(), [Tier(str(i), write) for i in range(3)])
        self.assertEqual(len(wrote), 1)
        self.assertEqual(body['text'], body['raw'] + '.')
        self.assertTrue(body['writing_started_during_speech'])
        self.assertEqual(self.store.get(body['id'])['status'], 'ready')

    async def test_a_draft_is_discarded_when_more_words_follow(self):
        wrote = []
        def write(text, words):
            wrote.append(text)
            return text + '.'
        async def audio():
            for second in (LOUD, LOUD, LOUD, QUIET, QUIET[:16000]):
                yield second          # a phone keeps sending audio as it records
                await asyncio.sleep(.05)
            for _ in range(400):
                if wrote: break
                await asyncio.sleep(.005)
            yield LOUD
            yield LOUD
        body = await self.post(audio(), [Tier(str(i), write) for i in range(3)])
        self.assertEqual(wrote[-1], body['raw'])
        self.assertEqual(body['text'], body['raw'] + '.')
        self.assertNotIn('writing_started_during_speech', body)
        self.assertEqual(len(wrote), 2)

    async def test_cloud_choice_is_written_once_after_the_final_words(self):
        wrote = []
        def write(text, words):
            wrote.append(text)
            return text + '.'
        self.pipeline.writing = [Tier('a', write), Tier('b', write), Tier('c', write, cloud=True)]
        client = TestClient(TestServer(make_app(self.store, self.pipeline, 'x' * 32)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        async def audio():
            for second in (LOUD, LOUD, LOUD, QUIET, QUIET):
                yield second
                await asyncio.sleep(.02)
        response = await client.post('/v1/stream?writer=haiku', data=audio(),
            headers={'Authorization': 'Bearer ' + 'x' * 32, 'X-Allow-Cloud': 'true'})
        self.assertEqual(response.status, 200)
        self.assertEqual(len(wrote), 1)
