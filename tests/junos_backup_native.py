"""Opt-in Junos ZIP/forwarding/shutdown test using only fresh disposable nodes.

Requires Intel KVM, Docker/Alpine, both exact 26.2R1.7 images and 14+ GiB RAM.
Run in a dedicated development container with /dev/kvm, /dev/net/tun,
NET_ADMIN and the Docker socket, mounting the supplied images read-only:
  python3 -u tests/junos_backup_native.py --image-dir /iou --work-parent /data/native-tests

Three boots verify CLI shutdown -> ZIP restore -> guarded Stop -> persistence.
Evidence stays under work-parent. On failure, cleanup first requests graceful
shutdown; only this test's disposable guests may then be forcibly cleaned up.
Never point work-parent at an existing node directory. No live lab is imported
or changed; each run creates its own manager, socket identity and data directory.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lab_backup
import lab_server
import vios

# The fixture owns these guest names. Boot logs may follow the prompt without
# a newline; Terminal.match excludes the submitted command's own echoed prompt.
OP = r'(?m)^root(?:@[\w.-]+)?>'
CONFIG = r'(?m)^root(?:@[\w.-]+)?#'
SHELL = r'(?m)^(?:\[vrf:[^\]]+\]\s+)?root@[^\s:>#%]*:[^\n#$%]*[#$%]'
PC = r'(?m)^/ #\s*$'
CLI_RETRY = r'Retry connection attempts \? \[yes,no\] \(yes\)'
IMAGES = ('vJunos-switch-26.2R1.7.qcow2', 'vJunosEvolved-26.2R1.7-EVO.qcow2')


class Terminal:
    def __init__(self, lab, node, directory, redactions):
        self.lab, self.node, self.directory, self.redactions = lab, node, directory, redactions
        self.last, self.output = None, ''
        self.last_input = None

    def accept(self, result):
        assert not result.get('gap') and not result.get('error'), result
        self.last = result
        self.output = (self.output + result.get('output', ''))[-65536:]
        text = result.get('output', '')
        for secret in self.redactions:
            text = text.replace(secret, '[REDACTED]')
        with (self.directory / (self.node + '.log')).open('a') as stream:
            stream.write(text)
        return result

    def poll(self):
        return self.accept(self.lab.automation.console_read(
            self.node, cursor=self.last['cursor'] if self.last else None,
            wait_seconds=1, max_bytes=65536))

    def match(self, pattern):
        matches = []
        for match in re.finditer(pattern, self.output):
            if match.group().endswith(('>', '#', '%')) and self.last_input:
                suffix = self.output[match.end():].split('\n', 1)[0].strip()
                if suffix and (self.last_input.startswith(suffix) or suffix.startswith(self.last_input)
                               or suffix.startswith('...')):
                    continue  # Full/partial echo or Junos's abbreviated input redraw.
            matches.append(match)
        return matches[-1] if matches else None

    def wait(self, pattern, timeout=30):
        deadline = time.monotonic() + timeout
        while not self.match(pattern):
            if time.monotonic() >= deadline:
                raise AssertionError(f'{self.node}: timed out waiting for {pattern}; see console log')
            self.poll()
        return self.output

    def send(self, text):
        for attempt in range(3):
            # Drain new output and bind each input to the observed runtime/cursor.
            self.poll()
            for _ in range(32):
                if self.last['capture_end'] != 'limit':
                    break
                self.poll()
            assert self.last['capture_end'] != 'limit', 'Undrained console history'
            try:
                result = self.lab.automation.console_send(
                    self.node, text + '\r', self.last['revision'], self.last['cursor'],
                    wait_seconds=1, max_bytes=65536)
            except lab_server.LabError as exc:
                # Retry only the explicitly rejected, never-submitted input.
                if 'no input was sent' in str(exc).lower() and attempt < 2:
                    continue
                raise
            assert result.get('input_status') == 'sent', 'Uncertain delivery; refusing to replay input'
            self.output = ''
            self.last_input = text
            self.accept(result)
            return

    def command(self, text, prompt=OP, timeout=30):
        self.send(text)
        output = self.wait(prompt, timeout)
        assert not re.search(r'(?m)^(?:error:|syntax error|unknown command)', output), output
        return output

    def login(self, password=None):
        # The switch's outer Linux prints qemux86-64 login before its nested
        # Junos VM boots. Only accept the inner guest's known login names (or
        # its bare login prompt). Boot messages may be appended after it.
        login_prompt = r'(?m)^(?:(?:re0|JZR|Amnesiac|JZSW)\s+)?login:'
        self.wait(login_prompt, 600)
        self.send('root')
        if password is not None:
            self.wait(r'(?m)^Password:', 20)
            self.send(password)
        self.wait(SHELL, 30)
        self.enter_cli()

    def enter_cli(self):
        self.send('cli')
        deadline = time.monotonic() + 180
        while True:
            output = self.wait(OP + '|' + CLI_RETRY, max(1, deadline - time.monotonic()))
            if self.match(OP):
                break
            assert time.monotonic() < deadline, 'Management daemon did not become ready'
            # Junos offers this explicit retry while mgd starts. This is a reply
            # to its observed question, not a replay of an uncertain command.
            time.sleep(3)
            self.send('yes')

    def ready(self):
        deadline, online = time.monotonic() + 240, 0
        while time.monotonic() < deadline:
            self.poll()
            if self.match(SHELL):
                self.enter_cli()
            self.send('show chassis fpc | no-more')
            output = self.wait('(?m)(?:' + OP.replace('(?m)', '') + ')|(?:' + SHELL.replace('(?m)', '') + ')', 30)
            if self.match(SHELL):
                # An observed shell after a closed management connection is a
                # definite failure of this read (or a shell rejection of show).
                # Re-enter CLI; never replay edits.
                self.enter_cli()
                online = 0
            elif re.search(r'(?m)^\s*0\s+Online\b', output):
                online += 1
                if online >= 2:
                    return
            else:
                online = 0
            time.sleep(5)
        raise AssertionError(self.node + ': forwarding plane did not stabilize')


def topology():
    return {'name': 'Disposable Junos ZIP test', 'nodes': [
        {'id': 'jz_sw', 'name': 'JZSW', 'type': 'switch', 'image': IMAGES[0], 'ethernet': 3, 'iol_id': 930},
        {'id': 'jz_r', 'name': 'JZR', 'type': 'router', 'image': IMAGES[1], 'ethernet': 3, 'iol_id': 931},
        {'id': 'jz_a', 'name': 'PC-A', 'type': 'pc', 'image': 'alpine:latest', 'iol_id': 932,
         'ipv4': '192.0.2.2/24', 'gateway': '192.0.2.1'},
        {'id': 'jz_b', 'name': 'PC-B', 'type': 'pc', 'image': 'alpine:latest', 'iol_id': 933,
         'ipv4': '198.51.100.2/24', 'gateway': '198.51.100.1'}], 'links': [
        {'id': 'access', 'a': {'node': 'jz_a', 'port': 'eth0'}, 'b': {'node': 'jz_sw', 'port': 'ge-0/0/0'}},
        {'id': 'transit', 'a': {'node': 'jz_sw', 'port': 'ge-0/0/1'}, 'b': {'node': 'jz_r', 'port': 'et-0/0/0'}},
        {'id': 'routed', 'a': {'node': 'jz_r', 'port': 'et-0/0/1'}, 'b': {'node': 'jz_b', 'port': 'eth0'}}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image-dir', type=Path, required=True)
    parser.add_argument('--work-parent', type=Path, required=True)
    args = parser.parse_args()
    if not Path('/dev/kvm').exists() or not Path('/dev/net/tun').exists():
        parser.error('Requires KVM and /dev/net/tun')
    images = [(args.image_dir / name).resolve(strict=True) for name in IMAGES]
    args.work_parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='junos-zip-native-', dir=args.work_parent)).resolve()
    os.chmod(root, 0o700)
    print('EVIDENCE', root, flush=True)
    before = {p.name: lab_backup.digest(p) for p in images}
    password = secrets.token_urlsafe(24)
    hashed = subprocess.run(['openssl', 'passwd', '-6', '-stdin'], input=password + '\n',
                            text=True, capture_output=True, check=True).stdout.strip()
    credential = root / 'test-password'
    credential.write_text(password)
    credential.chmod(0o600)
    lab = lab_server.Lab(root / 'data', args.image_dir, automation=True)
    stopped = threading.Event()
    monitor = threading.Thread(target=lab.monitor, args=(stopped,), daemon=True)
    monitor.start()
    report = {'images': before, 'phases': [], 'passed': False, 'forced_cleanup': False}
    terminals = {}

    def note(text):
        report['phases'].append(text)
        print(text, flush=True)
        (root / 'result.json').write_text(json.dumps(report, indent=2))

    def boot(saved=False):
        note('Booting ' + ('restored/saved' if saved else 'fresh') + ' disposable guests')
        lab.start_all()
        terminals.clear()
        for node in lab.topology['nodes']:
            terminals[node['id']] = Terminal(lab, node['id'], root, [password, hashed])
        for node in ('jz_r', 'jz_sw'):
            terminals[node].login(password if saved else None)
            terminals[node].ready()
            note(node + ': login/CLI and forwarding plane ready')

    def configure():
        for node, name in [('jz_r', 'JZR'), ('jz_sw', 'JZSW')]:
            terminal = terminals[node]
            terminal.command('configure exclusive', CONFIG)
            commands = ['set system host-name ' + name,
                        'set system root-authentication encrypted-password "' + hashed + '"']
            if node == 'jz_r':
                commands += ['set interfaces et-0/0/0 unit 0 family inet address 192.0.2.1/24',
                             'set interfaces et-0/0/1 unit 0 family inet address 198.51.100.1/24']
            else:
                commands += ['delete chassis auto-image-upgrade', 'set vlans ZIPTEST vlan-id 100']
                for port in ('ge-0/0/0', 'ge-0/0/1'):
                    commands += ['delete interfaces ' + port,
                                 'set interfaces ' + port + ' unit 0 family ethernet-switching interface-mode access',
                                 'set interfaces ' + port + ' unit 0 family ethernet-switching vlan members ZIPTEST']
            for command in commands:
                terminal.command(command, CONFIG)
            assert 'commit complete' in terminal.command('commit', CONFIG, 90)
            terminal.command('exit')
            note(node + ': committed baseline')

    def verify(baseline=None):
        configurations = {}
        for node in ('jz_r', 'jz_sw'):
            terminal = terminals[node]
            deadline = time.monotonic() + 180
            while True:
                output = terminal.command('show chassis fpc | no-more')
                if re.search(r'(?m)^\s*0\s+Online\b', output):
                    break
                assert time.monotonic() < deadline, node + ': forwarding plane did not become ready'
                time.sleep(3)
            output = terminal.command('show configuration interfaces | display set | no-more')
            configurations[node] = [line for line in output.splitlines() if line.startswith('set ')]
        if baseline is not None:
            assert configurations == baseline, 'Restored interface configuration differs'
        for node, target in [('jz_a', '198.51.100.2'), ('jz_b', '192.0.2.2')]:
            terminal = terminals[node]
            terminal.wait(PC)
            for attempt in range(3):
                output = terminal.command('ping -c 4 -W 2 ' + target, PC, 20)
                if '4 packets transmitted, 4 packets received, 0% packet loss' in output:
                    break
                assert attempt < 2, output
                time.sleep(3)
        note('Both FPCs online; saved interfaces match; bidirectional routed PC traffic has zero loss')
        return configurations

    def wait_stopped(timeout=210):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with lab.lock:
                assert not any(item['phase'] in ('timed_out', 'unconfirmed')
                               for item in lab.junos_shutdowns.values()), lab.errors
                if not lab.runtime:
                    assert not lab.errors, lab.errors
                    return
            time.sleep(1)
        raise AssertionError('Guest shutdown did not complete; acknowledgement is not completion')

    try:
        lab.save(topology())
        boot()
        configure()
        baseline = verify()
        for node in ('jz_sw', 'jz_r'):
            terminal = terminals[node]
            terminal.send('request system power-off')
            terminal.wait(r'\[yes,no\] \(no\)', 15)
            terminal.send('yes')
        wait_stopped()
        note('CLI power-off completed with clean process exit on both guests')
        export = lab_backup.create(lab)
        stream, _ = lab_backup.take(lab, export['url'].rsplit('/', 1)[1])
        archive = root / 'saved-lab.zip'
        with stream, archive.open('wb') as destination:
            shutil.copyfileobj(stream, destination)
        original = lab.snapshot()['topology']
        lab.reset_workspace({'expected_revision': lab.workspace_revision,
                             'expected_instructions_revision': lab.published_instructions()['revision']})
        assert not list((lab.directory / 'nodes').iterdir()), 'Reset left old disks behind'
        with archive.open('rb') as source:
            lab_backup.restore(lab, source, lab_server.run)
        assert lab.topology == original
        for node in lab.topology['nodes']:
            if vios.is_junos(node):
                disk = vios.disk_paths(node, lab.node_dir(node['id']))[1]
                lab_server.run('qemu-img', 'check', '-f', 'qcow2', str(disk))
        note('ZIP restored into cleared storage; topology identical; QCOW2 checks passed')
        boot(saved=True)
        verify(baseline)
        lab.request_stop()
        assert any(s['shutdown_pending'] for s in lab.snapshot()['status'].values())
        wait_stopped()
        note('Guarded Stop completed; both guests exited without a forced termination')
        boot(saved=True)
        verify(baseline)
        lab.request_stop()
        wait_stopped()
        assert {p.name: lab_backup.digest(p) for p in images} == before, 'Vendor base image changed'
        report['passed'] = True
        report['archive_bytes'] = archive.stat().st_size
        report['archive_sha256'] = lab_backup.digest(archive)
        note('PASS: real Junos ZIP restore, CLI and ACPI shutdown, subsequent boot/forwarding, unchanged base images')
    finally:
        if lab.runtime:
            try:
                lab.request_stop()
                wait_stopped()
            except Exception:
                report['forced_cleanup'] = True
                note('FAILED test: forcibly cleaning up only its disposable guests')
        stopped.set()
        monitor.join(timeout=5)
        lab.stop_all()
        lab_backup.expire(lab, all_files=True)
        lab.file_lock.close()
        (root / 'result.json').write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
