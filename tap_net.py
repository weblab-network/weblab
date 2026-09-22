#!/usr/bin/env python3
"""Multiport TAP adapter for the existing local Ethernet fabric."""
import argparse
import errno
import fcntl
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import struct

from qemu_net import Bridge, MAX_FRAME


class TapBridge(Bridge):
    def listen(self):
        base = Path(self.config['netio'])
        base.mkdir(mode=0o755, exist_ok=True)
        self.iou = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.iou.setblocking(False)
        self.selector.register(self.iou, selectors.EVENT_READ, ('iou', None))
        path = base / str(self.config['id'])
        self.iou.bind(str(path))
        self.paths.append(path)
        for index, name in enumerate(self.config['taps']):
            tap = open('/dev/net/tun', 'r+b', buffering=0)
            try:
                fcntl.ioctl(tap, 0x400454ca, struct.pack('16sH', name.encode(), 0x1002))  # TUNSETIFF, TAP|NO_PI
                os.set_blocking(tap.fileno(), False)
                self.selector.register(tap, selectors.EVENT_READ, ('client', index))
                self.clients[index] = {'socket': tap}
            except BaseException:
                tap.close()
                raise
        print('FRR Ethernet bridge ready', flush=True)

    def from_iou(self):
        data = self.iou.recv(MAX_FRAME + 9)
        if not 22 <= len(data) <= MAX_FRAME + 8:
            return
        dest, source, port, source_port, delimiter = struct.unpack('>HHBBH', data[:8])
        index = (port & 15) * 4 + (port >> 4)
        route, client = self.routes.get(index), self.clients.get(index)
        if (dest != self.config['id'] or delimiter != 0x100 or not route or not client or
                (source, source_port) != (route['id'], route['port'])):
            return
        try:
            client['socket'].write(data[8:])
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EIO, errno.ENOBUFS):
                raise

    def service_client(self, index, mask):
        try:
            frame = self.clients[index]['socket'].read(MAX_FRAME + 1)
            if frame and 14 <= len(frame) <= MAX_FRAME:
                self.to_iou(index, frame)
        except BlockingIOError:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    bridge = TapBridge(json.loads(parser.parse_args().config.read_text()))
    def stop(signum, frame):
        bridge.stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    bridge.serve()


if __name__ == '__main__':
    main()
