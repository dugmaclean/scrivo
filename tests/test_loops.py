import unittest
import dictation


class RunOnRepeats(unittest.TestCase):
    def test_saved_name_loop(self):
        raw = ("Please carry the boxes over to... zentoria, zentoria, zentoria, zentoria, "
               "zentoria, and label them for shipping.")
        self.assertTrue(dictation.looped(raw))
        self.assertEqual(dictation.collapse_loops(raw),
                         "Please carry the boxes over to... zentoria, and label them for shipping.")

    def test_phrase_loop_keeps_one(self):
        self.assertEqual(dictation.collapse_loops("Send it. Send it. Send it. Thanks."), "Send it. Thanks.")

    def test_ordinary_speech_untouched(self):
        for text in ("that is very very good", "Please send a message to Alex tomorrow.",
                     "Call Alex, then call Alex Rivera."):
            self.assertFalse(dictation.looped(text))
            self.assertEqual(dictation.collapse_loops(text), text)

    def test_comma_stutter_is_not_plain(self):
        self.assertIsNone(dictation.plain_words("Call zentoria, zentoria now."))

    def test_lowercase_sentence_starts(self):
        raw = ("Okay, I hear the bakery opens at eight today. we're ready to pick up the order. "
               "what time do you think you can meet us there?")
        self.assertEqual(dictation.plain_words(raw),
                         "Okay, I hear the bakery opens at eight today. We're ready to pick up the order. "
                         "What time do you think you can meet us there?")
        self.assertEqual(dictation.plain_words("Why is the game moved to tomorrow? what is the reason for waiting?"),
                         "Why is the game moved to tomorrow? What is the reason for waiting?")

    def test_abbreviations_are_not_sentence_ends(self):
        for text in ("Meet at 9 a.m. tomorrow.", "Bring tools, e.g. the wrench.", "Ask Dr. smith today.",
                     "Wait and... then go.", "The bag weighs 6.30 pounds."):
            self.assertEqual(dictation.sentence_starts(text), text)

    def test_spoken_restart_goes_to_the_writer(self):
        self.assertIsNone(dictation.plain_words(
            "And then the delivery should be, driver should be there about 10 o'clock."))
        self.assertIsNone(dictation.plain_words("We need to buy, we need to buy four more chairs."))
        self.assertEqual(dictation.plain_words("Send it today. Send the rest tomorrow."),
                         "Send it today. Send the rest tomorrow.")

    def test_unwritten_words_still_get_sentence_starts(self):
        import tempfile
        from pathlib import Path

        def broken(value, words):
            raise dictation.Unavailable()

        tiers = [dictation.Tier(f'w{n}', broken) for n in range(3)]
        heard = [dictation.Tier(f'r{n}', lambda audio, words: 'call him today. then send it. then send it. then send it.')
                 for n in range(3)]
        with tempfile.TemporaryDirectory() as folder:
            store = dictation.Store(Path(folder) / 'd.sqlite3')
            store.save('x', b'RIFF')
            result = dictation.Pipeline(store, heard, tiers).process('x')
        self.assertEqual(result['status'], 'raw')
        self.assertEqual(result['text'], 'call him today. Then send it.')
        self.assertEqual(result['raw'], 'call him today. then send it.')


if __name__ == '__main__':
    unittest.main()
