"""Local, runtime-only Ethernet link relay for live directional frame loss.

Each cable owns one synthetic IOL application ID, with ports 0/0 and 0/1.
Endpoints address that relay through NETMAP/the QEMU bridge. The relay rewrites
only the eight-byte transport envelope; Ethernet payloads are never changed.
No UDP listeners, guest commands, queues, or interface carrier changes are used.
"""
import errno
import json
from pathlib import Path
import selectors
import socket
import stat
import struct
import threading

HEADER = struct.Struct('>HHBBH')
MAX_FRAME = 65535


class FabricError(Exception):
    pass


def identity(path):
    try:
        info = path.lstat()
        if stat.S_ISSOCK(info.st_mode):
            return [info.st_dev, info.st_ino]
    except FileNotFoundError:
        pass
    return None


def recover(directory, netio):
    """Called only while holding the data-directory server lock."""
    journal = directory / 'link-sockets.json'
    if journal.exists():
        for app_id, expected in json.loads(journal.read_text()).items():
            if (not app_id.isdigit() or not 1 <= int(app_id) <= 1023 or
                    not isinstance(expected, list) or len(expected) != 2 or
                    any(type(value) is not int for value in expected)):
                raise FabricError('Invalid link relay recovery record')
            path = netio / app_id
            if identity(path) == expected:
                path.unlink(missing_ok=True)
        journal.unlink()


class LinkFabric:
    def __init__(self, directory, netio, links, occupied):
        self.directory, self.netio = Path(directory), Path(netio)
        self.links, self.peers, self.owned = {}, {}, {}
        self.lock = threading.Lock()
        self.selector = selectors.DefaultSelector()
        self.stopping = threading.Event()
        self.closed = False
        self.thread = None
        self.error = ''
        self.netio.mkdir(mode=0o755, exist_ok=True)
        candidates = iter(i for i in range(1023, 0, -1) if i not in occupied)
        try:
            for link in links:
                while True:
                    app_id = next(candidates, None)
                    if app_id is None:
                        raise FabricError('No free application IDs for link relays')
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                    sock.setblocking(False)
                    path = self.netio / str(app_id)
                    try:
                        sock.bind(str(path))
                        break
                    except OSError as exc:
                        sock.close()
                        if exc.errno != errno.EADDRINUSE:
                            raise
                self.owned[str(app_id)] = identity(path)
                self.selector.register(sock, selectors.EVENT_READ, link['id'])
                self.journal()
                self.links[link['id']] = {**link, 'id_number': app_id, 'socket': sock,
                                         'blocked_a_to_b': False, 'blocked_b_to_a': False,
                                         'carrier_a': 'up', 'carrier_b': 'up'}
                for side, port in (('a', 0), ('b', 16)):
                    endpoint = link[side]
                    self.peers[(endpoint['node'], endpoint['interface'])] = {'id': app_id, 'port': port}
            self.thread = threading.Thread(target=self.serve, name='lab-link-fabric', daemon=True)
            self.thread.start()
        except Exception:
            self.close()
            raise

    def journal(self):
        target = self.directory / 'link-sockets.json'
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.owned))
        temporary.replace(target)

    def states(self):
        with self.lock:
            return {key: {field: value[field] for field in ('blocked_a_to_b', 'blocked_b_to_a', 'carrier_a', 'carrier_b')}
                    for key, value in self.links.items()}

    def set_blocked(self, link_id, a_to_b, b_to_a):
        with self.lock:
            if self.error or not self.thread or not self.thread.is_alive():
                raise FabricError(self.error or 'Link relay is not running')
            link = self.links[link_id]
            link['blocked_a_to_b'], link['blocked_b_to_a'] = a_to_b, b_to_a

    def set_carrier(self, link_id, side, state):
        with self.lock:
            self.links[link_id]['carrier_' + side] = state

    def reset_node(self, node_id):
        with self.lock:
            for link in self.links.values():
                for side in ('a', 'b'):
                    if link[side]['node'] == node_id:
                        link['carrier_' + side] = 'up'

    def forward(self, link, packet):
        if not 14 + HEADER.size <= len(packet) <= MAX_FRAME + HEADER.size:
            return
        dest, source, port, source_port, delimiter = HEADER.unpack_from(packet)
        if dest != link['id_number'] or delimiter != 0x100 or port not in (0, 16):
            return
        side, other = ('a', 'b') if port == 0 else ('b', 'a')
        origin, peer = link[side], link[other]
        if (source, source_port) != (origin['id'], origin['port']):
            return
        with self.lock:
            if link[f'blocked_{side}_to_{other}'] or any(link['carrier_' + end] != 'up' for end in ('a', 'b')):
                return
            header = HEADER.pack(peer['id'], link['id_number'], peer['port'], 16 if other == 'b' else 0, 0x100)
            try:
                link['socket'].sendto(header + packet[HEADER.size:], str(self.netio / str(peer['id'])))
            except OSError as exc:
                # No replay when restoring: stopped peers and full queues lose frames.
                if exc.errno not in (errno.ENOENT, errno.ECONNREFUSED, errno.EAGAIN, errno.ENOBUFS):
                    raise

    def serve(self):
        try:
            while not self.stopping.is_set():
                for key, _ in self.selector.select(.05):
                    try:
                        packet = key.fileobj.recv(MAX_FRAME + HEADER.size + 1)
                    except BlockingIOError:
                        continue
                    self.forward(self.links[key.data], packet)
        except Exception as exc:
            self.error = f'Link relay failed: {exc}'

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stopping.set()
        if self.thread:
            self.thread.join()
        for key in list(self.selector.get_map().values()):
            key.fileobj.close()
        self.selector.close()
        for app_id, expected in self.owned.items():
            path = self.netio / app_id
            if identity(path) == expected:
                path.unlink(missing_ok=True)
        (self.directory / 'link-sockets.json').unlink(missing_ok=True)
