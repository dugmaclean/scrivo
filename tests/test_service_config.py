import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from server import service_token


class PrivateTokenTests(unittest.TestCase):
    def test_private_file_and_existing_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            token = Path(folder) / 'token'
            token.write_text('x' * 48)
            token.chmod(0o600)
            with patch.dict(os.environ, DICTATION_TOKEN_FILE=str(token)):
                self.assertEqual(service_token(), 'x' * 48)
        with patch.dict(os.environ, DICTATION_TOKEN='y' * 48):
            os.environ.pop('DICTATION_TOKEN_FILE', None)
            self.assertEqual(service_token(), 'y' * 48)

    def test_public_or_linked_token_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            token = Path(folder) / 'token'
            token.write_text('x' * 48)
            token.chmod(0o644)
            with patch.dict(os.environ, DICTATION_TOKEN_FILE=str(token)):
                with self.assertRaises(ValueError): service_token()
            token.chmod(0o600)
            link = Path(folder) / 'link'
            link.symlink_to(token)
            with patch.dict(os.environ, DICTATION_TOKEN_FILE=str(link)):
                with self.assertRaises(OSError): service_token()
