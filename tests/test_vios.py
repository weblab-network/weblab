"""IOSv validation/lifecycle and real socket forwarding, without Cisco images/KVM."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import lab_server
import vios


class ViosTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vios-unit-")
        self.root = Path(self.tmp.name)
        self.lab = lab_server.Lab(self.root / "data", self.root / "images")
        self.image = self.lab.image_dir / "vios.qcow2"
        self.image.write_bytes(b"QFI\xfb" + bytes(100))
        self.topology = {"name": "IOSv test", "nodes": [
            {"id": "v", "name": "V", "type": "router", "image": self.image.name, "ethernet": 16},
            {"id": "s", "name": "S", "type": "switch", "image": self.image.name, "ethernet": 16}],
            "links": [{"id": "l", "a": {"node": "v", "port": "Gi0/15"},
                       "b": {"node": "s", "port": "Gi3/3"}}]}
        self.lab.save(self.topology)

    def tearDown(self):
        self.lab.stop_all()
        self.lab.file_lock.close()
        self.tmp.cleanup()

    def test_catalog_interfaces_netmap_and_roundtrip(self):
        self.assertIn({"name": "vios.qcow2", "type": "router"}, self.lab.catalog())
        (self.lab.image_dir / "vios_l2.qcow2").write_bytes(self.image.read_bytes())
        self.assertIn({"name": "vios_l2.qcow2", "type": "switch"}, self.lab.catalog())
        self.lab.write_netmap()
        self.assertIn(":3/3@", (self.lab.directory / "NETMAP").read_text())
        saved = copy.deepcopy(self.lab.topology)
        self.lab.save(saved)
        self.assertEqual(saved, self.lab.topology)
        self.topology["nodes"][0]["ethernet"] = 17
        with self.assertRaises(lab_server.LabError):
            self.lab.save(self.topology)
        self.topology["nodes"][0]["ethernet"] = 15
        with self.assertRaisesRegex(lab_server.LabError, "interface"):
            self.lab.save(self.topology)

    def test_external_dependencies_rejected_before_qemu(self):
        for field, value in [(8, struct.pack(">Q", 100)), (16, struct.pack(">I", 8)),
                             (32, struct.pack(">I", 1)), (72, struct.pack(">Q", 4))]:
            header = bytearray(b"QFI\xfb" + struct.pack(">I", 3) + bytes(96))
            header[field:field+len(value)] = value
            self.image.write_bytes(header)
            with patch.object(lab_server, "run", side_effect=AssertionError("must not parse dependencies")):
                with self.assertRaises(lab_server.LabError):
                    vios.validate_image(self.image, lab_server.run, lab_server.LabError)

    def test_kvm_error_is_actionable(self):
        with patch("vios.open", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(lab_server.LabError, "compose.kvm.yaml"):
                vios.require_kvm(lab_server.LabError)

    @unittest.skipUnless(shutil.which("qemu-img"), "qemu-utils is optional for tests")
    def test_real_upload_and_persistent_independent_disks(self):
        source = self.root / "source.qcow2"
        lab_server.run("qemu-img", "create", "-f", "qcow2", str(source), "4M")
        payload = source.read_bytes()
        self.lab.upload_image("uploaded.qcow2", io.BytesIO(payload), len(payload))
        target = self.lab.image_dir / "uploaded.qcow2"
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        with self.assertRaisesRegex(lab_server.LabError, "already exists"):
            self.lab.upload_image("uploaded.qcow2", io.BytesIO(payload), len(payload))
        with self.assertRaises(lab_server.LabError):
            self.lab.upload_image("broken.qcow2", io.BytesIO(b"QFI\xfb" + bytes(100)), 104)
        self.assertFalse((self.lab.image_dir / "broken.qcow2").exists())
        self.assertFalse(list(self.lab.image_dir.glob(".upload-*")))
        node = dict(self.lab.node("v"), image="uploaded.qcow2")
        cwd = self.lab.node_dir("v")
        disk = vios.disk_for(node, cwd, self.lab.image_dir, lab_server.run, lab_server.LabError)
        # Write to the overlay and verify the base is unchanged and data survives reopen.
        lab_server.run("qemu-io", "-f", "qcow2", "-c", "write -P 0x5a 0 512", str(disk))
        self.assertEqual(vios.disk_for(node, cwd, self.lab.image_dir, lab_server.run, lab_server.LabError), disk)
        lab_server.run("qemu-io", "-f", "qcow2", "-c", "read -P 0x5a 0 512", str(disk))
        self.assertEqual(target.read_bytes(), payload)
        other = vios.disk_for(node, self.lab.node_dir("s"), self.lab.image_dir, lab_server.run, lab_server.LabError)
        lab_server.run("qemu-io", "-f", "qcow2", "-c", "read -P 0 0 512", str(other))
        # Moving the image directory updates the symlink without losing overlay data.
        moved = self.root / "moved-images"
        self.lab.image_dir.rename(moved)
        self.lab.image_dir = moved
        vios.disk_for(node, cwd, moved, lab_server.run, lab_server.LabError)
        lab_server.run("qemu-io", "-f", "qcow2", "-c", "read -P 0x5a 0 512", str(disk))

    def test_failed_qemu_launch_cleans_bridge_and_sockets(self):
        fake = self.root / "qemu-system-x86_64"
        fake.write_text("#!/bin/sh\necho 'test QEMU boot failure'\nexit 1\n")
        fake.chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(self.root) + ":" + os.environ["PATH"]}), \
                patch.object(vios, "require_kvm"), patch.object(vios, "disk_for", return_value=self.image):
            with self.assertRaisesRegex(lab_server.LabError, "test QEMU boot failure"):
                self.lab.start("v")
        self.assertFalse(self.lab.runtime)
        self.assertFalse((self.lab.netio / str(self.lab.node("v")["iol_id"])).exists())
        self.assertFalse(list(Path(tempfile.gettempdir()).glob(f"iol-{self.lab.owner}-*")))

    def test_qemu_restart_and_journal_recovery(self):
        fake = self.root / "qemu-system-x86_64"
        fake.write_text("#!/usr/bin/env python3\nimport time\nprint('FAKE VM READY', flush=True)\ntime.sleep(60)\n")
        fake.chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(self.root) + ":" + os.environ["PATH"]}), \
                patch.object(vios, "require_kvm"), patch.object(vios, "disk_for", return_value=self.image):
            self.lab.start("v")
            first = copy.deepcopy(self.lab.runtime["v"])
            self.lab.stop("v")
            self.assertFalse(Path(first["qemu_sockets"]).exists())
            self.lab.start("v")
            second = copy.deepcopy(self.lab.runtime["v"])
            self.assertNotEqual(first["console"]["pid"], second["console"]["pid"])
            children = list(self.lab.children.values())
            # Simulate loss of the in-process relay without removing its journal.
            old_fabric = self.lab.fabric
            self.lab.l1.close()
            old_fabric.stopping.set()
            old_fabric.thread.join()
            self.lab.file_lock.close()
            self.lab = lab_server.Lab(self.lab.directory, self.lab.image_dir)
            old_fabric.close()
            for proc in children:
                proc.wait(timeout=5)
            self.assertFalse(self.lab.runtime)
            self.assertFalse(Path(second["qemu_sockets"]).exists())


class BridgeTests(unittest.TestCase):
    def test_multiple_interfaces_fragmentation_peer_restart_and_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="qemu-net-unit-") as tmp:
            root = Path(tmp)
            sockets = root / "sockets"
            sockets.mkdir()
            netio = root / "netio"
            netio.mkdir()
            peer = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            peer.bind(str(netio / "201"))
            peer.settimeout(3)
            config = root / "config.json"
            config.write_text(json.dumps({"id": 200, "netio": str(netio), "count": 6, "sockets": str(sockets),
                "routes": {"0": {"id": 201, "port": 0x31}, "5": {"id": 201, "port": 0x12}}}))
            proc = subprocess.Popen(["python3", str(lab_server.ROOT / "qemu_net.py"), str(config)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            clients = []
            try:
                deadline = time.monotonic() + 5
                while not (sockets / "ready").exists():
                    self.assertIsNone(proc.poll())
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.02)
                for index in (0, 5):
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    client.settimeout(3)
                    client.connect(str(sockets / str(index)))
                    clients.append(client)
                # Include an 802.1Q tag and >1500-byte payload; preserve every byte.
                frame = b"\xff" * 6 + b"\x02\x00\x00\x00\x00\x01\x81\x00\x00\x64\x08\x00" + bytes(1800)
                for client, local, remote in [(clients[0], 0, 0x31), (clients[1], 0x11, 0x12)]:
                    framed = struct.pack(">I", len(frame)) + frame
                    client.sendall(framed[:2])
                    client.sendall(framed[2:20])
                    client.sendall(framed[20:] + framed)
                    for _ in range(2):
                        data = peer.recv(65535)
                        self.assertEqual(data, struct.pack(">HHBBH", 201, 200, remote, local, 0x100) + frame)
                    peer.sendto(struct.pack(">HHBBH", 200, 201, local, remote, 0x100) + frame, str(netio / "200"))
                    output = b""
                    while len(output) < len(framed):
                        output += client.recv(len(framed) - len(output))
                    self.assertEqual(output, framed)
                peer.close()
                (netio / "201").unlink()
                clients[0].sendall(framed)
                time.sleep(.1)
                self.assertIsNone(proc.poll(), "A stopped peer must not kill the bridge")
                peer = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                peer.bind(str(netio / "201")); peer.settimeout(3)
                clients[0].sendall(framed)
                self.assertEqual(peer.recv(65535)[8:], frame)
                # Invalid lengths close only the affected NIC connection.
                clients[0].sendall(struct.pack(">I", 0xffffffff))
                self.assertEqual(clients[0].recv(1), b"")
                self.assertIsNone(proc.poll())
            finally:
                for client in clients:
                    client.close()
                peer.close()
                proc.terminate()
                _, error = proc.communicate(timeout=5)
            self.assertEqual(proc.returncode, 0, error)
            self.assertFalse((netio / "200").exists())
            self.assertEqual(list(sockets.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
