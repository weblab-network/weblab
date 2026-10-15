"""SNMP intent, replay ordering, read-only policy and extension identity."""
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
import snmp_backend as snmp
spec = importlib.util.spec_from_file_location('frr_services', DIRECTORY / 'lldp_backend.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class SnmpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = backend.Lldp(Path(self.tmp.name) / 'runtime.json')
        self.apply = patch.object(self.service, 'apply').start()
        self.addCleanup(patch.stopall)
        self.session = {}

    def command(self, line):
        return self.service.command(line, self.session)

    def configure(self):
        for line in ('snmp-server community testMonitor ro', 'snmp-server listen 10.88.0.1',
                     'snmp-server source 10.88.0.2/32', 'snmp-server contact Lab Operations',
                     'snmp-server location Rack A', 'snmp-server enable'):
            self.command(line)

    def test_policy_is_only_v2c_readonly_and_explicit_views(self):
        self.configure()
        config = snmp.daemon_config(self.service.state['snmp'], 'R1')
        self.assertIn('agentaddress udp:10.88.0.1:161', config)
        self.assertIn('group weblab_ro v2c weblab', config)
        self.assertIn('weblab_monitor none none', config)
        self.assertIn('com2sec weblab 10.88.0.2/32 testMonitor', config)
        self.assertIn('view weblab_monitor included .1.0.8802.1.1.2', config)
        self.assertIn('sysDescr ' + snmp.SYSTEM_DESCRIPTION, config)
        self.assertNotIn(' v1 ', config)
        self.assertNotIn('rwcommunity', config)
        self.assertNotIn('testMonitor', self.command('show snmp'))
        self.assertEqual(self.service.state_path.stat().st_mode & 0o777, 0o600)

    def test_invalid_input_does_not_mutate_live_intent(self):
        for line in ('snmp-server enable', 'snmp-server listen 0.0.0.0',
                     'snmp-server listen 224.0.0.1', 'snmp-server listen 255.255.255.255',
                     'snmp-server source invalid', 'snmp-server community bad#value ro',
                     'snmp-server contact bad"value', 'snmp-server location ' + 'x'*129):
            before = copy.deepcopy(self.service.state)
            with self.assertRaises(ValueError, msg=line):
                self.command(line)
            self.assertEqual(before, self.service.state)
        self.configure()
        self.command('no snmp-server community')
        self.assertFalse(self.service.state['snmp']['enabled'])
        self.assertEqual(self.service.state['snmp']['community'], '')

    def test_sorted_replay_and_abbreviations(self):
        self.configure()
        saved = sorted(snmp.config_lines(self.service.state['snmp']))
        other = backend.Lldp(Path(self.tmp.name) / 'second.json')
        with patch.object(other, 'apply') as apply:
            session = {}
            other.command('XFRR_start_configuration', session)
            for line in saved:
                other.command(line, session)
            apply.assert_not_called()
            other.command('XFRR_end_configuration', session)
            apply.assert_called_once()
        self.assertEqual(other.state['snmp'], self.service.state['snmp'])
        self.command('snmp-s loc Rack B upstairs')
        self.assertEqual(self.service.state['snmp']['location'], 'Rack B upstairs')
        self.command('no snmp-s en')
        self.assertFalse(self.service.state['snmp']['enabled'])

    def test_apply_failure_rolls_back_and_recovery_keeps_settings(self):
        self.configure()
        before = copy.deepcopy(self.service.state)
        self.apply.side_effect = RuntimeError('failure')
        with self.assertRaises(RuntimeError):
            self.command('snmp-server location Unapplied')
        self.assertEqual(before, self.service.state)
        recovered = backend.Lldp(self.service.state_path)
        self.assertEqual(before, recovered.state)

    def test_missing_address_defers_and_removes_listener(self):
        self.configure()
        service = snmp.Snmp(backend.process_start)
        with patch.object(service, 'stop') as stop, patch.object(snmp, 'address_present', return_value=False), \
                patch.object(snmp.subprocess, 'Popen') as popen:
            service.apply(self.service.state['snmp'], 'R1')
            self.assertIn('waiting', service.status)
            stop.assert_called()
            popen.assert_not_called()

    def test_snmp_image_requires_its_own_identity(self):
        image = {'Id': 'sha256:'+'b'*64, 'Config': {'Labels': {
            'network.weblab.frr.extension': 'lldp-1'}}}
        run = lambda *args: json.dumps([image])
        with self.assertRaisesRegex(ValueError, 'SNMP image'):
            frr.inspect_image(run, ValueError, True, frr.SNMP_IMAGE)
        image['Config']['Labels']['network.weblab.frr.snmp'] = 'v2c-1'
        self.assertEqual(frr.archive_image(run, ValueError, True, frr.SNMP_IMAGE),
                         {'name': frr.SNMP_IMAGE, 'digest': image['Id']})


if __name__ == '__main__':
    unittest.main()
