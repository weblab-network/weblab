"""Guarded cable automation against disposable PTYs and local Ethernet relays."""
import copy
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

import lab_automation
import lab_server
import qmp
import test_lab
import test_agent


class LinkAutomationTests(unittest.TestCase):
    node = staticmethod(test_lab.LabTests.node)
    request = test_lab.LabTests.request
    tearDown = test_lab.LabTests.tearDown

    def setUp(self):
        test_lab.LabTests.setUp(self)
        self.api = self.lab.automation = lab_automation.Automation(
            self.lab, lab_server.LabError, lab_server.ports, lab_server.run)
        self.link = self.lab.topology['links'][0]['id']

    def start(self):
        self.lab.start('r1')
        self.api.console_read('r1', wait_seconds=.1)

    def args(self):
        state = self.api.link_state(self.link)
        return {'link_id': self.link, 'expected_revision': state['revision'],
                'expected_link_revision': state['link_revision']}

    def test_read_unavailable_validation_and_http_controls(self):
        state = self.api.link_state(self.link)
        self.assertFalse(state['state']['available'])
        self.assertEqual(state['a']['node'], 'r1')
        with self.assertRaisesRegex(lab_server.LabError, 'unavailable'):
            self.api.link_frame_loss(**self.args(), blocked_a_to_b=True, blocked_b_to_a=False)
        for bad in ('missing', '../x', None):
            with self.assertRaises(lab_server.LabError): self.api.link_state(bad)
        self.start()
        original = copy.deepcopy(self.lab.topology)
        for bad in (1, 'true', None):
            with self.assertRaisesRegex(lab_server.LabError, 'boolean'):
                self.api.link_frame_loss(**self.args(), blocked_a_to_b=bad, blocked_b_to_a=False)
        state = self.request('/api/automation/link-state', 'POST', {'link_id': self.link})
        args = {**self.args(), 'blocked_a_to_b': True, 'blocked_b_to_a': False}
        result = self.request('/api/automation/link-frame-loss', 'POST', args)
        self.assertTrue(result['applied'])
        self.assertTrue(result['state']['blocked_a_to_b'])
        self.assertFalse(result['state']['blocked_b_to_a'])
        self.assertNotEqual(state['link_revision'], result['link_revision'])
        self.assertEqual(original, self.lab.topology)
        with self.assertRaisesRegex(lab_server.LabError, 'changed'):
            self.api.link_frame_loss(**args)
        # Fresh state and an explicit clear, not a topology reset.
        cleared = self.api.link_frame_loss(**self.args(), blocked_a_to_b=False, blocked_b_to_a=False)
        self.assertFalse(cleared['state']['blocked_a_to_b'])

    def test_aba_restart_socket_replacement_and_layout_guards(self):
        self.start()
        args = self.args()
        self.lab.set_link_traffic(self.link, dict(blocked_a_to_b=True, blocked_b_to_a=False))
        self.lab.set_link_traffic(self.link, dict(blocked_a_to_b=False, blocked_b_to_a=False))
        with self.assertRaisesRegex(lab_server.LabError, 'changed'):
            self.api.link_frame_loss(**args, blocked_a_to_b=True, blocked_b_to_a=True)
        args = self.args()
        self.lab.stop('r1', dependents=False); self.start()
        with self.assertRaisesRegex(lab_server.LabError, 'changed'):
            self.api.link_frame_loss(**args, blocked_a_to_b=True, blocked_b_to_a=True)
        args = self.args()
        path = self.lab.netio / str(self.lab.node('r1')['iol_id'])
        path.unlink()
        replacement = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            replacement.bind(str(path))
            with self.assertRaisesRegex(lab_server.LabError, 'changed'):
                self.api.link_frame_loss(**args, blocked_a_to_b=True, blocked_b_to_a=True)
        finally:
            replacement.close(); path.unlink(missing_ok=True)
        args = self.args()
        self.lab.topology['nodes'][0]['x'] += 20
        self.assertTrue(self.api.link_frame_loss(**args, blocked_a_to_b=False, blocked_b_to_a=True)['applied'])
        args = self.args()
        self.lab.topology['nodes'][0]['name'] = 'Another device'
        with self.assertRaisesRegex(lab_server.LabError, 'Workspace changed'):
            self.api.link_frame_loss(**args, blocked_a_to_b=False, blocked_b_to_a=False)

    def test_carrier_profile_guard_independence_and_uncertain_result(self):
        self.start()
        with self.assertRaisesRegex(lab_server.LabError, 'not enabled/supported'):
            self.api.link_carrier(**self.args(), side='a', up=False)
        self.lab.runtime['r1']['qemu_sockets'] = str(self.root / 'fake-qmp')
        with patch('lab_server.vios.is_vios', return_value=True), patch('qmp.set_link') as control:
            self.api.link_frame_loss(**self.args(), blocked_a_to_b=True, blocked_b_to_a=False)
            down = self.api.link_carrier(**self.args(), side='a', up=False)
            self.assertTrue(down['applied']); self.assertEqual(down['state']['carrier_a'], 'down')
            control.assert_called_once_with(self.root / 'fake-qmp' / 'qmp', 0, False)
            clear = self.api.link_frame_loss(**self.args(), blocked_a_to_b=False, blocked_b_to_a=False)
            self.assertEqual(clear['state']['carrier_a'], 'down')
            self.api.link_frame_loss(**self.args(), blocked_a_to_b=False, blocked_b_to_a=True)
            control.side_effect = qmp.QMPError('timeout')
            failed = self.api.link_carrier(**self.args(), side='a', up=True)
            self.assertFalse(failed['applied']); self.assertEqual(failed['outcome'], 'uncertain')
            self.assertEqual(failed['state']['carrier_a'], 'unknown')
            self.assertEqual(control.call_count, 2)  # No retry after a possibly applied command.
            control.side_effect = None
            result = self.api.link_carrier(**self.args(), side='a', up=True)
            self.assertEqual(result['state']['carrier_a'], 'up')
            self.assertTrue(result['state']['blocked_b_to_a'])

    def test_countdown_does_not_expire_read_but_error_does(self):
        self.start()
        original = self.lab.link_states
        pending = {'pending': 9, 'error': ''}
        def states():
            result = original()
            result[self.link].update(carrier_pending_a=pending['pending'], carrier_error_a=pending['error'])
            return result
        with patch.object(self.lab, 'link_states', side_effect=states):
            args = self.args(); pending['pending'] = 2
            self.assertTrue(self.api.link_frame_loss(**args, blocked_a_to_b=True, blocked_b_to_a=False)['applied'])
            args = self.args(); pending['error'] = 'L1 socket replaced'
            with self.assertRaisesRegex(lab_server.LabError, 'changed'):
                self.api.link_frame_loss(**args, blocked_a_to_b=False, blocked_b_to_a=False)


