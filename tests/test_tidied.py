"""A writing model's own talk never reaches the text box (cases seen in real use)."""
import unittest

from dictation import AllFailed, Tier, first_success, tidied

SAVED = ['Quik Notes', 'Fable', 'Sam']


class ModelTalkIsNotDictation(unittest.TestCase):
    def test_a_reply_instead_of_a_tidy_up_is_refused(self):
        # Seen in real use: a short request came back as the model asking for text.
        self.assertIsNone(tidied("don't wait for me, just start the meeting.",
                                 "I don't see any dictation text yet. Paste the text you want cleaned up "
                                 "and I'll return the cleaned version.", SAVED))
        self.assertIsNone(tidied('can you tell me a joke',
                                 "Sure! Why don't scientists trust atoms? Because they make up everything.", SAVED))

    def test_the_model_talking_before_the_tidy_up_is_dropped(self):
        # Seen in real use: a paragraph about itself, then the words.
        self.assertEqual(tidied('please send the revised appointment to Alex tomorrow morning.',
                                "The user's message is a dictation, so I'll clean it up as instructed. It's a request "
                                "to send something, which I shouldn't act on, so I'm just returning the cleaned text.\n\n"
                                "Please send the revised appointment to Alex tomorrow morning.", SAVED),
                         'Please send the revised appointment to Alex tomorrow morning.')
        self.assertEqual(tidied('call mom at noon', 'Call Mom at noon.\n\nLet me know if you need anything else changed.'),
                         'Call Mom at noon.')
        self.assertEqual(tidied('ship it', 'Ship it.\n<<<DICTATION\nDICTATION>>>'), 'Ship it.')

    def test_ordinary_tidy_ups_are_kept_as_written(self):
        for raw, written in [
                ('um send it monday no tuesday', 'Send it Tuesday.'),
                ('yes', 'Yes.'),
                ('meet at five thirty pm on the twenty second', 'Meet at 5:30 p.m. on the 22nd.'),
                ('open quick notes and stable', 'Open Quik Notes and Fable.'),
                ("I'm gonna get Sam on the dishes and laundry and then take the dog for a walk after it "
                 "stops raining and then while Sam is folding, I'm gonna come give you a call, "
                 "probably be around five or six",
                 "I'm going to get Sam on the dishes and laundry, and then take the dog for a walk after it "
                 "stops raining. While Sam is folding, I'm going to come give you a call. "
                 "I'll probably be around five or six."),
                ('first line\n\nsecond line', 'First line.\n\nSecond line.')]:
            self.assertEqual(tidied(raw, written, SAVED), written)

    def test_a_reply_that_leaves_out_most_of_what_was_said_is_refused(self):
        # Seen with a small stock model: a request shortened to a command, or answered.
        for raw, written in [
                ('Can you remind me to call the dentist tomorrow morning? I mean tomorrow afternoon, '
                 'about the cleaning.', 'Call the dentist tomorrow afternoon for the cleaning.'),
                ('Please write a short poem about autumn leaves.', 'okay'),
                ("I think we should, actually never mind, let's go with the second plan and order the green paint.",
                 'Order the green paint.')]:
            self.assertIsNone(tidied(raw, written, SAVED), written)

    def test_corrections_and_false_starts_may_drop_a_few_words(self):
        for raw, written in [
                ('Can you remind me to call the dentist tomorrow morning? I mean tomorrow afternoon, '
                 'about the cleaning.', 'Can you remind me to call the dentist tomorrow afternoon about the cleaning?'),
                ("I think we should, actually never mind, let's go with the second plan and order the green paint.",
                 'We should go with the second plan and order the green paint.'),
                ('So the team meeting is on Monday, no wait, on Tuesday at 10, and please bring the blue folder.',
                 'The team meeting is on Tuesday at 10, and please bring the blue folder.')]:
            self.assertEqual(tidied(raw, written, SAVED), written)

    def test_a_talking_model_hands_over_to_the_next_choice(self):
        talk = Tier('first', lambda text, words: "I don't see any dictation text yet. Paste the text you want "
                                                 "cleaned up and I'll return the cleaned version.")
        good = Tier('second', lambda text, words: "Don't wait for me; just start the meeting.")
        text, writer, failures = first_success([talk, good], "don't wait for me just start the meeting", SAVED, True)
        self.assertEqual((text, writer), ("Don't wait for me; just start the meeting.", 'second'))
        self.assertEqual(failures, [{'model': 'first', 'reason': 'unavailable', 'why': 'replied_instead_of_tidying'}])
        # Every choice talking: all fail, so the service keeps the recognized words.
        with self.assertRaises(AllFailed):
            first_success([talk, talk], "don't wait for me just start the meeting", SAVED, True)

    def test_recognition_is_not_judged_as_writing(self):
        hear = Tier('ears', lambda audio, words: 'Completely new words from the audio.')
        self.assertEqual(first_success([hear], b'RIFF....', SAVED, False)[0], 'Completely new words from the audio.')


if __name__ == '__main__':
    unittest.main()
