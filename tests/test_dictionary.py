import json
import unittest
import test_dictation as fixtures
from dictation import import_entries, Pipeline, Tier


class DictionaryTests(unittest.TestCase):
    setUp = fixtures.CoreTests.setUp
    def test_lossless_import_keeps_variants_shortcuts_and_recordings(self):
        entries = [
            {'phrase': 'pick up', 'replacement': 'PickUp', 'is_snippet': False},
            {'phrase': 'Pick up', 'replacement': 'PickUp', 'is_snippet': False},
            {'phrase': 'Robin', 'replacement': None, 'is_snippet': False},
            {'phrase': 'my signoff', 'replacement': 'Regards,\nAlex', 'is_snippet': True},
        ]
        self.store.add_words(['Existing'])
        parsed = import_entries(json.dumps({'entries': entries}), 'json')
        self.store.add_entries(parsed)
        self.store.add_entries(parsed)
        self.assertCountEqual(self.store.entries(), entries)
        self.assertIn('Existing', self.store.words())
        self.assertIn('PickUp', self.store.words())
        self.assertEqual(self.store.get(self.id)['audio'], fixtures.wav())
        self.store = type(self.store)(self.store.path)
        self.assertCountEqual(self.store.entries(), entries)

    def test_replacements_apply_without_recursion_or_matching_inside_words(self):
        self.store.add_entries(import_entries(json.dumps({'entries': [
            {'phrase': 'pick up', 'replacement': 'PickUp', 'is_snippet': False},
            {'phrase': 'my signoff', 'replacement': 'Regards,\nAlex', 'is_snippet': True},
        ]}), 'json'))
        pipe = Pipeline(self.store, [Tier(str(i), lambda a, w: 'I pick up. Pickupable. My signoff') for i in range(3)],
                        [Tier(str(i), lambda t, w: t) for i in range(3)])
        self.assertEqual(pipe.process(self.id)['text'], 'I PickUp. Pickupable. Regards,\nAlex')

    def test_bad_structured_import_is_rejected_before_any_write(self):
        for entry in [{'phrase': 'x', 'replacement': 12, 'is_snippet': False},
                      {'phrase': 'x', 'replacement': 'ok', 'is_snippet': 'false'},
                      {'phrase': '', 'replacement': 'ok', 'is_snippet': False}]:
            with self.assertRaises(ValueError):
                import_entries(json.dumps({'entries': [entry]}), 'json')


class DictionaryHttpTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.HttpTests.asyncSetUp
    asyncTearDown = fixtures.HttpTests.asyncTearDown
    async def test_structured_http_import_returns_complete_entries(self):
        entries = [{'phrase': 'pick up', 'replacement': 'PickUp', 'is_snippet': False}]
        r = await self.client.post('/v1/dictionary/import',
            json={'format': 'json', 'data': json.dumps({'entries': entries})}, headers=self.headers)
        self.assertEqual(r.status, 200)
        self.assertEqual((await r.json())['entries'], entries)
        r = await self.client.get('/v1/dictionary', headers=self.headers)
        self.assertEqual((await r.json())['entries'], entries)