class LinkApprovalTests(unittest.TestCase):
    setUp = test_agent.AgentApprovalTests.setUp
    finish = test_agent.AgentApprovalTests.finish
    launch = test_agent.AgentApprovalTests.launch
    decide = test_agent.AgentApprovalTests.decide

    def args(self):
        self.lab.start('r1')
        self.bridge.automation.console_read('r1', wait_seconds=.1)
        self.link = self.lab.topology['links'][0]['id']
        state = self.bridge.tool(self.session, 'POST', '/api/automation/link-state', {'link_id': self.link}, self.turn)
        return {'link_id': self.link, 'expected_revision': state['revision'],
                'expected_link_revision': state['link_revision'], 'blocked_a_to_b': True, 'blocked_b_to_a': False}

    def test_separate_permission_exact_approval_and_single_use(self):
        args = self.args()
        with self.assertRaisesRegex(ValueError, 'Enable link_faults'):
            self.bridge.tool(self.session, 'POST', '/api/automation/link-frame-loss', args, self.turn)
        self.session['settings'].update(link_faults=True, lab_changes=False, console_input=False)
        thread, result, item = self.launch('link-frame-loss', args)
        self.assertEqual(item['context']['a']['node'], 'r1')
        self.assertEqual(item['context']['b']['node'], 'r2')
        self.assertFalse(self.lab.link_states()[self.link]['blocked_a_to_b'])
        args['blocked_b_to_a'] = True  # Approval owns a deep copy.
        self.decide(item); thread.join(3)
        self.assertNotIn('error', result)
        self.assertTrue(result['result']['state']['blocked_a_to_b'])
        self.assertFalse(result['result']['state']['blocked_b_to_a'])
        with self.assertRaisesRegex(ValueError, 'Read get_link_state'):
            self.bridge.tool(self.session, 'POST', '/api/automation/link-frame-loss', item['arguments'], self.turn)
        with self.assertRaises(ValueError): self.decide(item)

    def test_stale_approval_and_cross_session_read(self):
        args = self.args(); self.session['settings']['link_faults'] = True
        thread, result, item = self.launch('link-frame-loss', args)
        self.lab.set_link_traffic(self.link, dict(blocked_a_to_b=False, blocked_b_to_a=True))
        self.decide(item); thread.join(3)
        self.assertIn('changed', result['error'])
        self.assertFalse(self.lab.link_states()[self.link]['blocked_a_to_b'])
        args = self.args()
        other = self.bridge.resolve(self.bridge.api('session')['token'])
        other['settings'].update(link_faults=True, auto_approve=True)
        self.bridge.turns[other['id']] = {'id': self.turn, 'expires': self.bridge.turns[self.session['id']]['expires']}
        with self.assertRaisesRegex(ValueError, 'Read get_link_state'):
            self.bridge.tool(other, 'POST', '/api/automation/link-frame-loss', args, self.turn)
        # Ordinary browser mutation routes never become available to the companion.
        with self.assertRaisesRegex(ValueError, 'not permitted'):
            self.bridge.tool(self.session, 'POST', '/api/links/'+self.link+'/traffic',
                             dict(blocked_a_to_b=True, blocked_b_to_a=True), self.turn)

    def test_denial_expiry_yolo_and_revocation(self):
        args = self.args(); self.session['settings']['link_faults'] = True
        thread, result, item = self.launch('link-frame-loss', args)
        self.decide(item, False); thread.join(3)
        self.assertIn('denied', result['error'])
        self.assertFalse(self.lab.link_states()[self.link]['blocked_a_to_b'])
        thread, result, item = self.launch('link-frame-loss', args)
        self.bridge.turns[self.session['id']]['expires'] = 0
        with self.assertRaises(ValueError): self.decide(item)
        self.bridge.revoke(self.session); thread.join(3)
        self.assertFalse(self.lab.link_states()[self.link]['blocked_a_to_b'])
        self.assertNotIn(self.session['id'], self.bridge.link_observations)
        import time
        self.bridge.turns[self.session['id']] = {'id': self.turn, 'expires': time.monotonic()+30}
        args = self.args(); self.session['settings']['auto_approve'] = True
        self.assertTrue(self.bridge.tool(self.session, 'POST', '/api/automation/link-frame-loss', args, self.turn)['applied'])
        self.session['settings']['link_faults'] = False
        args = self.args()
        with self.assertRaisesRegex(ValueError, 'Enable link_faults'):
            self.bridge.tool(self.session, 'POST', '/api/automation/link-frame-loss', args, self.turn)


