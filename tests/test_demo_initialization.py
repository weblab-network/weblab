import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import lab_backup
import lab_server

SPEC = importlib.util.spec_from_file_location('exos_initialize', Path(__file__).resolve().parents[1] / 'packaging/exos/initialize.py')
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)


class DemoInitializationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='demo-initialize-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        self.images = self.root / 'images'
        self.data = self.root / 'data'
        self.image = self.bundle / 'EXOS-VM_fixture.qcow2'
        self.image.write_bytes(b'fixture only, never booted')
        (self.bundle / 'image.json').write_text(json.dumps({'filename': self.image.name, 'sha256': lab_backup.digest(self.image)}))
        lab = lab_server.Lab(self.root / 'source', self.bundle)
        try:
            lab.save({'name': 'Fixture demo', 'nodes': [
                {'id': 'demo-pc', 'name': 'PC', 'type': 'pc', 'image': 'alpine:latest'}], 'links': []})
            result = lab_backup.create(lab)
            stream, _ = lab_backup.take(lab, result['url'].rsplit('/', 1)[1])
            with stream, (self.bundle / 'demo.zip').open('wb') as output:
                shutil.copyfileobj(stream, output)
        finally:
            lab.file_lock.close()

    def test_empty_workspace_restores_once_and_preserves_user_changes(self):
        self.assertTrue(demo.initialize(self.bundle, self.images, self.data))
        self.assertEqual((self.images / self.image.name).read_bytes(), self.image.read_bytes())
        topology = self.data / 'topology.json'
        value = json.loads(topology.read_text())
        self.assertEqual(value['name'], 'Fixture demo')
        value['name'] = 'My subsequent work'
        topology.write_text(json.dumps(value))
        self.assertFalse(demo.initialize(self.bundle, self.images, self.data))
        self.assertEqual(json.loads(topology.read_text())['name'], 'My subsequent work')

    def test_any_existing_data_prevents_seed(self):
        self.data.mkdir()
        (self.data / 'keep.txt').write_text('existing storage')
        self.assertFalse(demo.initialize(self.bundle, self.images, self.data))
        self.assertEqual(sorted(p.name for p in self.data.iterdir()), ['keep.txt'])

    def test_conflicting_base_is_not_replaced(self):
        self.images.mkdir()
        target = self.images / self.image.name
        target.write_bytes(b'other image')
        with self.assertRaisesRegex(ValueError, 'nothing was replaced'):
            demo.initialize(self.bundle, self.images, self.data)
        self.assertEqual(target.read_bytes(), b'other image')
        self.assertFalse(self.data.exists())

    def test_corrupt_seed_fails_closed_and_does_not_launch_blank_lab(self):
        (self.bundle / 'demo.zip').write_bytes(b'broken ZIP')
        with self.assertRaises(Exception):
            demo.initialize(self.bundle, self.images, self.data)
        self.assertTrue((self.data / '.weblab-demo-initializing').exists())
        with self.assertRaisesRegex(ValueError, 'interrupted'):
            demo.initialize(self.bundle, self.images, self.data)

    def test_corrupt_bundled_image_is_rejected(self):
        self.image.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            demo.initialize(self.bundle, self.images, self.data)
        self.assertFalse((self.images / self.image.name).exists())


if __name__ == '__main__':
    unittest.main()
