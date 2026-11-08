"""Capture hints and scripted CLI regressions; no inference or vendor guest boots."""
import json
import unittest

import console_capture
import console_evidence
import lab_automation
import lab_server
import test_lab


class EvidenceTests(unittest.TestCase):
    def evidence(self, text, image='cisco_iol-17.18.02.bin', **flags):
        return console_evidence.summarize({'type': 'router', 'image': image}, {
            'output': text, 'capture_end': 'timeout', 'gap': False, **flags})

    def test_latest_tail_supersedes_historical_prompts(self):
        history = 'SW1#terminal length 0\nSW1#\nSession expired\nPress RETURN to get started.'
        result = self.evidence(history)
        self.assertEqual(result['tail_hint'], 'press_return')
        self.assertIsNone(result['pending_input'])
        self.assertEqual(self.evidence('SW1#\nSession expired')['tail_hint'], 'session_ended')
        self.assertEqual(self.evidence('SW1#\nBooting services...')['tail_hint'], 'unknown')
        self.assertEqual(self.evidence('SW1#\nUsername:')['tail_hint'], 'login')
        self.assertEqual(self.evidence('SW1>enable\nPassword:')['tail_hint'], 'password')
        self.assertEqual(self.evidence('Erase configuration? [confirm]')['tail_hint'], 'confirmation')

    def test_empty_prompt_guidance_does_not_prescribe_another_enter(self):
        for tail in ('SW4>', 'SW4#', 'SW4(config-if)#'):
            result = {'output': tail, 'capture_end': 'timeout'}
            evidence = self.evidence(tail)
            action = console_evidence.next_action(result, evidence)
            self.assertIn('send the intended command directly', action)
            self.assertIn('do not press Enter again', action)
        for tail in ('SW4>enable', 'SW4>\nPassword:', 'SW4>\nSession expired'):
            self.assertNotIn('An empty CLI prompt', console_evidence.next_action(
                {'output': tail}, self.evidence(tail)))
        for flags in ({'capture_end': 'limit'}, {'capture_end': 'disconnected'}, {'gap': True}):
            self.assertNotIn('An empty CLI prompt', console_evidence.next_action(
                {'output': 'SW4>', **flags}, self.evidence('SW4>', **flags)))

    def test_paging_dictionary_does_not_invent_prompt_modes(self):
        for image, command in (
            ('cisco_iol-17.18.02.bin', 'terminal length 0'),
            ('quay.io/frrouting/frr:10.7.1', 'terminal length 0'),
            ('vEOS64-lab-4.36.1F.qcow2', 'terminal length 0'),
            ('vJunos-switch-26.2R1.7.qcow2', 'set cli screen-length 0'),
            ('EXOS-VM_33.1.1.31.qcow2', 'disable clipaging'),
        ):
            evidence = self.evidence('Password:', image)
            self.assertEqual(evidence['paging']['disable_command'], command)
            self.assertEqual(evidence['tail_hint'], 'password')
            self.assertIn('inspect prompt', evidence['paging']['scope'])
        self.assertIsNone(self.evidence('SW1#', 'ghcr.io/weblab-network/ll2s:0.2.0-ovs-rstp1')['paging'])
        self.assertIsNone(self.evidence('host:~#', 'alpine:latest')['paging'])

    def test_cisco_modes_pending_input_and_pager(self):
        for text, hint, pending in (
                ('SW1>', 'user_exec', False), ('SW1#', 'privileged_exec', False),
                ('SW1(config)#', 'config', False), ('SW1(config-if)#', 'config', False),
                ('SW1#show clock', 'privileged_exec', True),
                ('SW1#show clock\\r', 'privileged_exec', True),
                ('SW1#^C', 'privileged_exec', True), (' --More-- ', 'pager', None)):
            with self.subTest(text=text):
                evidence = self.evidence(text)
                self.assertEqual((evidence['tail_hint'], evidence['pending_input']), (hint, pending))
        # Actual backspaces erase a pager; literal ^C is never treated as Ctrl+C.
        erased = console_capture.clean_output(b'--More--' + b'\b' * 8 + b' ' * 8 + b'\r\nSW1#')
        self.assertEqual(self.evidence(erased)['tail_hint'], 'privileged_exec')

    def test_incomplete_disconnected_and_idle_reads_never_imply_prompt(self):
        for flags in ({'capture_end': 'limit'}, {'gap': True}, {'error': 'closed'}):
            with self.subTest(flags=flags):
                self.assertEqual(self.evidence('SW1#', **flags)['tail_hint'], 'incomplete')
        for reason in ('disconnected', 'lock_lost'):
            self.assertEqual(self.evidence('SW1#', capture_end=reason)['tail_hint'], 'unavailable')
        self.assertEqual(self.evidence('')['tail_hint'], 'no_new_output')

    def test_errors_are_bounded_capture_markers_not_success_verdicts(self):
        text = 'SW1>show running-config\n% Invalid input detected at marker.\nSW1>'
        evidence = self.evidence(text)
        self.assertEqual(evidence['tail_hint'], 'user_exec')
        self.assertEqual(evidence['error_lines'], ['% Invalid input detected at marker.'])
        self.assertEqual(evidence['scope'], 'this_capture_only')
        self.assertNotIn('success', evidence)
        huge = self.evidence(('\n% Error ' + 'x' * 500) * 100 + '\n' + 'x' * 1000)
        self.assertEqual(len(huge['error_lines']), 2)
        self.assertLessEqual(len(json.dumps(huge)), 1000)

    def test_contextual_guidance_does_not_turn_unknown_or_incomplete_input_into_readiness(self):
        log = '*Nov 5 00:44:29.066: %PKI-6-SUDI_INFO: no certificate is installed'
        def advice(text, **flags):
            result = {'output': text, 'capture_end': 'timeout', 'next_action': 'inspect', **flags}
            evidence = console_evidence.summarize({'type': 'switch', 'image': 'cisco_iol-l2-17.18.02.bin'}, result)
            return evidence, console_evidence.next_action(result, evidence)
        evidence, action = advice('Press RETURN to get started!\n' + log)
        self.assertEqual(evidence['tail_hint'], 'unknown')  # Still not a readiness verdict.
        self.assertIn('key="enter" once', action)
        for tail in ('Username:', 'Password:', 'Erase? [confirm]', 'SW1#unfinished', '', 'Still processing...'):
            with self.subTest(tail=tail):
                self.assertEqual(advice(log + '\n' + tail if tail else '')[1], 'inspect')
        for flags in ({'capture_end': 'limit'}, {'capture_end': 'disconnected'}, {'gap': True}):
            self.assertEqual(advice(log, **flags)[1], 'inspect')
        self.assertIn('not complete command output', advice('config lines\n--More--')[1])
        self.assertNotIn('key="enter"', advice('config lines\n--More--')[1])

    def test_junos_is_not_parsed_as_cisco(self):
        image = 'vJunos-switch-26.2R1.7.qcow2'
        for text, hint in (('root@R1>', 'operational'), ('[edit]\nroot@R1#', 'config'),
                           ('root@R1%', 'shell'), ('root@R1#', 'unknown'),
                           ('root@R1:~ #', 'unknown'), ('---(more 25%)---', 'pager'),
                           ('SW1#', 'unknown')):
            with self.subTest(text=text):
                self.assertEqual(self.evidence(text, image)['tail_hint'], hint)
        self.assertEqual(self.evidence('error: invalid command\nroot@R1>', image)['error_lines'],
                         ['error: invalid command'])

    def test_other_profiles_are_not_given_cisco_modes(self):
        for image in ('quay.io/frrouting/frr:10.7.1', 'ghcr.io/weblab-network/ll2s:0.2.0-ovs-rstp1',
                      'EXOS-VM_33.1.1.31.qcow2', 'vEOS64-lab-4.36.1F.qcow2'):
            with self.subTest(image=image):
                self.assertEqual(self.evidence('SW1#', image)['tail_hint'], 'unknown')


