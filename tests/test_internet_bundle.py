"""Offline binary/source identity checks; no registry or Docker daemon required."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


collector = module('internet_source_collector', 'packaging/internet/collect_sources.py')
verifier = module('internet_bundle_verifier', 'tools/verify_internet_bundle.py')
bundler = module('internet_bundle_builder', 'tools/bundle_internet_release.py')


class InternetBundleTests(unittest.TestCase):
    def test_inventory_requires_exact_unmodified_source_identity(self):
        text = 'P:example\nV:1.0-r0\no:example\nc:' + 'a'*40 + '\nL:MIT\nU:https://example.invalid\n'
        row = collector.inventory(text)[0]
        self.assertEqual((row['name'], row['version'], row['license']), ('example', '1.0-r0', 'MIT'))
        for invalid in ('', text.replace('a'*40, '-dirty'), text.replace('o:example', 'o:../escape'),
                        text.replace('L:MIT', ''), text+'\n'+text):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                collector.inventory(invalid)

    def test_license_retention_does_not_extract_unsafe_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collector.license_texts(root/'meta-package-with-no-distfiles', root/'licenses')
            distfiles = root/'distfiles'; distfiles.mkdir()
            with tarfile.open(distfiles/'source.tar.gz', 'w:gz') as archive:
                for name in ('project/COPYING', '../../COPYING', '/COPYING', 'project/code.c'):
                    entry = tarfile.TarInfo(name); entry.size = 4
                    archive.addfile(entry, io.BytesIO(b'text'))
            collector.license_texts(distfiles, root/'licenses')
            files = [p.relative_to(root/'licenses').as_posix() for p in (root/'licenses').rglob('*') if p.is_file()]
            self.assertEqual(files, ['source.tar.gz/project/COPYING'])

    def fixture(self, root):
        labels = {'network.weblab.internet.distribution': '1',
                  'org.opencontainers.image.source': 'https://github.com/weblab-network/weblab',
                  'org.opencontainers.image.version': '0.11.0', 'org.opencontainers.image.revision': 'a'*40}
        config = json.dumps({'config': {'Labels': labels}}).encode()
        identity = 'sha256:' + hashlib.sha256(config).hexdigest()
        package = {'name': 'example', 'version': '1-r0', 'origin': 'example', 'commit': 'b'*40, 'license': 'MIT'}
        source = b'print("gateway")\n'
        info = {'Id': identity, 'Os': 'linux', 'Architecture': 'amd64', 'labels': labels,
                'image': 'ghcr.io/weblab-network/weblab-internet:0.11.0', 'packages': [package],
                'build_files': {'gateway.py': hashlib.sha256(source).hexdigest()}}
        def archive(path, files):
            with tarfile.open(path, 'w:gz') as output:
                for name, data in files.items():
                    entry = tarfile.TarInfo(name); entry.size = len(data)
                    output.addfile(entry, io.BytesIO(data))
        archive(root/'weblab-internet-linux-amd64.tar.gz', {
            'config.json': config, 'manifest.json': json.dumps([{'Config': 'config.json', 'RepoTags': [info['image']]}]).encode()})
        archive(root/'weblab-internet-sources.tar.gz', {
            'build/gateway.py': source, 'packages.json': json.dumps([package]).encode(),
            'aports/example/'+'b'*40+'/APKBUILD': b'pkgname=example\n'})
        (root/'weblab-internet-image.json').write_text(json.dumps(info))
        (root/'weblab-internet-NOTICE.txt').write_text('Component licenses retained')
        self.checksums(root)
        return info

    def checksums(self, root):
        (root/'weblab-internet-SHA256SUMS').write_text(''.join(
            hashlib.sha256((root/name).read_bytes()).hexdigest()+'  '+name+'\n' for name in sorted(verifier.FILES)))

    def test_bundle_binds_binary_sources_and_reviewed_public_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); info = self.fixture(root)
            self.assertEqual(verifier.verify(root, '0.11.0', 'a'*40)['Id'], info['Id'])
            with self.assertRaisesRegex(ValueError, 'revision differs'):
                verifier.verify(root, revision='c'*40)
            with self.assertRaisesRegex(ValueError, 'version differs'):
                verifier.verify(root, version='0.12.0')
            (root/'weblab-internet-NOTICE.txt').write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                verifier.verify(root)

    def test_binary_identity_mismatch_and_incomplete_bundle_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); info = self.fixture(root)
            info['Id'] = 'sha256:'+'d'*64
            (root/'weblab-internet-image.json').write_text(json.dumps(info)); self.checksums(root)
            with self.assertRaisesRegex(ValueError, 'Archived image does not match'):
                verifier.verify(root)
            (root/'weblab-internet-SHA256SUMS').write_text('')
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                verifier.verify(root)

    def test_development_images_and_source_metadata_mismatch_refused(self):
        labels = {'org.opencontainers.image.version': '0.11.0', 'org.opencontainers.image.revision': 'a'*40,
                  'org.opencontainers.image.source': 'https://github.com/weblab-network/weblab'}
        runtime, sources = {'Config': {'Labels': dict(labels)}}, {'Config': {'Labels': dict(labels)}}
        with self.assertRaisesRegex(ValueError, 'release build target'):
            bundler.validate_pair(runtime, sources)
        runtime['Config']['Labels']['network.weblab.internet.distribution'] = '1'
        bundler.validate_pair(runtime, sources)
        sources['Config']['Labels']['org.opencontainers.image.revision'] = 'b'*40
        with self.assertRaisesRegex(ValueError, 'metadata differ'):
            bundler.validate_pair(runtime, sources)

    def test_alpine_recipe_hook_links_are_bounded_to_their_recipe(self):
        root = 'aports/dnsmasq/'+'a'*40+'/'
        regular = tarfile.TarInfo(root+'dnsmasq.pre-install')
        link = tarfile.TarInfo(root+'dnsmasq.pre-upgrade')
        link.type, link.linkname = tarfile.SYMTYPE, 'dnsmasq.pre-install'
        members = {member.name: member for member in (regular, link)}
        verifier.safe_source_members(members)
        for target in ('/etc/passwd', '../../../../etc/passwd', 'dnsmasq.pre-upgrade'):
            link.linkname = target
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'Unsafe'):
                verifier.safe_source_members(members)

    def test_published_digest_requires_the_exact_tested_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); info = self.fixture(root)
            reference = 'ghcr.io/weblab-network/weblab-internet@sha256:'+'e'*64
            actual = {'Id': info['Id'], 'RepoDigests': [reference]}
            with patch.object(verifier.subprocess, 'check_output', return_value=json.dumps([actual]).encode()), \
                    patch.object(verifier, 'anonymous_reference', return_value=reference):
                result = verifier.registry_lock(root, info['image'], info)
            self.assertEqual(result['image'], reference)
            actual['Id'] = 'sha256:'+'f'*64
            with patch.object(verifier.subprocess, 'check_output', return_value=json.dumps([actual]).encode()), \
                    self.assertRaisesRegex(ValueError, 'differs from the tested binary'):
                verifier.registry_lock(root, info['image'], info)
