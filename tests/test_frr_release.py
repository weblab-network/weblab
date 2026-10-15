"""Release provenance and the public profile's image/storage compatibility."""
import importlib.util
import json
from pathlib import Path
import unittest

import frr

spec = importlib.util.spec_from_file_location('frr_sources', Path(__file__).resolve().parents[1] /
                                             'packaging/frr/collect_sources.py')
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)


class ReleaseTests(unittest.TestCase):
    def image(self, identity):
        return {'Id': identity, 'Config': {'Labels': {
            'network.weblab.frr.extension': 'lldp-1', 'network.weblab.frr.snmp': 'v2c-1',
            'network.weblab.frr.vlan': '8021q-1', 'network.weblab.frr.vrrp': 'ipv4-1'}}}

    def test_exact_release_accepted_and_archived_as_itself(self):
        self.assertRegex(frr.PUBLIC_IMAGE_ID, r'^sha256:[a-f0-9]{64}$')
        image = self.image(frr.PUBLIC_IMAGE_ID)
        run = lambda *args: json.dumps([image])
        self.assertTrue(frr.inspect_image(run, ValueError, name=frr.PUBLIC_IMAGE)['tested'])
        self.assertEqual(frr.archive_image(run, ValueError, name=frr.PUBLIC_IMAGE),
                         {'name': frr.PUBLIC_IMAGE, 'digest': frr.PUBLIC_IMAGE_ID})

    def test_same_labels_do_not_make_another_build_tested(self):
        image = self.image('sha256:' + 'a'*64)
        run = lambda *args: json.dumps([image])
        with self.assertRaisesRegex(ValueError, 'tested release ID'):
            frr.inspect_image(run, ValueError, name=frr.PUBLIC_IMAGE)
        self.assertFalse(frr.inspect_image(run, ValueError, True, frr.PUBLIC_IMAGE)['tested'])

    def test_local_selector_keeps_its_separate_storage_and_opt_in(self):
        image = self.image(frr.PUBLIC_IMAGE_ID)
        run = lambda *args: json.dumps([image])
        with self.assertRaisesRegex(ValueError, 'experimental'):
            frr.inspect_image(run, ValueError, name=frr.VRRP_IMAGE)
        self.assertNotEqual(frr.config_path({'image':frr.VRRP_IMAGE}, Path('.')),
                            frr.config_path({'image':frr.PUBLIC_IMAGE}, Path('.')))

    def test_custom_base_recipes_are_explicit_not_guessed(self):
        def package(origin, version, commit):
            return f'P:{origin}\nV:{version}\no:{origin}\nc:{commit}\nL:GPL-2.0\n'
        for origin, version in sources.CUSTOM.items():
            row = sources.inventory(package(origin, version, '-dirty'))[0]
            self.assertEqual(row['source_repository'], 'FRRouting/frr')
        for origin, version in (('unknown', '1'), ('frr', '10.99-r0'), ('libyang', '3-r0')):
            with self.assertRaises(ValueError):
                sources.inventory(package(origin, version, '-dirty'))
        row = sources.inventory(package('lldpd', '1.0.19-r0', 'a'*40))[0]
        self.assertEqual(row['source_repository'], 'alpinelinux/aports')
