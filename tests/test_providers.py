"""Writing models come from the settings file and speak the OpenAI chat format."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import providers
from dictation import Unavailable

CONFIG = {'writers': [
    {'key': 'home', 'label': 'Local model', 'base_url': 'http://127.0.0.1:11434/v1', 'model': 'small-model'},
    {'key': 'home-2', 'label': 'Second local model', 'base_url': 'http://127.0.0.1:8080/v1', 'model': 'other'},
    {'key': 'cloud', 'label': 'Cloud model', 'base_url': 'https://api.example.com/v1', 'model': 'cloud-small',
     'api_key_env': 'SCRIVO_TEST_KEY', 'cloud': True, 'company': 'Example'},
]}


class Reply(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def answering(text, seen):
    def opener(request, timeout):
        seen.append({'url': request.full_url, 'headers': dict(request.header_items()),
                     'body': json.loads(request.data), 'timeout': timeout})
        return Reply(json.dumps({'choices': [{'message': {'content': text}}]}).encode())
    return opener


class SettingsFile(unittest.TestCase):
    def test_missing_file_means_one_local_model(self):
        rows = providers.models(providers.load_config('/nonexistent/scrivo.json'))
        self.assertEqual([(r['key'], r['cloud']) for r in rows], [('home', False)])

    def test_bad_entries_are_refused_with_a_plain_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'scrivo.json'
            path.write_text(json.dumps({'writers': [{'key': 'a', 'base_url': 'http://x/v1'}]}))
            with self.assertRaisesRegex(ValueError, 'base_url'):
                providers.load_config(path)
            path.write_text(json.dumps({'writers': [CONFIG['writers'][0], CONFIG['writers'][0]]}))
            with self.assertRaisesRegex(ValueError, 'twice'):
                providers.load_config(path)

    def test_the_example_settings_file_loads(self):
        example = Path(__file__).resolve().parent.parent / 'scrivo.example.json'
        self.assertTrue(providers.load_config(example)['writers'])


class Writing(unittest.TestCase):
    def test_local_writer_sends_the_words_between_markers_with_no_key(self):
        seen = []
        tier = providers.openai_writer(CONFIG['writers'][0], opener=answering('Hello there.', seen))
        self.assertFalse(tier.cloud)
        self.assertEqual(tier.run('um hello there', ['Sam']), 'Hello there.')
        self.assertEqual(seen[0]['url'], 'http://127.0.0.1:11434/v1/chat/completions')
        self.assertNotIn('Authorization', seen[0]['headers'])
        self.assertIn('["Sam"]', seen[0]['body']['messages'][0]['content'])
        self.assertEqual(seen[0]['body']['messages'][1]['content'],
                         providers.BEGIN + '\num hello there\n' + providers.END)
        self.assertEqual(seen[0]['body']['temperature'], 0)

    def test_cloud_writer_needs_its_key_from_the_environment(self):
        seen = []
        tier = providers.openai_writer(CONFIG['writers'][2], opener=answering('Hi.', seen))
        self.assertTrue(tier.cloud)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SCRIVO_TEST_KEY', None)
            with self.assertRaises(Unavailable):
                tier.run('hi', [])
            self.assertEqual(seen, [])                          # nothing was sent without a key
        with patch.dict(os.environ, SCRIVO_TEST_KEY='secret-value'):
            self.assertEqual(tier.run('hi', []), 'Hi.')
        self.assertEqual(seen[0]['headers']['Authorization'], 'Bearer secret-value')

    def test_failures_and_empty_answers_hand_over(self):
        def down(request, timeout):
            raise OSError('connection refused')
        with self.assertRaises(Unavailable):
            providers.openai_writer(CONFIG['writers'][0], opener=down).run('hi', [])
        with self.assertRaises(Unavailable):
            providers.openai_writer(CONFIG['writers'][0], opener=answering('  ', [])).run('hi', [])


class Picks(unittest.TestCase):
    def test_local_models_first_and_cloud_has_its_notice(self):
        with patch.dict(os.environ, SCRIVO_TEST_KEY='k'):
            rows = providers.models(CONFIG)
        self.assertEqual([r['key'] for r in rows], ['home', 'home-2', 'cloud'])
        self.assertIn('Example', rows[2]['notice'])
        self.assertTrue(rows[2]['available'])

    def test_a_cloud_model_without_its_key_is_listed_but_cannot_be_used(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SCRIVO_TEST_KEY', None)
            rows = providers.models(CONFIG)
            self.assertFalse(rows[2]['available'])
            self.assertEqual([t.name for t in providers.chain(['cloud', 'home'], CONFIG)], ['home'])

    def test_picks_become_ordered_tiers_without_repeats(self):
        with patch.dict(os.environ, SCRIVO_TEST_KEY='k'):
            tiers = providers.chain(['cloud', None, 'home', 'unknown', 'cloud'], CONFIG)
        self.assertEqual([(t.name, t.cloud) for t in tiers], [('cloud', True), ('home', False)])

    def test_the_default_chain_is_the_first_three_writers(self):
        self.assertEqual([t.name for t in providers.writers(CONFIG)], ['home', 'home-2', 'cloud'])

    def test_one_local_writer_and_one_speech_model_are_enough_to_start(self):
        import tempfile
        from dictation import Pipeline, Store, Tier
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'd.sqlite3')
            Pipeline(store, [Tier('ears', lambda a, w: 'hi')], providers.writers(providers.DEFAULTS))
            with self.assertRaises(ValueError):
                Pipeline(store, [], providers.writers(providers.DEFAULTS))
            with self.assertRaises(ValueError):
                Pipeline(store, [Tier(str(i), lambda a, w: 'hi') for i in range(4)], providers.writers(CONFIG))


if __name__ == '__main__':
    unittest.main()
