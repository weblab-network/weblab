"""Automation tests use disposable storage and the existing PTY echo fixture."""
import copy
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import lab_automation
import lab_server
import test_lab

ROOT = Path(__file__).resolve().parents[1]


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_lab.LabTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.lab = self.fixture.lab
        self.lab.save({'name': 'Empty', 'nodes': [], 'links': []})
        self.api = lab_automation.Automation(self.lab, lab_server.LabError, lab_server.ports, lab_server.run)
        self.lab.automation = self.api
        self.topology = {'version': 1, 'name': 'Exercise',
                         'nodes': [self.fixture.node('new_r1')], 'links': []}
        (self.fixture.root / 'docs').mkdir()
        (self.fixture.root / 'docs/practice-labs.md').write_text((ROOT / 'docs/practice-labs.md').read_text())

    def preview(self):
        return self.api.preview(self.topology, '# Exercise\nInspect the console.')

    def test_preview_is_nonmutating_and_downloads_roundtrip(self):
        before = self.lab.topology_path.read_bytes()
        proposal = self.preview()
        self.assertEqual(before, self.lab.topology_path.read_bytes())
        self.assertNotIn('iol_id', proposal['topology']['nodes'][0])
        self.assertFalse((self.lab.directory / 'nodes' / 'new_r1').exists())
        for key, expected in [('topology', proposal['topology']), ('instructions', proposal['instructions_markdown'])]:
            with urlopen(f'http://127.0.0.1:{self.fixture.port}' + proposal['downloads'][key]) as response:
                self.assertIn('attachment', response.headers['Content-Disposition'])
                body = response.read().decode()
                self.assertEqual(json.loads(body) if key == 'topology' else body, expected)

    def test_http_opt_in_and_origin(self):
        self.lab.automation = None
        with self.assertRaises(HTTPError):
            self.fixture.request('/api/automation/state')
        self.lab.automation = self.api
        with self.assertRaises(HTTPError):
            self.fixture.request('/api/automation/state', origin='https://untrusted.example')
        self.assertIn('markdown', self.fixture.request('/api/automation/guide'))
        with self.assertRaises(HTTPError):
            self.fixture.request('/api/automation/preview', 'POST', {'topology': self.topology, 'instructions': 'x'}, origin='https://untrusted.example')

    def test_apply_and_retry_preserve_changes_and_disk_identity(self):
        proposal = self.preview()
        applied = self.api.apply(proposal['proposal_id'])
        self.assertEqual(applied, self.api.apply(proposal['proposal_id']))
        self.assertEqual(applied['revision'], self.api.proposal(proposal['proposal_id'])['applied_revision'])
        changed = copy.deepcopy(self.lab.topology)
        changed['nodes'][0]['x'] += 1
        self.lab.save(changed)
        with self.assertRaisesRegex(lab_server.LabError, 'changed'):
            self.api.apply(proposal['proposal_id'])
        self.assertEqual(self.lab.topology['nodes'][0]['x'], changed['nodes'][0]['x'])

    def test_stale_preview_and_start_refused(self):
        proposal = self.preview()
        self.lab.save({'name': 'Someone edited', 'nodes': [], 'links': []})
        with self.assertRaisesRegex(lab_server.LabError, 'changed'):
            self.api.apply(proposal['proposal_id'])
        with self.assertRaisesRegex(lab_server.LabError, 'changed'):
            self.api.start_node('new_r1', proposal['base_revision'])

    def test_reused_aliases_are_remapped_without_touching_saved_storage(self):
        self.lab.save(self.fixture.topology)
        with self.assertRaisesRegex(lab_server.LabError, 'not empty'):
            self.preview()
        retained = self.lab.directory / 'nodes' / 'r1'
        retained.mkdir(parents=True)
        (retained / 'saved').write_text('keep')
        source = copy.deepcopy(self.fixture.topology)
        before = copy.deepcopy(source)
        first = self.api.preview(source, '# Exercise', True)
        second = self.api.preview(source, '# Exercise', True)
        self.assertEqual(source, before)
        self.assertTrue(first['replaces_existing'])
        self.assertNotEqual(first['node_id_map'], second['node_id_map'])
        reread = self.api.proposal(first['proposal_id'])
        self.assertEqual(first['topology'], reread['topology'])
        self.assertEqual(first['node_id_map'], reread['node_id_map'])
        mapping = first['node_id_map']
        for node in first['topology']['nodes']:
            self.assertRegex(node['id'], r'^n[a-f0-9]{24}$')
        link = first['topology']['links'][0]
        self.assertEqual(link['a']['node'], mapping['r1'])
        self.assertEqual(link['b']['node'], mapping['r2'])
        applied = self.api.apply(first['proposal_id'])
        self.assertEqual(applied['topology']['nodes'][0]['id'], mapping['r1'])
        self.assertEqual(self.api.apply(first['proposal_id']), applied)
        self.assertEqual((retained / 'saved').read_text(), 'keep')

    def test_optional_ids_long_aliases_and_repeated_link_labels(self):
        source = {'name': 'Local aliases', 'nodes': [self.fixture.node('A'), self.fixture.node('B')], 'links': []}
        del source['nodes'][0]['id']
        source['nodes'][0]['name'] = 'Router A'
        source['nodes'][1]['id'] = 'long alias / ' + 'x' * 60
        for i in range(3):
            link = {'a': {'node': 'Router A', 'port': f'0/{i}'},
                    'b': {'node': source['nodes'][1]['id'], 'port': f'0/{i}'}}
            if i:
                link['id'] = 'same cosmetic label'
            source['links'].append(link)
        p = self.api.preview(source, '# Exercise')
        self.assertEqual(len({l['id'] for l in p['topology']['links']}), 3)
        self.assertEqual(p['topology']['links'][0]['a']['node'], p['node_id_map']['Router A'])
        self.assertEqual(p['topology']['nodes'][0]['name'], 'Router A')
        self.assertEqual(self.lab.topology['nodes'], [])

    def test_duplicate_alias_and_unknown_reference_diagnostics(self):
        source = copy.deepcopy(self.fixture.topology)
        source['nodes'][1]['id'] = 'r1'
        with self.assertRaisesRegex(lab_server.LabError, r'nodes\[1\].*duplicate alias.*r1.*nodes\[0\]'):
            self.api.preview(source, '# Exercise')
        source = copy.deepcopy(self.fixture.topology)
        source['links'][0]['b']['node'] = 'R2'
        with self.assertRaisesRegex(lab_server.LabError, r'links\[0\].b.node.*R2.*r2.*case-sensitive'):
            self.api.preview(source, '# Exercise')
        self.assertEqual(self.api.proposals, {})

    def test_interface_and_memory_errors_identify_the_problem(self):
        source = copy.deepcopy(self.fixture.topology)
        source['links'][0]['a']['port'] = 'bad'
        with self.assertRaisesRegex(lab_server.LabError, r'links\[0\].a.port.*bad.*r1.*0/0'):
            self.api.preview(source, '# Exercise')
        source = copy.deepcopy(self.fixture.topology)
        source['links'].append(copy.deepcopy(source['links'][0]))
        with self.assertRaisesRegex(lab_server.LabError, r'links\[1\].a.*already used by links\[0\].a'):
            self.api.preview(source, '# Exercise')
        source['nodes'][1]['memory'] = 1
        with self.assertRaisesRegex(lab_server.LabError, r'nodes\[1\].*r2.*memory.*256'):
            self.api.preview(source, '# Exercise')

    def test_allocation_skips_retained_and_pending_ids(self):
        retained = self.lab.directory / 'nodes' / ('n' + 'a' * 24)
        retained.mkdir(parents=True)
        with patch.object(lab_automation.secrets, 'token_hex', side_effect=['a' * 24, 'b' * 24, 'c' * 32]):
            p = self.preview()
        self.assertEqual(p['node_id_map']['new_r1'], 'n' + 'b' * 24)
        with patch.object(lab_automation.secrets, 'token_hex', side_effect=['b' * 24, 'd' * 24, 'e' * 32]):
            other = self.preview()
        self.assertEqual(other['node_id_map']['new_r1'], 'n' + 'd' * 24)

    def test_running_node_blocks_apply_and_real_echo_launch(self):
        self.lab.save(self.fixture.topology)
        p = self.api.preview(self.topology, '# Exercise', True)
        state = self.api.state()
        result = self.api.start_node('r1', state['revision'])
        self.assertTrue(result['started'])
        self.assertEqual(result['state']['guest_readiness'], 'unverified')
        with self.assertRaisesRegex(lab_server.LabError, 'Stop all'):
            self.api.apply(p['proposal_id'])
        self.assertIn('r1', self.lab.runtime)

    def test_reading_logs_does_not_create_saved_node_storage(self):
        p = self.preview()
        self.api.apply(p['proposal_id'])
        node_id = p['node_id_map']['new_r1']
        result = self.fixture.request(f'/api/nodes/{node_id}/logs')
        self.assertIn('No launcher logs yet', result['logs'])
        self.assertFalse((self.lab.directory / 'nodes' / node_id).exists())

    def test_start_failure_does_not_stop_other_nodes(self):
        state = self.api.apply(self.preview()['proposal_id'])
        with patch.object(self.lab, 'start', side_effect=lab_server.LabError('Unavailable image')), patch.object(self.lab, 'stop_all') as stop:
            result = self.api.start_node(state['topology']['nodes'][0]['id'], state['revision'])
        self.assertFalse(result['started'])
        self.assertIn('Unavailable image', result['error'])
        stop.assert_not_called()

    def test_schema_and_port_errors_rejected(self):
        cases = []
        p = copy.deepcopy(self.topology); p['nodes'][0]['iol_id'] = 100; cases.append(p)
        p = copy.deepcopy(self.topology); p['nodes'][0]['shell'] = 'bad'; cases.append(p)
        p = copy.deepcopy(self.topology); p['nodes'][0]['image'] = 'missing.bin'; cases.append(p)
        p = copy.deepcopy(self.topology); p['nodes'][0]['x'] = float('nan'); cases.append(p)
        p = copy.deepcopy(self.topology); p['version'] = 2; cases.append(p)
        p = copy.deepcopy(self.fixture.topology); p['links'][0]['a']['port'] = 'eth99'; cases.append(p)
        for topology in cases:
            with self.subTest(topology=topology), self.assertRaises(lab_server.LabError):
                self.api.preview(topology, '# Exercise', True)
        self.assertEqual(self.lab.topology['nodes'], [])
        self.assertEqual(self.api.proposals, {})

    def test_expiry_limits_discard_and_late_storage_collision(self):
        p = self.preview()
        (self.lab.directory / 'nodes' / p['node_id_map']['new_r1']).mkdir(parents=True)
        with self.assertRaisesRegex(lab_server.LabError, 'stored device data'):
            self.api.apply(p['proposal_id'])
        self.api.discard(p['proposal_id'])
        with self.assertRaisesRegex(lab_server.LabError, 'expired'):
            self.api.proposal(p['proposal_id'])
        self.topology['nodes'][0]['id'] = 'other'
        p = self.preview()
        self.api.proposals[p['proposal_id']]['expires'] = time.monotonic() - 1
        with self.assertRaisesRegex(lab_server.LabError, 'expired'):
            self.api.apply(p['proposal_id'])
        with patch.object(lab_automation, 'MAX_PROPOSALS', 1):
            self.preview()
            with self.assertRaisesRegex(lab_server.LabError, 'Too many'):
                self.preview()

    def test_instructions_limits(self):
        for instructions in ('', 'x' * 65537, 'bad\x00text', None):
            with self.assertRaises(lab_server.LabError):
                self.api.preview(self.topology, instructions)

    def test_profile_catalog_includes_actual_ports_without_launch(self):
        for name in ('EXOS-VM_test.qcow2', 'vJunos-switch-test.qcow2', 'vJunosEvolved-test.qcow2', 'vEOS-lab-test.qcow2'):
            (self.lab.image_dir / name).touch()
        with patch.object(self.api, 'run', return_value='alpine:latest\n'), patch.object(self.lab, 'start') as start:
            catalog = self.api.catalog()
        by_family = {p['family']: p for p in catalog['profiles']}
        self.assertEqual(by_family['exos']['ports_at_default'], ['Mgmt'] + [str(i) for i in range(1, 13)])
        self.assertIn('ge-0/0/0', by_family['vjunos-switch']['ports_at_default'])
        self.assertIn('et-0/0/0', by_family['vjunos-evolved']['ports_at_default'])
        self.assertFalse(by_family['frr']['image_present'])
        self.assertFalse(by_family['ll2s']['image_present'])
        self.assertEqual(by_family['ll2s']['ports_at_default'], ['eth0', 'eth1', 'eth2', 'eth3'])
        self.assertEqual(by_family['ll2s']['ports_at_maximum'][-1], 'eth7')
        self.assertTrue(by_family['ll2s']['startup_config'])
        self.assertFalse(by_family['ll2s']['kvm_required'])
        self.assertTrue(by_family['alpine']['image_present'])
        self.assertFalse(by_family['exos']['startup_config'])
        start.assert_not_called()

    def test_flag_defaults_and_validation(self):
        with patch.dict('os.environ', {'WL_AUTOMATION': '0'}):
            self.assertFalse(lab_server.parse_args([]).automation)
            self.assertTrue(lab_server.parse_args(['--automation']).automation)
        with patch.dict('os.environ', {'WL_AUTOMATION': '1'}):
            self.assertTrue(lab_server.parse_args([]).automation)
