#!/usr/bin/env python3
"""Bridge QEMU Ethernet streams to the local IOL datagram fabric.

One process/socket owns a node's application ID. Each NIC has a private Unix
stream listener using QEMU's four-byte big-endian Ethernet length framing.
IOU's eight-byte header is documented in iou2net.pl; payloads are unchanged.
"""
import argparse
import errno
import json
from pathlib import Path
import selectors
import signal
import socket
import struct

MAX_FRAME = 65535
MAX_QUEUE = 1024 * 1024


class Bridge:
    def __init__(self, config):
        self.config = config
        self.selector = selectors.DefaultSelector()
        self.clients = {}
        self.paths = []
        self.stopping = False
        self.routes = {int(key): value for key, value in config["routes"].items()}
        self.iou = None

    def listen(self):
        base = Path(self.config["netio"])
        base.mkdir(mode=0o755, exist_ok=True)
        self.iou = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.iou.setblocking(False)
        self.selector.register(self.iou, selectors.EVENT_READ, ("iou", None))
        path = base / str(self.config["id"])
        self.iou.bind(str(path))  # Never unlink/adopt another process's socket.
        self.paths.append(path)
        for index in range(self.config["count"]):
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.selector.register(listener, selectors.EVENT_READ, ("listen", index))
            path = Path(self.config["sockets"]) / str(index)
            listener.bind(str(path))
            self.paths.append(path)
            listener.listen(1)
            listener.setblocking(False)
        ready = Path(self.config["sockets"]) / "ready"
        ready.touch()
        self.paths.append(ready)
        print("QEMU Ethernet bridge ready", flush=True)

    def disconnect(self, index):
        client = self.clients.pop(index)
        self.selector.unregister(client["socket"])
        client["socket"].close()

    def to_iou(self, index, frame):
        route = self.routes.get(index)
        if route is None:
            return
        local_port = (index // 4) | ((index % 4) << 4)
        header = struct.pack(">HHBBH", route["id"], self.config["id"], route["port"], local_port, 0x100)
        try:
            self.iou.sendto(header + frame, str(Path(self.config["netio"]) / str(route["id"])))
        except OSError as exc:
            # A stopped/restarting peer or a full datagram queue drops packets.
            if exc.errno not in (errno.ENOENT, errno.ECONNREFUSED, errno.EAGAIN, errno.ENOBUFS):
                raise

    def from_iou(self):
        data = self.iou.recv(MAX_FRAME + 8)
        if len(data) < 22:
            return
        dest, source, port, source_port, delimiter = struct.unpack(">HHBBH", data[:8])
        index = (port & 15) * 4 + (port >> 4)
        route, client = self.routes.get(index), self.clients.get(index)
        if (dest != self.config["id"] or delimiter != 0x100 or not route or not client
                or (source, source_port) != (route["id"], route["port"])):
            return
        framed = struct.pack(">I", len(data) - 8) + data[8:]
        if len(client["output"]) + len(framed) <= MAX_QUEUE:
            client["output"] += framed
            self.selector.modify(client["socket"], selectors.EVENT_READ | selectors.EVENT_WRITE, ("client", index))

    def service_client(self, index, mask):
        client = self.clients[index]
        sock = client["socket"]
        try:
            if mask & selectors.EVENT_READ:
                chunk = sock.recv(65536)
                if not chunk:
                    self.disconnect(index)
                    return
                client["input"] += chunk
                buf = client["input"]
                while len(buf) >= 4:
                    size = struct.unpack(">I", buf[:4])[0]
                    if not 14 <= size <= MAX_FRAME:
                        self.disconnect(index)
                        return
                    if len(buf) < size + 4:
                        break
                    self.to_iou(index, buf[4:size + 4])
                    del buf[:size + 4]
            if mask & selectors.EVENT_WRITE and client["output"]:
                sent = sock.send(client["output"])
                del client["output"][:sent]
                if not client["output"]:
                    self.selector.modify(sock, selectors.EVENT_READ, ("client", index))
        except BlockingIOError:
            pass
        except (ConnectionResetError, BrokenPipeError):
            self.disconnect(index)

    def serve(self):
        try:
            self.listen()
            while not self.stopping:
                for key, mask in self.selector.select(.5):
                    kind, index = key.data
                    if kind == "iou":
                        self.from_iou()
                    elif kind == "listen":
                        sock, _ = key.fileobj.accept()
                        if index in self.clients:
                            sock.close()
                            continue
                        sock.setblocking(False)
                        self.clients[index] = {"socket": sock, "input": bytearray(), "output": bytearray()}
                        self.selector.register(sock, selectors.EVENT_READ, ("client", index))
                    elif index in self.clients:
                        self.service_client(index, mask)
        finally:
            for key in list(self.selector.get_map().values()):
                key.fileobj.close()
            self.selector.close()
            for path in self.paths:
                path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    bridge = Bridge(json.loads(args.config.read_text()))
    def stop(signum, frame):
        bridge.stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    bridge.serve()


if __name__ == "__main__":
    main()
