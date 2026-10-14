"""Shared exercise persistence, optimistic updates and authenticated permissions."""
from concurrent.futures import ThreadPoolExecutor
import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

import lab_server
import lab_backup
import test_web_auth


class InstructionsTests(unittest.TestCase):
    setUp = test_web_auth.AuthTests.setUp
    tearDown = test_web_auth.AuthTests.tearDown
    node = staticmethod(test_web_auth.AuthTests.node)
    http = test_web_auth.AuthTests.http
    enable = test_web_auth.AuthTests.enable
    login = test_web_auth.AuthTests.login

    @staticmethod
    def payload(revision='0', text='# Exercise\n\nShow the routing table.'):
        return {'revision': revision, 'file': {'name': 'exercise.md', 'text': text}}

    def test_role_boundary_csrf_and_shared_read(self):
        self.enable()
        self.assertEqual(self.http('/api/instructions')[0], 401)
        admin, csrf = self.login()
        self.assertEqual(self.http('/api/instructions', 'PUT', self.payload(), admin)[0], 403)
        for name in ('user', 'guest'):
            cookie, token = self.login(name)
            self.assertIsNone(self.http('/api/instructions', cookie=cookie)[2]['file'])
            self.assertEqual(self.http('/api/instructions', 'PUT', self.payload(), cookie, token)[0], 403)
            self.assertEqual(self.http('/api/instructions', 'PUT', {'revision':'0','file':None}, cookie, token)[0], 403)
        status, _, saved = self.http('/api/instructions', 'PUT', self.payload(), admin, csrf)
        self.assertEqual(status, 200)
        self.assertNotEqual(saved['revision'], '0')
        self.assertEqual(self.http('/api/instructions', cookie=cookie)[2], saved)
        unchanged = self.http('/api/instructions?revision=' + saved['revision'], cookie=cookie)[2]
        self.assertEqual(unchanged, {'revision':saved['revision'], 'unchanged':True})
        self.assertIsNone(self.http('/api/state', cookie=cookie)[2].get('instructions'))

    def test_restart_atomic_failure_clear_and_stale_updates(self):
        original = copy.deepcopy(self.lab.topology)
        saved = self.http('/api/instructions', 'PUT', self.payload())[2]
        old_bytes = (self.lab.directory / 'instructions.json').read_bytes()
        # No nodes are running. Release ownership, then load the same disposable
        # directory through the real startup path, as after a server restart.
        self.lab.file_lock.close()
        restarted = lab_server.Lab(self.lab.directory)
        self.addCleanup(restarted.file_lock.close)
        self.assertEqual(restarted.published_instructions(), saved)
        with patch.object(Path, 'replace', side_effect=OSError('disk error')):
            self.assertEqual(self.http('/api/instructions', 'PUT', self.payload(saved['revision'], 'replacement'))[0], 400)
        self.assertEqual((self.lab.directory / 'instructions.json').read_bytes(), old_bytes)
        self.assertEqual(self.lab.published_instructions(), saved)
        self.assertEqual(self.http('/api/instructions', 'PUT', self.payload())[0], 409)
        status, _, cleared = self.http('/api/instructions', 'PUT', {'revision':saved['revision'], 'file':None})
        self.assertEqual(status, 200)
        self.assertIsNone(cleared['file'])
        self.assertNotEqual(cleared['revision'], '0')
        self.assertEqual(self.http('/api/instructions', 'PUT', self.payload(saved['revision']))[0], 409)
        self.assertEqual(restarted.published_instructions(), cleared)
        self.assertEqual(self.lab.topology, original)

    def test_concurrent_admin_updates_have_one_winner(self):
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(self.http, '/api/instructions', 'PUT', self.payload(text=text)) for text in ('one', 'two')]
            results = [f.result() for f in futures]
        self.assertEqual(sorted(result[0] for result in results), [200, 409])
        winner = next(result[2] for result in results if result[0] == 200)
        self.assertEqual(self.lab.published_instructions(), winner)

    def test_validation_utf8_size_and_failed_changes_preserve_document(self):
        saved = self.http('/api/instructions', 'PUT', self.payload())[2]
        for file in ([], 'text', {}, {'name':'x.md'}, {'name':'../x.md','text':'a'},
                     {'name':'x.html','text':'a'}, {'name':'x.md','text':'nul\0'},
                     {'name':'x.md','text':'\ud800'}, {'name':'x.md','text':'\x1b[2J'},
                     {'name':'x.md','text':'é' * (256 * 1024 + 1)},
                     {'name':'x.md','text':'a','extra':True}):
            with self.subTest(file=str(file)[:80]):
                self.assertEqual(self.http('/api/instructions', 'PUT', {'revision':saved['revision'],'file':file})[0], 400)
                self.assertEqual(self.lab.published_instructions(), saved)
        # JSON escape overhead can exceed 1 MB while decoded UTF-8 is within 512 KiB.
        response = self.http('/api/instructions', 'PUT', self.payload(saved['revision'], 'é' * (256 * 1024)))
        self.assertEqual(response[0], 200)
        self.assertEqual(len(response[2]['file']['text']), 256 * 1024)
        (self.lab.directory / 'instructions.json').write_text('corrupt')
        self.assertEqual(self.http('/api/instructions')[0], 409)

    def test_topology_and_zip_do_not_export_or_overwrite_published_document(self):
        saved = self.lab.publish_instructions(self.payload())
        topology = copy.deepcopy(self.topology)
        topology['name'] = 'Imported exercise'
        self.lab.save(topology)
        self.assertEqual(self.lab.published_instructions(), saved)
        export = lab_backup.create(self.lab)
        stream, _ = lab_backup.take(self.lab, export['url'].rsplit('/', 1)[1])
        with stream:
            data = stream.read()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertNotIn('instructions.json', archive.namelist())
            self.assertNotIn('instructions', json.loads(archive.read('topology.json')))
        lab_backup.restore(self.lab, io.BytesIO(data), lab_server.run)
        self.assertEqual(self.lab.published_instructions(), saved)


if __name__ == '__main__':
    unittest.main()
