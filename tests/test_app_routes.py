"""Compatibility checks for the cleaned Flask entry point."""
import importlib
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class AppRoutesTests(unittest.TestCase):
    def test_public_page_and_all_legacy_bookmarks_keep_their_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'PARKING_DATABASE': str(Path(directory) / 'routes.db')}):
                import database
                database.init_db()
                module = importlib.import_module('app')
                module.app.config['TESTING'] = True
                client = module.app.test_client()
                response = client.get('/')
                self.assertEqual(response.status_code, 200)
                self.assertIn('Smart Parking', response.text)
                self.assertIn('client-parking.js', response.text)
                self.assertEqual(client.get('/admin').status_code, 302)
                self.assertEqual(client.get('/admin/login').status_code, 200)
                self.assertEqual(client.get('/api/admin/sessions').status_code, 401)
                for rule, methods in module.LEGACY_ROUTES:
                    sample = re.sub(r'<int:[^>]+>', '1', rule)
                    sample = re.sub(r'<[^>]+>', 'tester', sample)
                    if 'GET' in methods:
                        result = client.get(sample)
                        expected = '/admin' if sample.startswith(('/admin', '/dashboard', '/vehicles', '/history')) else '/'
                        self.assertEqual(result.status_code, 302, sample)
                        self.assertEqual(result.headers['Location'], expected, sample)
                    if 'POST' in methods:
                        self.assertEqual(client.post(sample, json={}).status_code, 410, sample)
                    self.assertEqual(client.options(sample).status_code, 410, sample)
                self.assertEqual(client.get('/api/dashboard').status_code, 410)
                self.assertEqual(client.get('/api/spots').status_code, 200)


if __name__ == '__main__':
    unittest.main()