class ConsoleWorkflowTests(unittest.TestCase):
    def test_expiry_privilege_pager_and_literal_enter_over_real_transport(self):
        fixture = test_lab.LabTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        lab = fixture.lab
        history = 'SW1#terminal length 0\r\nSW1#\r\nSession expired\r\nPress RETURN to get started.'
        steps = [
            ('\r', '\r\nSW1>'),
            ('show running-config\r', 'show running-config\r\n% Invalid input detected at marker.\r\nSW1>'),
            ('enable\r', 'enable\r\nPassword:'),
            ('fixture-wrong\r', '\r\n% Bad secrets\r\nSW1>'),
            ('enable\r', 'enable\r\nPassword:'),
            ('fixture-password\r', '\r\nSW1#'),
            ('configure terminal\r', 'configure terminal\r\nSW1(config)#'),
            ('interface Ethernet0/0\r', 'interface Ethernet0/0\r\nSW1(config-if)#'),
            ('end\r', 'end\r\nSW1#'),
            ('show running-config\r', 'show running-config\r\n' + 'interface Ethernet0/0\r\n' * 100 + '--More--'),
            (' ', '\r\nend\r\nSW1#'),
            ('show clock\\r', 'show clock\\r'),
            ('\x15', '\r' + ' ' * 40 + '\rSW1#'),
            ('show clock\r', 'show clock\r\n12:34:56 UTC\r\nSW1#'),
        ]
        image = fixture.root / 'fake.bin'
        prefix = image.read_text().split("os.write(1, b'BOOT READY")[0]
        image.write_text(prefix + '\nimport json, time\n' +
                         f'os.write(1, {history.encode()!r})\n' +
                         f'steps = json.loads({json.dumps(steps)!r})\n' + '''
for expected, response in steps:
    data = b''
    expected = expected.encode()
    while len(data) < len(expected):
        data += os.read(0, 65536)
    assert data == expected, (data, expected)
    os.write(1, response.encode())
while True:
    time.sleep(1)
''')
        api = lab_automation.Automation(lab, lab_server.LabError, lab_server.ports, lab_server.run)
        lab.start('r1')
        current = api.console_read('r1', latest=True, wait_seconds=.2)
        self.assertEqual(current['evidence']['tail_hint'], 'press_return')
        expected_hints = ['user_exec', 'user_exec', 'password', 'user_exec', 'password',
                          'privileged_exec', 'config', 'config', 'privileged_exec', 'pager',
                          'privileged_exec', 'privileged_exec', 'privileged_exec', 'privileged_exec']
        for i, (value, response) in enumerate(steps):
            # Explicit new read even after a send, just as the Agent requires.
            current = api.console_read('r1', cursor=current['cursor'], wait_seconds=.1)
            self.assertEqual(current['evidence']['tail_hint'], 'no_new_output')
            result = api.console_send('r1', value, current['revision'], current['cursor'],
                                      wait_seconds=.2, max_bytes=128 if i == 9 else 4096)
            self.assertEqual(result['input_status'], 'sent')
            self.assertEqual(result['command_completion'], 'unverified')
            if i in (1, 3):
                self.assertTrue(result['evidence']['error_lines'])
            if i == 9:
                self.assertEqual(result['evidence']['tail_hint'], 'incomplete')
                chunks = [result['output']]
                while result['capture_end'] == 'limit':
                    result = api.console_read('r1', cursor=result['cursor'], wait_seconds=.1, max_bytes=128)
                    chunks.append(result['output'])
                self.assertIn('--More--', ''.join(chunks))
                self.assertGreater(len(chunks), 10)
            # Stream chunks need not contain a prompt prefix echoed before this input.
            if i == 11:
                self.assertEqual(result['evidence']['tail_hint'], 'unknown')
                latest = api.console_read('r1', latest=True, wait_seconds=.1)
                self.assertTrue(latest['evidence']['pending_input'])
            else:
                self.assertEqual(result['evidence']['tail_hint'], expected_hints[i], result)
            current = result
        self.assertIn('12:34:56 UTC', current['output'])
        self.assertFalse(api.console_read('r1', wait_seconds=.1)['locked'])


