#!/usr/bin/env python3
"""Build and verify a saved EXOS demo ZIP using disposable KVM guests only.

Requires root, KVM, Docker with alpine:latest, and the usual native dependencies.
The output archive contains derived disks; never commit it to source control.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import console_capture
import lab_backup
import lab_server


class ReplayConsole(console_capture.Console):
    """Keep boot/login output received while acquiring the console input lock."""
    def __init__(self, port):
        self.captured = bytearray()
        super().__init__(port)

    def receive(self, deadline):
        data = super().receive(deadline)
        if self.captured is not None:
            self.captured.extend(data)
        return data


class EXOSConsole:
    def __init__(self, lab, node_id):
        self.console = ReplayConsole(lab.runtime[node_id]['port'])
        try:
            self.console.acquire()
        except BaseException:
            self.console.close()
            raise
        self.pending = bytes(self.console.captured)
        self.console.captured = None

    def close(self):
        self.console.close()

    def send(self, text):
        self.console.send((text + '\r').encode())

    def wait(self, pattern, timeout=180):
        deadline = time.monotonic() + timeout
        output = self.pending
        self.pending = b''
        while True:
            text = console_capture.clean_output(output)
            if re.search(pattern, text, re.I | re.M):
                return text
            try:
                output += self.console.receive(deadline)
            except Exception as error:
                raise RuntimeError(f'EXOS console: {error}\n{console_capture.clean_output(output)[-16000:]}') from error
            if not self.console.lock or not self.console.lock['mine']:
                raise RuntimeError('EXOS demo console input lock lost')

    def login(self, fresh):
        # Wait passively, including replay received during the lock handshake.
        # An unrecognized CPU model name sends EXOS into development-board
        # boot. Continuing its menu only reaches a shell, not a usable switch.
        ready = r'Authentication Service \(AAA\).*available|^(?:[\w-]+ )?login:\s*$'
        unsupported = r'Could not determine the CPU Family|===== developer menu ====='
        output = self.wait(f'{ready}|{unsupported}', timeout=300)
        if re.search(unsupported, output, re.I):
            raise RuntimeError('EXOS entered development-board boot; check the EXOS QEMU CPU model-name profile.\n'
                               + output[-2000:])
        # The readiness announcement can precede successful AAA requests briefly.
        for attempt in range(12):
            time.sleep(5)
            self.send('admin')
            self.wait(r'password:\s*$')
            self.send('')
            output = self.wait(r'\[y/N/q\]|\.\d+ #\s*$|Login incorrect')
            if 'Login incorrect' in output:
                continue
            if '[y/N/q]' in output:
                if not fresh:
                    raise RuntimeError('Restored EXOS unexpectedly lost its saved first-run state')
                self.send('q')
                self.wait(r'\.\d+ #\s*$')
            return
        raise RuntimeError('EXOS authentication did not become ready')

    def command(self, text):
        for attempt in range(30):
            self.send(text)
            output = self.wait(r'\.\d+ #\s*$', timeout=45)
            if 'cannot be executed during configuration load' not in output:
                break
            time.sleep(5)
        if re.search(r'\b(error|invalid|unrecognized|incomplete|ambiguous)\b', output, re.I):
            raise RuntimeError(f'EXOS command failed: {text}\n{output}')
        return output

    def save(self):
        self.send('save configuration')
        self.wait(r'\(y/N\)|\[y/N\]')
        self.send('y')
        output = self.wait(r'\.\d+ #\s*$', timeout=90)
        if 'saved' not in output.lower():
            raise RuntimeError('EXOS did not confirm saving: ' + output)


def verify(image, archive, directory):
    directory.mkdir()
    images = directory / 'images'
    images.mkdir()
    (images / image.name).symlink_to(image)
    print('Restoring the ZIP into a second lab and booting all five nodes', flush=True)
    restored = lab_server.Lab(directory / 'data', images)
    try:
        with archive.open('rb') as archive:
            lab_backup.restore(restored, archive, lab_server.run)
        restored.start_all()
        for node_id in ('exos-demo-r1', 'exos-demo-sw1', 'exos-demo-sw2'):
            console = EXOSConsole(restored, node_id)
            try:
                console.login(fresh=False)
                print(console.command('disable cli paging'), flush=True)
                print(console.command('show vlan'), flush=True)
                print(console.command('show lldp neighbors'), flush=True)
            finally:
                console.close()
        for node_id, destination in [('exos-demo-pc1', '10.10.20.10'), ('exos-demo-pc2', '10.10.10.10')]:
            for attempt in range(12):
                try:
                    report = lab_server.run('docker', 'exec', restored.runtime[node_id]['container'],
                                            'ping', '-c', '3', '-W', '2', destination)
                    if ', 0% packet loss' not in report:
                        raise RuntimeError(report)
                    print(report, flush=True)
                    break
                except (lab_server.LabError, RuntimeError):
                    if attempt == 11:
                        raise
                    time.sleep(5)
    finally:
        restored.stop_all()
        restored.file_lock.close()


def build(image, output, temporary_parent):
    metadata = json.loads((ROOT / 'packaging/exos/image.json').read_text())
    if image.name != metadata['filename'] or lab_backup.digest(image) != metadata['sha256']:
        raise ValueError('EXOS image does not match the pinned official release')
    if output.exists():
        raise ValueError('Choose an output ZIP that does not already exist')
    with tempfile.TemporaryDirectory(prefix='weblab-exos-demo-', dir=temporary_parent) as temporary:
        directory = Path(temporary)
        images = directory / 'images'
        images.mkdir()
        (images / image.name).symlink_to(image)
        lab = lab_server.Lab(directory / 'build', images)
        try:
            lab.save(json.loads((ROOT / 'examples/exos-demo.json').read_text()))
            for node_id, commands in json.loads((ROOT / 'packaging/exos/configs.json').read_text()).items():
                print(f'Booting and configuring {node_id}', flush=True)
                lab.start(node_id)
                console = EXOSConsole(lab, node_id)
                try:
                    console.login(fresh=True)
                    for command in commands:
                        console.command(command)
                    console.save()
                    print(f'Saved {node_id}', flush=True)
                finally:
                    console.close()
                lab.stop(node_id)
            print("Exporting saved EXOS disks", flush=True)
            result = lab_backup.create(lab)
            stream, _ = lab_backup.take(lab, result['url'].rsplit('/', 1)[1])
            with stream, (directory / 'demo.zip').open('wb') as target:
                shutil.copyfileobj(stream, target)
        finally:
            lab.stop_all()
            lab.file_lock.close()

        verify(image, directory / 'demo.zip', directory / 'verification')
        if lab_backup.digest(image) != metadata['sha256']:
            raise RuntimeError('Base image changed during test')
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(directory / 'demo.zip', output)
        print('PASS: saved ZIP restored, guests rebooted, VLANs and inter-VLAN ping verified', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--temporary-parent', type=Path)
    args = parser.parse_args()
    build(args.image.resolve(), args.output.resolve(), args.temporary_parent)
