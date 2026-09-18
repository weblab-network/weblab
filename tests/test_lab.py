"""Run with: python3 -m unittest discover -s tests -v

Uses a tiny PTY echo process in place of IOL. No Docker, TAP or Cisco image needed.
"""
import base64
import copy
import json
import io
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import select
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import lab_server


class WebSocket:
    def __init__(self, port, node_id, protocol=None, cursor=None):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=4)
        key = base64.b64encode(os.urandom(16)).decode()
        suffix = "?cursor=" + cursor if cursor else ""
        protocols = f"Sec-WebSocket-Protocol: {protocol}\r\n" if protocol else ""
        self.sock.sendall((f"GET /ws/{node_id}{suffix} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n{protocols}\r\n").encode())
        header = b""
        while not header.endswith(b"\r\n\r\n"):
            header += self.exact(1)
        self.headers = header
        if not header.startswith(b"HTTP/1.1 101"):
            raise AssertionError(header)

    def exact(self, size):
        data = b""
        while len(data) < size:
            piece = self.sock.recv(size - len(data))
            if not piece:
                raise EOFError("WebSocket closed")
            data += piece
        return data

    def receive(self):
        first, length = self.exact(2)
        if length == 126:
            length = struct.unpack("!H", self.exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self.exact(8))[0]
        return first & 15, self.exact(length)

    def send(self, data, opcode=1, fin=True):
        mask = os.urandom(4)
        length = len(data)
        size = bytes([128 | length]) if length < 126 else (bytes([254]) + struct.pack('!H', length) if length < 65536 else bytes([255]) + struct.pack('!Q', length))
        self.sock.sendall(bytes([(128 if fin else 0) | opcode]) + size + mask +
                          bytes(value ^ mask[i % 4] for i, value in enumerate(data)))

    def until(self, marker):
        output = b""
        while marker not in output:
            _, data = self.receive()
            output += data
        return output

    def close(self):
        self.sock.close()


class LabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="iol-unit-")
        self.root = Path(self.tmp.name)
        shutil.copy(lab_server.ROOT / "wrapper-ws.pl", self.root)
        image = self.root / "fake.bin"
        image.write_text("""#!/usr/bin/env python3
import os, socket, sys, tty
from pathlib import Path
tty.setraw(0)
assert Path('NETMAP').is_file(), 'Missing node NETMAP'
if os.environ.get('IOURC'):
    assert Path(os.environ['IOURC']).is_file(), 'Missing configured IOURC'
base = Path('/tmp/netio' + str(os.getuid()))
base.mkdir(exist_ok=True)
s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
s.bind(str(base / sys.argv[-1]))
os.write(1, b'BOOT READY\\r\\n')
while True:
    data = os.read(0, 65536)
    if not data: break
    os.write(1, b'RX:' + data)
""")
        image.chmod(0o755)
        self.root_patch = patch.object(lab_server, "ROOT", self.root)
        self.root_patch.start()
        self.lab = lab_server.Lab(self.root / "data")
        self.server = lab_server.ThreadingHTTPServer(("127.0.0.1", 0), lab_server.Handler)
        self.server.daemon_threads = True
        self.server.lab = self.lab
        self.server.stopping = threading.Event()
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.topology = {"name": "Test lab", "nodes": [self.node("r1"), self.node("r2")],
                         "links": [{"id": "cable", "a": {"node": "r1", "port": "0/0"}, "b": {"node": "r2", "port": "0/0"}}]}
        self.lab.save(self.topology)

    def tearDown(self):
        self.server.stopping.set()
        self.lab.stop_all()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.lab.file_lock.close()
        self.root_patch.stop()
        self.tmp.cleanup()

    @staticmethod
    def node(node_id):
        return {"id": node_id, "name": node_id, "type": "router", "image": "fake.bin", "x": 100, "y": 100}

    def request(self, path, method="GET", data=None, origin=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        request = Request(f"http://127.0.0.1:{self.port}{path}", method=method, headers=headers,
                          data=json.dumps(data).encode() if data is not None else None)
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    def test_console_launch_proxy_fragmentation_reconnect_and_cleanup(self):
        self.request("/api/nodes/r1/start", "POST", {})
        runtime = copy.deepcopy(self.lab.runtime["r1"])
        with self.assertRaises(lab_server.LabError):
            changed = copy.deepcopy(self.topology)
            changed["links"] = []
            self.lab.save(changed)
        ws = WebSocket(self.port, "r1")
        self.assertIn(b"BOOT READY", ws.until(b"BOOT READY"))
        ws.send(b"hello ", fin=False)
        ws.send(b"world", opcode=0)
        self.assertIn(b"hello world", ws.until(b"hello world"))
        ws.send(b"ping", opcode=9)
        self.assertEqual(ws.receive(), (10, b"ping"))
        ws.close()
        time.sleep(.2)
        ws = WebSocket(self.port, "r1")
        ws.send(b"reconnected")
        self.assertIn(b"reconnected", ws.until(b"reconnected"))
        ws.close()
        self.request("/api/nodes/r1/stop", "POST", {})
        self.assertFalse(self.lab.runtime)
        self.assertFalse((self.lab.netio / str(runtime["iol_id"])).exists())
        self.assertFalse(self.lab.alive(runtime["console"]))

    def test_reject_duplicate_interface(self):
        self.topology["links"].append({"id": "another", "a": {"node": "r1", "port": "0/0"}, "b": {"node": "r2", "port": "0/1"}})
        with self.assertRaisesRegex(lab_server.LabError, "only one cable"):
            self.lab.save(self.topology)

    def test_separate_image_directory_launch_and_license(self):
        images = self.root / "images"
        images.mkdir()
        (self.root / "fake.bin").rename(images / "fake.bin")
        (images / "iourc").write_text("test placeholder, not a license")
        self.lab.file_lock.close()
        self.lab = lab_server.Lab(self.root / "data", images)
        self.server.lab = self.lab
        self.assertEqual(self.lab.catalog(), [{"name": "fake.bin", "type": "router"}])
        self.lab.start("r1")
        self.assertEqual((self.lab.node_dir("r1") / "iourc").resolve(), images / "iourc")
        ws = WebSocket(self.port, "r1")
        try:
            self.assertIn(b"BOOT READY", ws.until(b"BOOT READY"))
        finally:
            ws.close()

    def test_server_starts_without_generating_license(self):
        data = self.root / 'fresh-server'
        proc = subprocess.Popen([sys.executable, str(Path(lab_server.__file__).resolve()),
                                 '--port', '0', '--data-dir', str(data), '--image-dir', str(self.root)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env={**os.environ, 'IOL_AUTO_LICENSE': '1'})
        try:
            self.assertTrue(select.select([proc.stdout], [], [], 10)[0], 'Server did not announce startup')
            self.assertIn(b'Weblab:', proc.stdout.readline())
            self.assertIsNone(proc.poll())
            self.assertFalse((self.root / 'iourc').exists())
        finally:
            proc.terminate()
            proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0)

    def test_fast_exit_includes_console_diagnostic(self):
        image = self.root / "fake.bin"
        image.write_text("#!/bin/sh\nprintf 'IOURC: test diagnostic before clean exit\\n'\nexit 0\n")
        with self.assertRaisesRegex(lab_server.LabError, "IOURC: test diagnostic"):
            self.lab.start("r1")
        self.assertFalse(self.lab.runtime)
        self.assertIn("child exited with status 0", self.lab.logs("r1"))

    def test_unresolvable_hostname_is_reported_before_launch(self):
        with patch.object(socket, "gethostbyname", side_effect=socket.gaierror("not found")):
            with self.assertRaisesRegex(lab_server.LabError, "does not resolve to IPv4"):
                self.lab.start("r1")
        self.assertFalse(self.lab.runtime)

    def upload(self, name, payload, origin=None):
        headers = {"Content-Type": "application/octet-stream"}
        if origin:
            headers["Origin"] = origin
        request = Request(f"http://127.0.0.1:{self.port}/api/images/{name}",
                          method="POST", headers=headers, data=payload)
        with urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 201)
            return json.load(response)

    def test_upload_catalog_permissions_and_no_overwrite(self):
        payload = b"\x7fELF" + bytes(1024)
        result = self.upload("test-l2.bin", payload)
        target = self.lab.image_dir / "test-l2.bin"
        self.assertEqual(target.read_bytes(), payload)
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)
        self.assertIn({"name": "test-l2.bin", "type": "switch"}, result["images"])
        with self.assertRaises(HTTPError):
            self.upload("test-l2.bin", b"\x7fELF" + bytes(2000))
        self.assertEqual(target.read_bytes(), payload)
        self.assertFalse(list(self.lab.image_dir.glob(".upload-*")))

    def test_upload_rejects_paths_non_elf_cross_origin_and_symlinks(self):
        for name in ["..%2Fescape.bin", "%2Ftmp%2Fescape.bin", ".hidden.bin", "a%00.bin", "image.txt"]:
            with self.subTest(name=name), self.assertRaises(HTTPError):
                self.upload(name, b"\x7fELF" + bytes(64))
        with self.assertRaises(HTTPError):
            self.upload("text.bin", b"#!/bin/sh\n" + bytes(64))
        with self.assertRaises(HTTPError):
            self.upload("foreign.bin", b"\x7fELF" + bytes(64), origin="http://foreign.invalid")
        (self.root / "symlink.bin").symlink_to(self.root / "absent")
        with self.assertRaises(HTTPError):
            self.upload("symlink.bin", b"\x7fELF" + bytes(64))
        self.assertFalse((self.root / "absent").exists())
        self.assertFalse((self.root / "text.bin").exists())
        self.assertFalse((self.root / "foreign.bin").exists())
        self.assertFalse(list(self.root.glob(".upload-*")))

    def test_upload_incomplete_size_limits_and_lock(self):
        with self.assertRaisesRegex(lab_server.LabError, "Incomplete"):
            self.lab.upload_image("partial.bin", io.BytesIO(b"\x7fELF"), 100)
        for length in [0, 63, lab_server.MAX_IMAGE_BYTES + 1]:
            with self.subTest(length=length), self.assertRaisesRegex(lab_server.LabError, "size"):
                self.lab.upload_image("size.bin", io.BytesIO(), length)
        with self.lab.upload_lock, self.assertRaisesRegex(lab_server.LabError, "in progress"):
            self.lab.upload_image("busy.bin", io.BytesIO(), 100)
        self.assertFalse(list(self.root.glob(".upload-*")))
        self.assertFalse((self.root / "partial.bin").exists())

    def test_split_upload_is_hidden_until_complete(self):
        payload = b"\x7fELF" + bytes(1024)
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as connection:
            connection.sendall((f"POST /api/images/split.bin HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nContent-Type: application/octet-stream\r\nContent-Length: {len(payload)}\r\n\r\n").encode() + payload[:1])
            time.sleep(.1)
            self.assertNotIn("split.bin", [image["name"] for image in self.lab.catalog()])
            connection.sendall(payload[1:])
            self.assertTrue(connection.recv(4096).startswith(b"HTTP/1.1 201"))
        self.assertEqual((self.root / "split.bin").read_bytes(), payload)

    def test_reject_invalid_image_node_path_and_interfaces(self):
        for key, value in [("image", "/bin/sh"), ("id", "../../oops"), ("ethernet", 0), ("memory", "1024")]:
            with self.subTest(key=key):
                topology = copy.deepcopy(self.topology)
                topology["nodes"][0][key] = value
                with self.assertRaises(lab_server.LabError):
                    self.lab.save(topology)
        self.topology["links"][0]["a"]["port"] = "7/9"
        with self.assertRaises(lab_server.LabError):
            self.lab.save(self.topology)

    def test_pc_validation_and_missing_connection(self):
        self.topology["nodes"] = [{"id": "pc", "name": "PC", "type": "pc", "image": "alpine:latest",
                                   "ipv4": "10.0.10.10/24", "gateway": "10.9.9.1"}]
        self.topology["links"] = []
        with self.assertRaisesRegex(lab_server.LabError, "Gateway"):
            self.lab.save(self.topology)
        self.topology["nodes"][0]["gateway"] = "10.0.10.1"
        self.lab.save(self.topology)
        with self.assertRaisesRegex(lab_server.LabError, "Connect the PC"):
            self.lab.start_all()
        self.assertFalse(self.lab.runtime)

    def test_running_nodes_can_move_without_changing_netmap(self):
        self.lab.start("r1")
        original = (self.lab.directory / "NETMAP").read_text()
        self.topology["nodes"][0].update(x=300, name="Renamed")
        self.lab.save(self.topology)
        self.assertEqual((self.lab.directory / "NETMAP").read_text(), original)

    def test_partial_start_failure_rolls_back_only_new_nodes(self):
        bad = self.root / "broken.bin"
        bad.write_text("#!/bin/sh\nexit 1\n")
        bad.chmod(0o755)
        self.topology["nodes"][1]["image"] = bad.name
        self.lab.save(self.topology)
        with self.assertRaises(lab_server.LabError):
            self.lab.start_all()
        self.assertFalse(self.lab.runtime)
        self.assertIn("r2", self.lab.errors)
        self.lab.start("r1")
        with self.assertRaises(lab_server.LabError):
            self.lab.start_all()
        self.assertIn("r1", self.lab.runtime)

    def test_cross_origin_and_static_path_rejected(self):
        with self.assertRaises(HTTPError) as error:
            self.request("/api/nodes/r1/start", "POST", {}, origin="http://foreign.invalid")
        self.assertEqual(error.exception.code, 400)
        self.assertFalse(self.lab.runtime)
        with self.assertRaises(HTTPError):
            self.request("/../lab_server.py")

    def test_split_http_body_is_read_completely(self):
        payload = json.dumps(self.topology).encode()
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as connection:
            connection.sendall((f"PUT /api/topology HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n").encode() + payload[:10])
            time.sleep(.1)
            connection.sendall(payload[10:])
            self.assertTrue(connection.recv(4096).startswith(b"HTTP/1.1 200"))

    def test_persistence_and_exclusive_owner(self):
        saved = json.loads(self.lab.topology_path.read_text())
        self.assertEqual(saved["name"], "Test lab")
        ids = [node["iol_id"] for node in saved["nodes"]]
        self.lab.save(self.topology)
        self.assertEqual(ids, [node["iol_id"] for node in self.lab.topology["nodes"]])
        with self.assertRaisesRegex(lab_server.LabError, "already manages"):
            lab_server.Lab(self.lab.directory)

    def test_exported_ids_survive_import_when_available(self):
        exported = copy.deepcopy(self.lab.topology)
        self.lab.save({"name": "Empty", "nodes": [], "links": []})
        self.lab.save(exported)
        self.assertEqual([n["iol_id"] for n in exported["nodes"]], [n["iol_id"] for n in self.lab.topology["nodes"]])
        # Never take an external process's socket, even when an import requests its ID.
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as occupied:
            foreign = self.lab.netio / str(exported["nodes"][0]["iol_id"])
            occupied.bind(str(foreign))
            try:
                self.lab.save({"name": "Empty", "nodes": [], "links": []})
                self.lab.save(exported)
                self.assertNotEqual(self.lab.topology["nodes"][0]["iol_id"], exported["nodes"][0]["iol_id"])
            finally:
                foreign.unlink()


if __name__ == "__main__":
    unittest.main()
