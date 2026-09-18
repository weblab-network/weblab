"""Saved-config export uses disposable storage; no Cisco images or consoles needed."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import lab_server
import saved_config
import test_lab
import vios


def checksum(data):
    struct.pack_into('>H', data, 4, 0)
    total = sum(w[0] for w in struct.iter_unpack('>H', data[:len(data)//2]))
    while total >> 16:
        total = (total & 65535) + (total >> 16)
    struct.pack_into('>H', data, 4, total ^ 65535)
    return data


def nvram(text=b'hostname Saved\nend\n', compressed=False, size=65536):
    payload = text
    if compressed:
        # A tiny Unix-compress stream of literal codes, before the first width
        # change. Test data is ours; no device firmware/configuration is bundled.
        assert len(text) < 255
        bits = sum(char << (i * 9) for i, char in enumerate(text))
        payload = b'\x1f\x9d\x8c' + bits.to_bytes((len(text)*9+7)//8, 'little')
    data = bytearray(size)
    start = 0x10024
    struct.pack_into('>HHHHIII', data, 0, 0xABCD, 2 if compressed else 1, 0, 0x0F04,
                     start, start + len(payload), len(payload))
    if compressed:
        struct.pack_into('>III', data, 24, 1, 65536, len(text))
    data[36:36+len(payload)] = payload
    private = (36 + len(payload) + 3) & ~3
    secret = b'private data excluded'
    pstart = start - 36 + private + 16
    struct.pack_into('>HHIII', data, private, 0xFEDC, 1, pstart, pstart + len(secret), len(secret))
    data[private+16:private+16+len(secret)] = secret
    return bytes(checksum(data))


class DecoderTests(unittest.TestCase):
    def test_uncompressed_compressed_and_private_exclusion(self):
        for size in (65536, 524288):
            for compressed in (False, True):
                with self.subTest(size=size, compressed=compressed):
                    self.assertEqual(saved_config.decode_nvram(nvram(compressed=compressed, size=size)),
                                     'hostname Saved\nend\n')

    def test_binary_banner_body_is_preserved(self):
        text = b'hostname Saved\nbanner motd \x03\nKeep ^C and no logging console\n%SYS-5-NOTICE: example\n\x03\nend\n'
        result = saved_config.decode_nvram(nvram(text))
        self.assertIn('Keep ^C and no logging console\n%SYS-5-NOTICE: example', result)
        self.assertNotIn('\x03', result)
        self.assertIn('banner motd !\n', result)

    def test_invalid_checksum_format_bounds_and_private_header(self):
        original = nvram()
        variants = [original[:-1], b'\x00'*65536]
        broken = bytearray(original); broken[40] ^= 1; variants.append(broken)
        for offset, value in ((2, 3), (8, 1), (12, 0), (16, 65535), (56, 0)):
            broken = bytearray(original)
            struct.pack_into('>I' if offset >= 8 and offset < 20 else '>H', broken, offset, value)
            variants.append(checksum(broken))
        for variant in variants:
            with self.subTest(prefix=bytes(variant[:20]).hex()), self.assertRaises(ValueError):
                saved_config.decode_nvram(variant)

    def test_incomplete_controls_and_limits_fail(self):
        for text in (b'', b'hostname Incomplete\n', b'hostname Bad\x00\nend\n', b'!'+b'x'*16384+b'\nend\n'):
            with self.subTest(length=len(text)), self.assertRaises(ValueError):
                saved_config.decode_nvram(nvram(text))
        for length in (0, 16385, 2):
            bad = bytearray(nvram(compressed=True)); struct.pack_into('>I', bad, 32, length)
            with self.assertRaises(ValueError): saved_config.decode_nvram(checksum(bad))
        bad = bytearray(nvram(compressed=True)); bad[36] = 0
        with self.assertRaisesRegex(ValueError, 'compression format'):
            saved_config.decode_nvram(checksum(bad))
        with patch('saved_config.shutil.which', return_value=None), self.assertRaisesRegex(ValueError, 'Install gzip'):
            saved_config.decode_nvram(nvram(compressed=True))

    def test_external_reader_output_is_bounded(self):
        with self.assertRaisesRegex(ValueError, 'extraction limit'):
            saved_config.command_bytes([sys.executable, '-c', 'import os;os.write(1,b"x"*65536)'], limit=100)


class SavedExportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_lab.LabTests(); self.fixture.setUp()
        self.lab = self.fixture.lab
        for node in self.lab.topology['nodes']:
            path = self.lab.node_dir(node['id']) / f"nvram_{node['iol_id']:05d}"
            path.write_bytes(nvram(('hostname Saved'+node['id']+'\nend\n').encode()))

    def tearDown(self):
        self.fixture.tearDown()

    def test_http_export_preserves_topology_storage_and_does_not_open_consoles(self):
        self.lab.topology['nodes'][0]['startup_config'] = 'hostname OldSnippet\nend\n'
        before = copy.deepcopy(self.lab.topology)
        files = {p:p.read_bytes() for p in (self.lab.directory/'nodes').glob('*/*') if p.is_file()}
        with patch('console_capture.Console', side_effect=AssertionError('Console opened')):
            result = self.fixture.request('/api/export/saved-configs', 'POST', {})
        self.assertEqual(result['nodes'][0]['startup_config'], 'hostname Savedr1\nend\n')
        self.assertEqual(self.lab.topology, before)
        self.assertEqual(files, {p:p.read_bytes() for p in files})
        self.assertEqual(self.lab.validate(result)['nodes'][0]['startup_config'], result['nodes'][0]['startup_config'])

    def test_running_cisco_preflight_but_running_pc_is_allowed(self):
        self.lab.runtime['r2'] = {}
        with patch('saved_config.read_node', side_effect=AssertionError('Read too early')):
            with self.assertRaisesRegex(ValueError, 'Stop'):
                saved_config.export(self.lab, lab_server.run)
        self.lab.runtime.clear()
        self.lab.topology['nodes'].append({'id':'pc1','name':'PC','type':'pc','image':'alpine:latest','ipv4':'192.0.2.1/24','gateway':''})
        self.lab.runtime['pc1'] = {}
        try:
            result = saved_config.export(self.lab, lab_server.run)
            self.assertNotIn('startup_config', result['nodes'][-1])
            self.assertEqual(result['nodes'][-1]['ipv4'], '192.0.2.1/24')
        finally: self.lab.runtime.clear()

    def test_missing_or_corrupt_storage_never_substitutes_snippet_or_console(self):
        node = self.lab.topology['nodes'][1]
        node['startup_config'] = 'hostname Stale\nend\n'
        path = self.lab.node_dir(node['id']) / f"nvram_{node['iol_id']:05d}"
        for data in (None, b'bad'):
            path.unlink(missing_ok=True)
            if data is not None: path.write_bytes(data)
            with patch('console_capture.Console', side_effect=AssertionError('Console opened')):
                with self.assertRaises(HTTPError) as caught:
                    self.fixture.request('/api/export/saved-configs', 'POST', {})
                self.assertEqual(caught.exception.code, 400)
                error = json.load(caught.exception)['error']
                self.assertIn('No file exported', error)
                self.assertIn('live configs', error)
        path.unlink()
        path.symlink_to('/dev/zero')
        with self.assertRaisesRegex(ValueError, 'regular file'):
            saved_config.export(self.lab, lab_server.run)

    @unittest.skipUnless(all(shutil.which(c) for c in ('qemu-img','mformat','mcopy','mtype')), 'requires QEMU and mtools')
    def test_qcow2_fat_nvram_roundtrip_cleanup_and_wrong_backing(self):
        node = copy.deepcopy(self.lab.topology['nodes'][0]);node['image']='test_vios.qcow2'
        cwd = self.lab.node_dir(node['id'])
        node['startup_config'] = 'hostname SeedMustNotBeExported\nend\n'
        raw = vios.config_disk(node, cwd, lab_server.run, ValueError)
        nvfile = cwd/'test-nvram'
        nvfile.write_bytes(nvram(b'hostname DiskSaved\nend\n', compressed=True, size=524288))
        lab_server.run('mcopy','-i',str(raw)+'@@32256',str(nvfile),'::nvram')
        base = self.lab.image_dir/node['image']
        lab_server.run('qemu-img','convert','-f','raw','-O','qcow2',str(raw),str(base))
        disk = vios.disk_for(node, cwd, self.lab.image_dir, lab_server.run, ValueError)
        original = {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (disk,base)}
        self.assertEqual(saved_config.read_node(self.lab,node,lab_server.run),'hostname DiskSaved\nend\n')
        self.assertFalse(list(self.lab.directory.glob('.saved-config-*')))
        self.assertEqual(original, {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in original})
        with patch('saved_config.disk_nvram', side_effect=ValueError('No saved NVRAM')):
            with self.assertRaisesRegex(ValueError, 'No saved NVRAM'):
                saved_config.read_node(self.lab,node,lab_server.run)
        self.assertFalse(list(self.lab.directory.glob('.saved-config-*')))
        backing,_ = vios.disk_paths(node,cwd)
        backing.unlink();backing.symlink_to(raw)
        with self.assertRaisesRegex(ValueError, 'expected installed image'):
            saved_config.read_node(self.lab,node,lab_server.run)


if __name__ == '__main__': unittest.main()
