"""Routed VLAN ownership safety, validation and integrated configuration."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import frr

DIRECTORY = Path(__file__).resolve().parents[1] / 'packaging/frr'
sys.path.insert(0, str(DIRECTORY))
import vlan_backend as vlan
spec = importlib.util.spec_from_file_location('vlan_services', DIRECTORY/'lldp_backend.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class VlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.service = vlan.Vlans(self.root/'owners.json')
        self.links = {'eth0': {'ifname': 'eth0', 'ifindex': 2, 'flags': ['UP']},
                      'eth0.10': {'ifname': 'eth0.10', 'ifindex': 3, 'link': 'eth0',
                                  'flags': [], 'linkinfo': {'info_kind': 'vlan',
                                    'info_data': {'id': 10, 'protocol': '802.1Q'}}}}

    def test_strict_names_and_limit(self):
        self.assertEqual(vlan.parse('eth7.4094'), ('eth7', 4094))
        for name in ('eth0.0', 'eth0.4095', 'eth8.10', 'eth0.010', 'eth0.10.20',
                     'lo.10', 'eth0.10;id', 'eth0.10\n', 'eth0.1e2'):
            with self.assertRaises(ValueError, msg=name):
                vlan.parse(name)
        with self.assertRaises(ValueError):
            vlan.validate([f'eth0.{i}' for i in range(1, 66)])

    def test_unowned_replaced_and_changed_links_are_never_deleted(self):
        for change in ({}, {'ifindex': 4}, {'master': 'blue'}, {'link': 'eth1'},
                       {'linkinfo': {'info_kind': 'dummy'}}):
            links = copy.deepcopy(self.links)
            links['eth0.10'].update(change)
            self.service.owners = {} if not change else {'eth0.10': [3, 2]}
            with patch.object(self.service, 'snapshot', return_value=links), patch.object(vlan, 'ip') as ip:
                with self.assertRaisesRegex(ValueError, 'conflicts'):
                    self.service.apply(['eth0.10'])
                ip.assert_not_called()
                if change:
                    with self.assertRaisesRegex(ValueError, 'conflicts'):
                        self.service.apply([])
                    ip.assert_not_called()

    def test_recovery_keeps_shutdown_and_removes_only_owned(self):
        self.service.owners = {'eth0.10': [3, 2]}
        self.service.save()
        recovered = vlan.Vlans(self.service.path)
        with patch.object(recovered, 'snapshot', return_value=self.links), patch.object(vlan, 'ip') as ip:
            recovered.apply(['eth0.10'])
            ip.assert_not_called()
            self.assertIn('down', recovered.show(['eth0.10']))
            recovered.apply([])
            ip.assert_called_once_with('link', 'delete', 'dev', 'eth0.10')
        self.assertEqual(json.loads(self.service.path.read_text()), {})

    def test_create_is_bounded_and_records_identity(self):
        with patch.object(self.service, 'snapshot', side_effect=[{'eth0': self.links['eth0']}, self.links]), \
                patch.object(vlan, 'ip') as ip:
            self.service.apply(['eth0.10'])
            ip.assert_called_once_with('link', 'add', 'link', 'eth0', 'name', 'eth0.10', 'type', 'vlan', 'id', '10')
        self.assertEqual(json.loads(self.service.path.read_text()), {'eth0.10': [3, 2]})

    def test_intent_replay_rollback_and_runtime_recovery(self):
        service = backend.Lldp(self.root/'runtime.json')
        session = {}
        with patch.object(service, 'apply') as apply:
            service.command('XFRR_start_configuration', session)
            service.command('vlan int eth0.10', session)
            apply.assert_not_called()
            service.command('XFRR_end_configuration', session)
            apply.assert_called_once()
            self.assertIn('vlan interface eth0.10\n', service.config())
            apply.side_effect = RuntimeError('create failed')
            with self.assertRaises(RuntimeError):
                service.command('vlan interface eth0.20', session)
            self.assertEqual(service.state['vlans'], ['eth0.10'])
            self.assertEqual(backend.Lldp(service.state_path).state['vlans'], ['eth0.10'])
            apply.side_effect = None
            service.command('no vlan int eth0.10', session)
            self.assertEqual(service.state['vlans'], [])

    def test_failed_replay_restores_intent_and_applies_rollback(self):
        service = backend.Lldp(self.root/'runtime.json')
        session = {}
        with patch.object(service, 'apply', side_effect=[RuntimeError('partial failure'), None]) as apply:
            service.command('XFRR_start_configuration', session)
            service.command('vlan interface eth0.10', session)
            with self.assertRaisesRegex(RuntimeError, 'partial failure'):
                service.command('XFRR_end_configuration', session)
            self.assertEqual(apply.call_count, 2)
        self.assertEqual(service.state['vlans'], [])
        self.assertEqual(json.loads(service.state_path.read_text())['vlans'], [])
        self.assertFalse(service.replaying)
        self.assertNotIn('replay', session)

    def test_new_image_requires_all_extension_labels(self):
        image = {'Id': 'sha256:'+'b'*64, 'Config': {'Labels': {
            'network.weblab.frr.extension': 'lldp-1', 'network.weblab.frr.snmp': 'v2c-1'}}}
        run = lambda *args: json.dumps([image])
        with self.assertRaisesRegex(ValueError, 'VLAN image'):
            frr.inspect_image(run, ValueError, True, frr.VLAN_IMAGE)
        image['Config']['Labels']['network.weblab.frr.vlan'] = '8021q-1'
        self.assertEqual(frr.archive_image(run, ValueError, True, frr.VLAN_IMAGE),
                         {'name': frr.VLAN_IMAGE, 'digest': image['Id']})
        with self.assertRaisesRegex(ValueError, 'WL_ALLOW_UNTESTED_FRR'):
            frr.inspect_image(run, ValueError, False, frr.VLAN_IMAGE)


if __name__ == '__main__':
    unittest.main()
