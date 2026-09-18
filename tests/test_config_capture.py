"""Configuration capture uses the real shared wrapper against a fake IOS CLI."""
import copy
import json
from pathlib import Path
import threading
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

import cisco_config
import console_capture
import lab_server
import test_lab

FAKE_CLI = r'''
pending=b''
paging=False
config_mode=False
def state():
    return Path('logging_state').read_text() if Path('logging_state').exists() else ''
def prompt():
    return b'Capture(config)#' if config_mode or Path('config_mode').exists() else b'Capture#'
def emit(text):
    os.write(1,text.encode()+b'\r\n'+prompt())
while True:
    data=os.read(0,65536)
    if not data:break
    with Path('input_bytes').open('ab') as recorded:recorded.write(data)
    for char in data:
        if char in (3,26):
            pending=b'';paging=False
            if char==26:config_mode=False
            os.write(1,b'\r\n'+prompt())
        elif paging and char==32:
            tail=b' ip address 192.0.2.1 255.255.255.0\r\nbanner motd ^C\r\nPractice lab\r\nno logging console\r\n^C\r\n'
            if Path('inject_capture_log').exists():tail+=b'*Sep 11 12:00:00: %LINK-3-UPDOWN: Interface Ethernet0/0, changed state to down\r\n'
            if Path('single_capture_log').exists():
                tail+=b'*Sep 11 12:00:00: %SYS-4-LOG_CONFIG_CHANGE: Console logging disabled\r\n'
                Path('single_capture_log').unlink()
            if Path('oversize_capture').exists():tail+=b'!'+b'x'*16384+b'\r\n'
            if not Path('incomplete_capture').exists():tail+=b'end\r\n'
            os.write(1,b'\x08'*9+b' '*9+b'\x08'*9+tail+prompt())
            paging=False
        elif paging and char==ord('q'):
            paging=False;os.write(1,b'\r\n'+prompt())
        elif char==21:
            pending=b''
        elif char==13:
            command=pending.decode();pending=b''
            with Path('commands').open('a') as log:log.write(command+'\n')
            if not command:
                os.write(1,b'\r\n'+prompt());continue
            os.write(1,command.encode()+b'\r\n')
            if command=='configure terminal':
                config_mode=True;emit('Enter configuration commands, one per line.')
            elif command=='end':
                config_mode=False;emit('')
            elif command=='show clock':emit('12:00:00 UTC Fri Sep 11 2026')
            elif command=='no logging console':
                if Path('fail_disable').exists():emit('% Invalid input detected')
                else:Path('logging_state').write_text(command);emit('')
            elif command=='default logging console' or command.startswith('logging console'):
                if Path('fail_restore').exists():emit('% Invalid input detected')
                else:Path('logging_state').write_text('' if command=='default logging console' else command);emit('')
            elif command.startswith('show run | inc '):
                prefix=command.split('^',1)[1]
                result=state() if state().startswith(prefix) else ''
                if Path('query_noise').exists():result+='\r\n*Sep 11 12:00:00: %LINK-3-UPDOWN: A log interrupt'
                emit(result)
            elif command=='show log | inc ^ *Console logging:':
                emit('    Console logging: disabled' if state()=='no logging console' and not Path('fail_status').exists() else '    Console logging: level warnings, 3 messages logged, xml disabled,')
            elif command=='show running-config':
                if Path('disconnect_capture').exists():os._exit(0)
                elif Path('hold_capture').exists():os.write(1,b'Waiting for takeover')
                else:
                    config='Building configuration...\r\nCurrent configuration : 200 bytes\r\n!\r\nhostname Capture\r\n'
                    if state():config+=state()+'\r\n'
                    os.write(1,config.encode()+b'interface Ethernet0/0\r\n --More-- ')
                    paging=True
            else:emit('% Invalid input detected')
        else:
            pending+=bytes([char])
'''


