"""Publication policy checks with an in-memory registry; no writes to GHCR."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('promotion', Path(__file__).resolve().parents[1]/'tools/promote_release.py')
promotion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(promotion)


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.revision = 'a'*40
        self.images = {'ghcr.io/'+promotion.REPOSITORY+suffix: 'sha256:'+str(i)*64
                       for i, suffix in enumerate(('', '-agent', '-exos', '-internet'), 1)}
        self.rows = {}
        for image, digest in self.images.items():
            self.rows[image, digest] = {'digest': digest, 'labels': {
                'org.opencontainers.image.version': '0.11.1',
                'org.opencontainers.image.revision': self.revision}}
            self.rows[image, 'latest'] = {'digest': 'sha256:'+'0'*64, 'labels': {
                'org.opencontainers.image.version': '0.11.0'}}

    def read(self, image, ref, missing_ok=False):
        if (image, ref) not in self.rows and not missing_ok:
            raise RuntimeError('Missing required registry object')
        return self.rows.get((image, ref))

    def write(self, args, check):
        image, digest = args[-1].split('@')
        target, tag = args[args.index('--tag')+1].rsplit(':', 1)
        self.assertEqual(image, target)
        self.rows[image, tag] = self.rows[image, digest]

    def test_retry_keeps_exact_versions_and_promotes_all(self):
        with patch.object(promotion, 'registry', side_effect=self.read), patch.object(
                promotion.subprocess, 'run', side_effect=self.write) as write:
            promotion.promote(self.images, 'v0.11.1', self.revision, True)
            promotion.promote(self.images, 'v0.11.1', self.revision, True)
            self.assertEqual(write.call_count, 16)
            for image, digest in self.images.items():
                self.assertEqual(self.rows[image, 'latest']['digest'], digest)
                self.assertEqual(self.rows[image, '0.11.1']['digest'], digest)

    def test_every_guard_runs_before_any_write(self):
        image = list(self.images)[-1]
        for alias, labels, digest in (
            ('0.11.1', {}, 'sha256:'+'f'*64),
            ('latest', {'org.opencontainers.image.version': '0.12.0'}, 'sha256:'+'f'*64),
            ('latest', {'org.opencontainers.image.version': '0.11.1'}, 'sha256:'+'f'*64),
            ('latest', {}, 'sha256:'+'f'*64)):
            with self.subTest(alias=alias, labels=labels):
                self.setUp()
                self.rows[image, alias] = {'digest': digest, 'labels': labels}
                with patch.object(promotion, 'registry', side_effect=self.read), patch.object(promotion.subprocess, 'run') as write:
                    with self.assertRaises(ValueError):
                        promotion.promote(self.images, 'v0.11.1', self.revision, True)
                    write.assert_not_called()

    def test_incomplete_set_and_wrong_source_rejected(self):
        with patch.object(promotion, 'registry', side_effect=self.read):
            with self.assertRaises(ValueError):
                promotion.preflight({}, 'v0.11.1', self.revision)
            with self.assertRaises(ValueError):
                promotion.preflight(self.images, 'v0.11.1', 'b'*40)

    def test_network_errors_never_allow_publication(self):
        with patch.object(promotion, 'registry', side_effect=TimeoutError), patch.object(promotion.subprocess, 'run') as write:
            with self.assertRaises(TimeoutError):
                promotion.promote(self.images, 'v0.11.1', self.revision, True)
            write.assert_not_called()
