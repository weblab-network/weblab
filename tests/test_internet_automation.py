"""Gateway automation uses disposable storage; no Docker or active lab required."""
import copy
import json
import time
import unittest
from unittest.mock import patch

import internet_cloud
import lab_automation
import lab_server
import test_lab


class InternetAutomationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_lab.LabTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.lab = self.fixture.lab
        self.lab.save({'name': 'Gateway', 'nodes': [
            {'id': 'cloud', 'name': 'Internet', 'type': 'cloud'},
            {'id': 'pc', 'name': 'PC', 'type': 'pc'}],
            'links': [{'id': 'wire', 'a': {'node': 'cloud', 'port': 'eth0'},
                       'b': {'node': 'pc', 'port': 'eth0'}}]})
        self.api = lab_automation.Automation(self.lab, lab_server.LabError, lab_server.ports, lab_server.run)
        self.lab.automation = self.api

    def preview(self, config):
        return self.api.preview_internet('cloud', config, self.api.state()['revision'])

    def test_large_settings_pages_roundtrip_and_reject_stale_pages(self):
        config = internet_cloud.settings({
            'routes': [{'destination': f'10.{i}.0.0/24', 'via': '192.0.2.2'} for i in range(32)],
            'firewall': {'default_action': 'deny', 'rules': [
                {'action': 'allow', 'protocol': 'tcp', 'source': f'10.{i}.0.0/24', 'destination_port': '443'}
                for i in range(64)]},
            'port_forwards': [{'protocol': 'tcp', 'external_port': 22000 + i,
                               'target_address': '10.0.0.10', 'target_port': 22} for i in range(32)]})
        self.api.apply_internet(self.preview(config)['proposal_id'])
        offsets, collected, revision = {}, {'routes': [], 'rules': [], 'port_forwards': []}, None
        for _ in range(20):
            page = self.api.internet_settings('cloud', page_size=8, expected_revision=revision, **offsets)
            self.assertLessEqual(len(json.dumps(page, ensure_ascii=True, indent=2)), 8000)
            revision = page['revision']
            collected['routes'] += page['internet']['routes']
            collected['rules'] += page['internet']['firewall']['rules']
            collected['port_forwards'] += page['internet']['port_forwards']
            if all(v is None for v in page['next_offsets'].values()):
                break
            offsets = {arg: page['next_offsets'][key] if page['next_offsets'][key] is not None else page['totals'][key]
                       for arg, key in [('route_offset', 'routes'), ('rule_offset', 'rules'), ('forward_offset', 'port_forwards')]}
        self.assertEqual(collected, {'routes': config['routes'], 'rules': config['firewall']['rules'], 'port_forwards': config['port_forwards']})
        self.lab.topology['name'] = 'Edited'
        with self.assertRaisesRegex(lab_server.LabError, 'between settings pages'):
            self.api.internet_settings('cloud', expected_revision=revision)

    def test_preview_apply_preserves_running_pc_and_retry_does_not_reapply(self):
        pc = self.lab.runtime['pc'] = {'test_marker': 'same-runtime', 'iol_id': self.lab.node('pc')['iol_id']}
        before = self.lab.topology_path.read_bytes()
        draft = self.preview({'routes': [{'destination': '10.44.0.0/24', 'via': '192.0.2.2'}],
                              'port_forwards': [{'protocol': 'tcp', 'external_port': 22022,
                                                 'target_address': '10.44.0.10', 'target_port': 22}]})
        self.assertEqual(before, self.lab.topology_path.read_bytes())
        candidate = self.api.internet_settings('cloud', proposal_id=draft['proposal_id'])['internet']
        candidate['port_forwards'][0]['external_port'] = 9999
        with patch.object(self.lab, 'update_internet', wraps=self.lab.update_internet) as update:
            applied = self.api.apply_internet(draft['proposal_id'])
            self.api.apply_internet(draft['proposal_id'])
            self.assertEqual(update.call_count, 1)
        self.assertEqual(self.lab.node('cloud')['internet']['port_forwards'][0]['external_port'], 22022)
        self.assertIs(self.lab.runtime['pc'], pc)
        self.assertEqual(applied['revision'], self.api.state()['revision'])

    def test_live_firewall_uses_backend_and_network_edits_require_gateway_stop(self):
        self.lab.runtime['cloud'] = {'cloud': True, 'cloud_container_id': 'test-cid', 'iol_id': self.lab.node('cloud')['iol_id']}
        pc = self.lab.runtime['pc'] = {'test_marker': 'same-runtime', 'iol_id': self.lab.node('pc')['iol_id']}
        policy = copy.deepcopy(self.lab.node('cloud')['internet'])
        policy['firewall']['default_action'] = 'deny'
        draft = self.preview(policy)
        with patch.object(internet_cloud, 'firewall_container', return_value='test-cid'), \
                patch.object(internet_cloud, 'update_firewall') as update:
            self.api.apply_internet(draft['proposal_id'])
            update.assert_called_once()
        self.assertIs(self.lab.runtime['pc'], pc)
        with self.assertRaisesRegex(lab_server.LabError, 'Stop only this Internet node'):
            self.preview({**policy, 'address': '198.51.100.1/24'})

    def test_runtime_and_topology_changes_expiry_and_wrong_node_refused(self):
        draft = self.preview({})
        self.lab.runtime['cloud'] = {'cloud_container_id': 'new-cid'}
        with self.assertRaisesRegex(lab_server.LabError, 'runtime changed'):
            self.api.apply_internet(draft['proposal_id'])
        self.lab.runtime.clear()
        self.lab.topology['nodes'][0]['name'] = 'Edited'
        with self.assertRaisesRegex(lab_server.LabError, 'Workspace changed'):
            self.api.apply_internet(draft['proposal_id'])
        self.api.internet_proposals[draft['proposal_id']]['expires'] = time.monotonic() - 1
        with self.assertRaisesRegex(lab_server.LabError, 'expired'):
            self.api.apply_internet(draft['proposal_id'])
        with self.assertRaisesRegex(lab_server.LabError, 'Only Internet'):
            self.api.start_internet('pc', self.api.state()['revision'])

    def test_gateway_stop_rechecks_runtime_and_leaves_pc_running(self):
        runtime = self.lab.runtime['cloud'] = {'cloud': True, 'iol_id': self.lab.node('cloud')['iol_id']}
        pc = self.lab.runtime['pc'] = {'test_marker': 'same-runtime', 'iol_id': self.lab.node('pc')['iol_id']}
        page = self.api.internet_settings('cloud')
        self.lab.runtime['cloud'] = dict(runtime)
        with self.assertRaisesRegex(lab_server.LabError, 'runtime changed'):
            self.api.stop_internet('cloud', page['revision'], page['runtime_revision'])
        page = self.api.internet_settings('cloud')
        result = self.api.stop_internet('cloud', page['revision'], page['runtime_revision'])
        self.assertTrue(result['stopped'])
        self.assertNotIn('cloud', self.lab.runtime)
        self.assertIs(self.lab.runtime['pc'], pc)
        with self.assertRaisesRegex(lab_server.LabError, 'Only Internet'):
            self.api.stop_internet('pc', result['revision'], 'stopped')

    def test_http_opt_in_and_validation(self):
        page = self.fixture.request('/api/automation/internet-settings', 'POST', {'node_id': 'cloud'})
        self.assertEqual(page['internet']['address'], '192.0.2.1/24')
        for options in ({'route_offset': -1}, {'page_size': 9}, {'rule_offset': True}):
            with self.subTest(options=options), self.assertRaises(lab_server.LabError):
                self.api.internet_settings('cloud', **options)
        self.lab.automation = None
        from urllib.error import HTTPError
        with self.assertRaises(HTTPError):
            self.fixture.request('/api/automation/internet-settings', 'POST', {'node_id': 'cloud'})
