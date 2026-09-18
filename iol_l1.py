"""IOL L1 signaling for the image profiles verified by our native experiment.

Carrier values are requested states, not guest acknowledgments. Ethernet loss
is independent: periodic L1 messages continue until an endpoint is unplugged.
"""
import json
import math
from pathlib import Path
import socket
import struct
import threading
import time

from link_fabric import FabricError, identity

PROFILES = {'cisco_iol-17.18.02.bin', 'cisco_iol-l2-17.18.02.bin'}


def supported(node):
    return node['type'] in ('router', 'switch') and node['image'] in PROFILES


def recover(directory, path):
    journal = directory / 'l1-senders.json'
    if journal.exists():
        for number, expected in json.loads(journal.read_text()).items():
            if (not number.isdigit() or not 1 <= int(number) <= 1023 or
                    not isinstance(expected, list) or len(expected) != 2 or
                    any(type(v) is not int for v in expected)):
                raise FabricError('Invalid IOL L1 recovery record')
            target = path / ('L1' + number)
            if identity(target) == expected:
                target.unlink(missing_ok=True)
        journal.unlink()


class Controller:
    def __init__(self, directory, path, fabric):
        self.directory, self.path, self.fabric = Path(directory), Path(path), fabric
        self.senders, self.targets = {}, {}
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.serve, name='iol-l1', daemon=True)
        self.thread.start()

    def journal(self):
        target = self.directory / 'l1-senders.json'
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps({str(k): v[1] for k, v in self.senders.items()}))
        temporary.replace(target)

    def add_node(self, node_id, expected):
        with self.lock:
            self.path.mkdir(exist_ok=True)
            for key, link in self.fabric.links.items():
                for side, source_port in (('a', 0), ('b', 16)):
                    end = link[side]
                    if end['node'] != node_id:
                        continue
                    number = link['id_number']
                    if number not in self.senders:
                        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                        sock.setblocking(False)
                        try:
                            sock.bind(str(self.path / f'L1{number}'))
                        except Exception:
                            sock.close()
                            raise
                        self.senders[number] = (sock, identity(self.path / f'L1{number}'))
                        self.journal()
                    target = {'node': node_id, 'path': self.path / f"L1{end['id']}",
                              'identity': expected, 'sender': self.senders[number][0],
                              'message': struct.pack('>HHBBBB', end['id'], number, end['port'], source_port, 3, 0),
                              'up': True, 'error': '', 'until': time.monotonic() + 1}
                    self.targets[(key, side)] = target
                    self.send(target)

    @staticmethod
    def send(target):
        if identity(target['path']) != target['identity']:
            raise FabricError('IOL L1 socket disappeared or changed; stop and restart this node')
        target['sender'].sendto(target['message'], str(target['path']))

    def set_carrier(self, key, side, up):
        with self.lock:
            target = self.targets[(key, side)]
            target['up'] = False
            try:
                if up:
                    self.send(target)
            except (OSError, FabricError) as exc:
                target['error'] = str(exc)
                self.fabric.set_carrier(key, side, 'unknown')
                raise FabricError(f'IOL L1 signaling failed: {exc}. Use Reconnect or restart the node.') from exc
            target.update(up=up, error='', until=time.monotonic() + (1 if up else 10))
            self.fabric.set_carrier(key, side, 'up' if up else 'down')

    def states(self):
        with self.lock:
            return {key: {'error': t['error'], 'pending': max(0, math.ceil(t['until'] - time.monotonic()))}
                    for key, t in self.targets.items()}

    def remove_node(self, node_id):
        with self.lock:
            self.targets = {key: t for key, t in self.targets.items() if t['node'] != node_id}

    def serve(self):
        while not self.stopping.wait(.5):
            with self.lock:
                for (key, side), target in self.targets.items():
                    if not target['up']:
                        continue
                    try:
                        self.send(target)
                    except (OSError, FabricError) as exc:
                        target.update(up=False, error=f'IOL L1 signaling failed: {exc}', until=0)
                        self.fabric.set_carrier(key, side, 'unknown')

    def close(self):
        self.stopping.set()
        self.thread.join()
        for number, (sock, expected) in self.senders.items():
            sock.close()
            path = self.path / f'L1{number}'
            if identity(path) == expected:
                path.unlink(missing_ok=True)
        self.senders.clear()
        self.targets.clear()
        (self.directory / 'l1-senders.json').unlink(missing_ok=True)
