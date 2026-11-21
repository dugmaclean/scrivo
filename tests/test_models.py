"""The three writing picks: the list, saving them on the server, and really using them."""
import io
import json
import os
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from aiohttp.test_utils import TestClient, TestServer

import providers
from dictation import Pipeline, Store, Tier
from server import make_app, saved_models

KEY = 'k' * 32
AUTH = {'Authorization': 'Bearer ' + KEY}
def wav():
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b'\x00\x00' * 1600)
    return out.getvalue()


class SavedPickTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'db')
        self.used = []
        self.broken = set()

        def tier(name, cloud):
            def write(text, words):
                self.used.append(name)
                if name in self.broken:
                    raise RuntimeError('private failure')
                return f'{text} (written by {name})'      # a tidy-up keeps what was said
            return Tier(name, write, cloud=cloud)
        self.rows = [
            {'key': 'home', 'label': 'Local model', 'group': 'Home', 'cloud': False, 'available': True, 'why_not': '', 'notice': 'n'},
            {'key': 'anthropic:haiku', 'label': 'Claude Haiku 5.5', 'group': 'Claude', 'cloud': True, 'available': True, 'why_not': '', 'notice': 'n'},
            {'key': 'openai:luna', 'label': 'GPT-6 Luna', 'group': 'OpenAI', 'cloud': True, 'available': True, 'why_not': '', 'notice': 'n'},
            {'key': 'openai:reserve', 'label': 'GPT-Reserve', 'group': 'OpenAI', 'cloud': True, 'available': False, 'why_not': 'x', 'notice': 'n'},
        ]
        cloud = {row['key']: row['cloud'] for row in self.rows}
        # "um" keeps every sentence out of the quick finish, so a writer is always asked.
        self.pipeline = Pipeline(self.store, [Tier(str(i), lambda a, w: 'um hello there') for i in range(3)],
                                 [tier('old-home', False), tier('old-home-2', False), tier('old-haiku', True)],
                                 quick_plain=True)
        app = make_app(self.store, self.pipeline, KEY, model_list=lambda: self.rows,
                       model_chain=lambda picks: [tier(k, cloud[k]) for k in picks if k and cloud.get(k) is not None
                                                  and k != 'openai:reserve'])
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def pick(self, chosen, notice=None):
        body = {'chosen': chosen}
        if notice is not None:
            body['cloud_notice_shown'] = notice
        return await self.client.post('/v1/models', json=body, headers=AUTH)

    async def dictate(self, query='?writer=saved', route='/v1/dictate'):
        self.used.clear()
        response = await self.client.post(route + query, data=wav() if route == '/v1/dictate' else b'\0' * 32000, headers=AUTH)
        return response.status, await response.json()

    async def test_list_needs_the_private_key_and_starts_at_home_only(self):
        self.assertEqual((await self.client.get('/v1/models')).status, 401)
        body = await (await self.client.get('/v1/models', headers=AUTH)).json()
        self.assertEqual([s['slot'] for s in body['slots']], ['Primary', 'Fallback 1', 'Fallback 2'])
        self.assertEqual([s['key'] for s in body['slots']], ['home', None, None])
        self.assertEqual(body['slots'][0]['label'], 'Local model')
        self.assertEqual(len(body['models']), 4)
        status, result = await self.dictate()
        self.assertEqual((status, result['writer'], self.used), (200, 'home', ['home']))

    async def test_cloud_pick_needs_its_notice_and_bad_picks_are_refused(self):
        self.assertEqual((await self.pick(['anthropic:haiku', None, None])).status, 400)
        self.assertEqual((await self.pick(['anthropic:haiku', None, None], False)).status, 400)
        self.assertEqual((await self.pick([None, 'home', None])).status, 400)
        self.assertEqual((await self.pick(['home', 'home', None])).status, 400)
        self.assertEqual((await self.pick(['openai:reserve', None, None], True)).status, 400)
        self.assertEqual((await self.pick(['made-up', None, None], True)).status, 400)
        self.assertEqual((await self.pick(['home', None])).status, 400)
        self.assertEqual(saved_models(self.store), ['home', None, None])
        response = await self.pick(['anthropic:haiku', 'home', 'openai:luna'], True)
        self.assertEqual(response.status, 200)
        self.assertEqual([s['key'] for s in (await response.json())['slots']], ['anthropic:haiku', 'home', 'openai:luna'])
        self.assertEqual(saved_models(Store(Path(self.tmp.name) / 'db')), ['anthropic:haiku', 'home', 'openai:luna'])

    async def test_a_different_primary_really_writes_the_text(self):
        await self.pick(['openai:luna', 'home', 'anthropic:haiku'], True)
        status, result = await self.dictate()
        self.assertEqual((status, result['text'], result['writer']), (200, 'um hello there (written by openai:luna)', 'openai:luna'))
        await self.pick(['anthropic:haiku', 'home', 'openai:luna'], True)
        status, result = await self.dictate()
        self.assertEqual((result['text'], self.used), ('um hello there (written by anthropic:haiku)', ['anthropic:haiku']))

    async def test_failing_primary_falls_to_first_then_second_backup_with_recording_kept(self):
        await self.pick(['openai:luna', 'anthropic:haiku', 'home'], True)
        self.broken = {'openai:luna'}
        status, result = await self.dictate()
        self.assertEqual((result['writer'], self.used), ('anthropic:haiku', ['openai:luna', 'anthropic:haiku']))
        self.assertEqual(result['writing_failures'], [{'model': 'openai:luna', 'reason': 'unavailable'}])
        self.broken = {'openai:luna', 'anthropic:haiku'}
        status, result = await self.dictate()
        self.assertEqual((result['writer'], self.used), ('home', ['openai:luna', 'anthropic:haiku', 'home']))
        self.assertNotIn('private failure', json.dumps(result))
        self.broken = {'openai:luna', 'anthropic:haiku', 'home'}
        status, result = await self.dictate()
        self.assertEqual((status, result['status'], result['text']), (200, 'raw', 'um hello there'))
        self.assertTrue(self.store.get(result['id'])['audio'])

    async def test_streamed_dictation_uses_the_picks_and_only_home_starts_early(self):
        await self.pick(['anthropic:haiku', 'home', None], True)
        status, result = await self.dictate(route='/v1/stream')
        self.assertEqual((status, result['writer'], self.used), (200, 'anthropic:haiku', ['anthropic:haiku']))
        await self.pick(['home', 'anthropic:haiku', None], True)
        self.broken = {'home'}
        status, result = await self.dictate(route='/v1/stream')
        self.assertEqual(result['writer'], 'anthropic:haiku')
        self.assertTrue(self.store.get(result['id'])['audio'])

    async def test_settings_can_say_which_model_wrote_the_last_dictation(self):
        body = await (await self.client.get('/v1/models', headers=AUTH)).json()
        self.assertIsNone(body['last'])
        await self.pick(['openai:luna', 'anthropic:haiku', None], True)
        self.broken = {'openai:luna'}
        await self.dictate()
        last = (await (await self.client.get('/v1/models', headers=AUTH)).json())['last']
        self.assertEqual((last['writer'], last['writer_label'], last['failed'], last['status']),
                         ('anthropic:haiku', 'Claude Haiku 5.5', ['GPT-6 Luna'], 'ready'))
        self.assertNotIn('hello', json.dumps(last))
        await self.dictate(route='/v1/stream')
        last = (await (await self.client.get('/v1/models', headers=AUTH)).json())['last']
        self.assertEqual(last['writer'], 'anthropic:haiku')
        self.broken = {'openai:luna', 'anthropic:haiku'}
        await self.dictate()
        last = (await (await self.client.get('/v1/models', headers=AUTH)).json())['last']
        self.assertEqual((last['writer'], last['status'], last['failed']), (None, 'raw', ['GPT-6 Luna', 'Claude Haiku 5.5']))

    async def test_an_older_phone_never_reaches_the_saved_cloud_picks(self):
        await self.pick(['anthropic:haiku', 'openai:luna', None], True)
        status, result = await self.dictate(query='')
        self.assertEqual((status, result['writer'], self.used), (200, 'old-home', ['old-home']))
        self.broken = {'old-home', 'old-home-2'}
        status, result = await self.dictate(query='')
        self.assertEqual((result['status'], result['writer']), ('raw', None))
        self.assertNotIn('old-haiku', self.used)

    async def test_the_test_button_names_the_model_that_wrote(self):
        await self.pick(['openai:luna', 'home', None], True)
        self.broken = {'openai:luna'}
        body = await (await self.client.post('/v1/models/test', headers=AUTH)).json()
        self.assertEqual((body['writer'], body['writer_label'], body['failed']),
                         ('home', 'Local model', ['GPT-6 Luna']))
        self.broken = {'openai:luna', 'home'}
        body = await (await self.client.post('/v1/models/test', headers=AUTH)).json()
        self.assertEqual((body['text'], body['writer'], body['failed']), (None, None, ['GPT-6 Luna', 'Local model']))


if __name__ == '__main__':
    unittest.main()