class LinkCompanionTests(unittest.TestCase):
    setUp = test_agent.CompanionTests.setUp
    tearDown = test_agent.CompanionTests.tearDown
    wait = test_agent.CompanionTests.wait

    def test_link_tools_only_enabled_by_own_permission(self):
        for enabled in (False, True):
            self.settings.update(link_faults=enabled, lab_changes=True, console_input=True)
            self.manager.handle('turn', {**self.payload, 'prompt': 'Read the cable', 'request_id': ('b' if enabled else 'c')*32})
            session = self.manager.sessions['a'*32]
            self.wait(lambda: session.status == 'running')
            test_agent.FakeRPC.instances[-1].events.put({'method':'item/completed', 'params':{'turnId':'turn', 'item':{'type':'agentMessage','id':'answer','text':'Read cable state'}}})
            test_agent.FakeRPC.instances[-1].events.put({'method':'turn/completed', 'params':{'turn':{'id':'turn','status':'completed'}}})
            self.wait(lambda: not session.worker.is_alive())
            self.assertEqual(session.status, 'completed')
            config = test_agent.FakeRPC.instances[-1].config
            names = config['mcp_servers.weblab.enabled_tools']
            self.assertIn('get_link_state', names)
            self.assertEqual('--allow-link-faults' in config['mcp_servers.weblab.args'], enabled)
            for name in ('set_link_carrier', 'set_link_frame_loss'):
                self.assertEqual(name in names, enabled)
                if enabled: self.assertEqual(config[f'mcp_servers.weblab.tools.{name}.approval_mode'], 'approve')