class TerminalOutputTests(unittest.TestCase):
    def test_log_splitting_command_echo_retries_query(self):
        first='show run | inc ^logging console'
        second='show run | inc ^no logging console'
        frames=iter([(first+'\r\nlogging console warnings\r\nGuard#').encode(),
                     b'sh\r\r\n*Sep 11 15:48:45: %SYS-5-CONFIG_I: Configured from console by consoleow run | inc ^no logging console\r\r\nGuard#',
                     (second+'\r\nGuard#').encode()])
        client=console_capture.Console.__new__(console_capture.Console)
        client.lock={'mine':True};sent=[]
        client.send=sent.append;client.receive=lambda deadline:next(frames)
        self.assertEqual(cisco_config.CiscoIOS().settings(client,'Guard#'),('logging console warnings',))
        self.assertEqual(sent,[first.encode()+b'\r',second.encode()+b'\r',second.encode()+b'\r'])

    def test_log_attached_to_prompt_retries_query(self):
        command='show log | inc ^ *Console logging:'
        frames=iter([(command+'\r\n    Console logging: disabled\r\n*Sep 11 15:48:45: %SYS-5-CONFIG_I: Configured from console by consoleGuard#').encode(),
                     (command+'\r\n    Console logging: disabled\r\nGuard#').encode()])
        client=console_capture.Console.__new__(console_capture.Console)
        client.lock={'mine':True};sent=[]
        client.send=sent.append;client.receive=lambda deadline:next(frames)
        self.assertFalse(cisco_config.CiscoIOS().enabled(client,'Guard#'))
        self.assertEqual(sent,[command.encode()+b'\r']*2)

    def test_banner_delimiters_preserve_body_and_avoid_message_characters(self):
        config = 'banner exec ^C\nKeep | and # unchanged\n^C\nend\n'
        self.assertEqual(cisco_config.portable_banners(config),
                         'banner exec ~\nKeep | and # unchanged\n~\nend\n')

    def test_iosv_pager_erasure_preserves_configuration_indentation(self):
        pager = b' --More-- ' + b'\x08' * 10 + b' ' * 10 + b'\x08' * 10
        raw = (b'interface GigabitEthernet0/0\r\r\n' + pager +
               b' ip address 192.0.2.1 255.255.255.0\r\r\n' + pager + b'end\r\r\nRouter#')
        self.assertEqual(console_capture.clean_output(raw),
                         'interface GigabitEthernet0/0\n ip address 192.0.2.1 255.255.255.0\nend\nRouter#')


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_lab.LabTests();self.fixture.setUp()
        self.lab=self.fixture.lab
        image=self.fixture.root/'fake.bin'
        text=image.read_text();text=text[:text.index('while True:')]+FAKE_CLI
        image.write_text(text)
        self.lab.start_all()

    def tearDown(self): self.fixture.tearDown()

    def test_export_running_configs_preserves_topology_and_releases_locks(self):
        before=copy.deepcopy(self.lab.topology)
        result=self.fixture.request('/api/export/initial-configs','POST',{})
        self.assertEqual(self.lab.topology,before)
        for node in result['nodes']:
            self.assertIn('hostname Capture\n',node['startup_config'])
            self.assertIn(' ip address 192.0.2.1 255.255.255.0\n',node['startup_config'])
            self.assertNotIn('--More--',node['startup_config'])
            self.assertIn('banner motd |\nPractice lab\nno logging console\n|\n',node['startup_config'])
            self.assertTrue(node['startup_config'].endswith('end\n'))
        # Each finished capture releases its input lock.
        client=console_capture.Console(self.lab.runtime['r1']['port'])
        try: client.acquire()
        finally: client.close()

    def test_logging_settings_restored_and_temporary_commands_excluded(self):
        directory=self.lab.node_dir('r1')
        for original in ('', 'logging console warnings', 'logging console discriminator FILTER warnings',
                         'logging console xml errors', 'no logging console'):
            with self.subTest(original=original):
                (directory/'logging_state').write_text(original)
                (directory/'commands').write_text('')
                (directory/'input_bytes').write_bytes(b'')
                client=console_capture.Console(self.lab.runtime['r1']['port'])
                try:
                    import time
                    deadline=time.monotonic()+3
                    while client.lock is None or client.lock['locked']:client.receive(deadline)
                    client.acquire()
                    result=cisco_config.CiscoIOS().capture(client)
                finally:client.close()
                self.assertEqual((directory/'logging_state').read_text(),original)
                commands=(directory/'commands').read_text().splitlines()
                if original=='no logging console':
                    self.assertNotIn('configure terminal',commands)
                    self.assertNotIn('default logging console',commands)
                else:
                    self.assertIn('no logging console',commands)
                    self.assertIn('default logging console',commands)
                import re
                unbannered=re.sub(r'(?ms)^banner motd \|.*?\|\n','',result)
                self.assertEqual(re.findall(r'(?m)^(?:no )?logging console.*$',unbannered),
                                 [original] if original else [])
                self.assertIn('Practice lab\nno logging console\n',result,'Banner content is preserved')
                self.assertNotIn('write memory',commands)
                self.assertFalse(any(command.startswith('copy ') for command in commands))
                self.assertNotIn(b'\x03',(directory/'input_bytes').read_bytes())
                self.assertNotIn(b'\x1a',(directory/'input_bytes').read_bytes())

    def test_interrupted_or_incomplete_capture_restores_logging(self):
        directory=self.lab.node_dir('r1')
        for marker, error in [('inject_capture_log','contains a log message'),
                              ('incomplete_capture','incomplete'),('oversize_capture','16 KiB')]:
            with self.subTest(marker=marker):
                (directory/'logging_state').write_text('logging console warnings')
                (directory/marker).touch()
                try:
                    with self.assertRaisesRegex(ValueError,error):
                        console_capture.running_config(self.lab.runtime['r1']['port'])
                    self.assertEqual((directory/'logging_state').read_text(),'logging console warnings')
                finally:(directory/marker).unlink()

    def test_noisy_preflight_does_not_change_logging(self):
        directory=self.lab.node_dir('r1');(directory/'query_noise').touch()
        with self.assertRaisesRegex(ValueError,'interrupted'):
            console_capture.export(self.lab)
        self.assertNotIn('configure terminal',(directory/'commands').read_text())
        self.assertFalse((directory/'logging_state').exists())

    def test_queued_log_discards_whole_response_and_retries_capture(self):
        directory=self.lab.node_dir('r1')
        (directory/'logging_state').write_text('logging console warnings');(directory/'single_capture_log').touch()
        result=console_capture.running_config(self.lab.runtime['r1']['port'])
        self.assertNotIn('%SYS-',result)
        self.assertEqual((directory/'logging_state').read_text(),'logging console warnings')
        commands=(directory/'commands').read_text().splitlines()
        self.assertEqual(commands.count('show running-config'),2)
        self.assertEqual(commands.count('no logging console'),1)

    def test_suppression_must_be_verified_before_reading_configuration(self):
        directory=self.lab.node_dir('r1')
        (directory/'logging_state').write_text('logging console warnings');(directory/'fail_status').touch()
        with self.assertRaisesRegex(ValueError,'could not be suppressed'):
            console_capture.running_config(self.lab.runtime['r1']['port'])
        self.assertEqual((directory/'logging_state').read_text(),'logging console warnings')
        self.assertNotIn('show running-config',(directory/'commands').read_text().splitlines())

    def test_rejected_disable_returns_to_exec_without_changing_settings(self):
        directory=self.lab.node_dir('r1')
        (directory/'logging_state').write_text('logging console errors');(directory/'fail_disable').touch()
        with self.assertRaisesRegex(ValueError,'rejected export command'):
            console_capture.running_config(self.lab.runtime['r1']['port'])
        self.assertEqual((directory/'logging_state').read_text(),'logging console errors')
        self.assertNotIn('default logging console',(directory/'commands').read_text())

    def test_restore_failure_reports_manual_recovery_and_exports_no_file(self):
        directory=self.lab.node_dir('r1')
        (directory/'logging_state').write_text('logging console warnings');(directory/'fail_restore').touch()
        with self.assertRaises(HTTPError) as raised:
            self.fixture.request('/api/export/initial-configs','POST',{})
        message=json.load(raised.exception)['error']
        self.assertIn('restoration could not be verified',message)
        self.assertIn('No file exported',message)
        self.assertIn('default logging console\nlogging console warnings\nend',message)
        self.assertEqual((directory/'logging_state').read_text(),'no logging console')
        self.assertFalse((self.lab.node_dir('r2')/'logging_state').exists())

    def test_disconnect_reports_unverified_restoration(self):
        directory=self.lab.node_dir('r1')
        (directory/'logging_state').write_text('logging console warnings');(directory/'disconnect_capture').touch()
        with self.assertRaisesRegex(ValueError,'restoration could not be verified'):
            console_capture.running_config(self.lab.runtime['r1']['port'])
        self.assertEqual((directory/'logging_state').read_text(),'no logging console')

    def test_unsupported_handler_is_rejected_before_opening_any_console(self):
        self.lab.topology['nodes'][1]['image']='unsupported.img'
        with patch.object(console_capture,'Console',side_effect=AssertionError('must preflight all devices')):
            with self.assertRaisesRegex(ValueError,'No configuration export handler'):
                console_capture.export(self.lab)

    def test_locked_or_non_exec_console_refused_without_reconfiguration(self):
        client=console_capture.Console(self.lab.runtime['r1']['port'])
        try:
            client.acquire()
            with self.assertRaisesRegex(ValueError,'Release this device'):
                console_capture.export(self.lab)
        finally:client.close()
        # Ensure the release is observed before testing the prompt mode.
        client=console_capture.Console(self.lab.runtime['r1']['port'])
        try:
            import time
            deadline=time.monotonic()+3
            while client.lock is None or client.lock['locked']:client.receive(deadline)
        finally:client.close()
        (self.lab.node_dir('r1')/'config_mode').touch()
        with self.assertRaisesRegex(ValueError,'privileged EXEC'):
            console_capture.export(self.lab)

    def test_stopped_device_export_rejected(self):
        self.lab.stop('r2')
        with self.assertRaisesRegex(ValueError,'Start r2'):
            console_capture.export(self.lab)

    def test_takeover_aborts_capture(self):
        import time
        port=self.lab.runtime['r1']['port']
        (self.lab.node_dir('r1')/'hold_capture').touch()
        observer=console_capture.Console(port)
        errors=[]
        def capture():
            try:console_capture.running_config(port)
            except ValueError as exc:errors.append(str(exc))
        thread=threading.Thread(target=capture);thread.start()
        try:
            deadline=time.monotonic()+5
            output=b''
            while b'Waiting for takeover' not in output:output+=observer.receive(deadline)
            observer.send(json.dumps({'action':'takeover','revision':observer.lock['revision']}).encode(),1)
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertTrue(errors)
            self.assertIn('took over',errors[0])
            self.assertIn('restoration could not be verified',errors[0])
            self.assertEqual((self.lab.node_dir('r1')/'logging_state').read_text(),'no logging console')
            self.assertNotIn('default logging console',(self.lab.node_dir('r1')/'commands').read_text())
        finally:
            observer.close();thread.join(35)


if __name__=='__main__':unittest.main()