class BulkPromptTests(unittest.TestCase):
    def profile(self, text, image='cisco_iol-17.18.02.bin', kind='router', **flags):
        return console_evidence.bulk_prompt({'type':kind, 'image':image},
            {'output':text, 'capture_end':'timeout', **flags})

    def test_each_vendor_has_an_independent_top_level_grammar(self):
        for image, kind, prompt, expected in (
                ('cisco_iol-17.18.02.bin','router','R#',('cisco_ios','privileged_exec')),
                ('cisco_iol-l2-17.18.02.bin','switch','SW>',('cisco_ios','user_exec')),
                ('quay.io/frrouting/frr:10.7.1','router','r1#',('frr','privileged_exec')),
                ('vEOS64-lab-4.36.1F.qcow2','switch','leaf>',('veos','user_exec')),
                ('vJunos-switch-26.2R1.7.qcow2','switch','root@leaf>',('junos','operational')),
                ('EXOS-VM_33.1.1.31.qcow2','switch','* SW1.12 # ',('exos','exec')),
                ('ghcr.io/weblab-network/ll2s:0.2.0-ovs-rstp1','switch','ll2s#',('ll2s','exec')),
                ('alpine:latest','pc','/ # ',('alpine','root_shell')),
                ('alpine:latest','pc','pc1:~# ',('alpine','root_shell'))):
            with self.subTest(image=image):
                self.assertEqual(self.profile(prompt, image, kind), expected)
                self.assertIsNone(self.profile(prompt+'pending', image, kind))
                self.assertIsNone(self.profile(prompt+'\nPassword:', image, kind))
                self.assertIsNone(self.profile(prompt+'\n--More--', image, kind))

    def test_never_accept_config_shell_mismatch_or_incomplete_output(self):
        for prompt, image, kind in (
                ('R(config-if)#','cisco_iol-17.18.02.bin','router'),
                ('root@leaf%','vJunos-switch-26.2R1.7.qcow2','switch'),
                ('[edit]\nroot@leaf#','vJunos-switch-26.2R1.7.qcow2','switch'),
                ('bash-5.2#','quay.io/frrouting/frr:10.7.1','router'),
                ('root@frr:~#','quay.io/frrouting/frr:10.7.1','router'),
                ('[root@leaf ~]#','vEOS64-lab-4.36.1F.qcow2','switch'),
                ('ll2s:~#','ghcr.io/weblab-network/ll2s:0.2.0-ovs-rstp1','switch'),
                ('R#','alpine:latest','pc'), ('pc:~#','mystery','router')):
            with self.subTest(prompt=prompt):
                self.assertIsNone(self.profile(prompt, image, kind))
        for flags in ({'capture_end':'limit'}, {'capture_end':'disconnected'}, {'gap':True},
                      {'error':'lost'}, {'unread_cursor':'epoch:5'}):
            self.assertIsNone(self.profile('R#', **flags))
