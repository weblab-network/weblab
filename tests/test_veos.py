"""Arista hardware, boot media, and portable storage contracts."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import console_capture
import lab_backup
import lab_server
import saved_config
import vios


def iso_bytes():
    image = bytearray(40 * 2048)
    image[16*2048:16*2048+7] = b'\x01CD001\x01'
    image[17*2048:17*2048+7] = b'\x00CD001\x01'
    image[17*2048+7:17*2048+30] = b'EL TORITO SPECIFICATION'
    return bytes(image)


class VeosTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='veos-test-')
        self.root = Path(self.tmp.name)
        self.lab = lab_server.Lab(self.root/'data', self.root/'images')
        self.name = 'vEOS64-lab-4.36.1F.qcow2'
        (self.lab.image_dir/self.name).write_bytes(b'QFI\xfb'+bytes(100))
        self.lab.save({'name':'Arista', 'nodes':[{'id':'a', 'name':'A', 'type':'switch', 'image':self.name}], 'links':[]})

    def tearDown(self):
        lab_backup.expire(self.lab, all_files=True)
        self.lab.file_lock.close()
        self.tmp.cleanup()

    def test_profile_and_ports(self):
        node = self.lab.node('a')
        self.assertEqual(node['memory'], 6144)
        self.assertEqual(lab_server.ports(node), ['Management1','Ethernet1','Ethernet2','Ethernet3','Ethernet4'])
        self.assertEqual(lab_server.netmap_port(node,'Ethernet4'), '1/0')
        self.assertIn({'name':self.name,'type':'switch'}, self.lab.catalog())
        self.assertTrue(vios.is_veos(node))
        self.assertFalse(vios.is_vios(node))
        self.assertTrue(vios.is_veos({'image':'vEOS-lab-4.30.qcow2'}))
        self.assertFalse(vios.is_veos({'type':'pc','image':self.name}))
        disk = vios.disk_paths(node,self.root)[1]
        self.assertTrue(disk.name.startswith('veos-'))
        self.assertTrue(lab_backup.saved_path(node,disk.name))
        with self.assertRaisesRegex(ValueError,'Aboot'): vios.command(node,disk,self.root)
        args = vios.command(node,disk,self.root,boot=self.root/vios.ABOOT_IMAGE)
        self.assertEqual(args[args.index('-smp')+1], '2')
        self.assertEqual(sum('virtio-net-pci,netdev=' in a for a in args), 5)
        self.assertTrue(any('media=cdrom,if=ide,index=2,readonly=on' in a for a in args))
        self.assertIn('order=dc',args)
        self.assertTrue(any('format=qcow2,if=ide' in a for a in args))
        with self.assertRaisesRegex(ValueError,'only supported for IOSv'):
            vios.command(node,disk,self.root,config=self.root/'seed',boot=self.root/vios.ABOOT_IMAGE)

    def test_validation_and_cisco_guards(self):
        for change in ({'type':'router'}, {'ethernet':1}, {'ethernet':17}, {'startup_config':'hostname Wrong\n'}):
            data=copy.deepcopy(self.lab.topology); data['nodes'][0].update(change)
            with self.subTest(change=change),self.assertRaises(lab_server.LabError): self.lab.save(data)
        with patch('console_capture.Console',side_effect=AssertionError('console touched')):
            with self.assertRaisesRegex(ValueError,'Arista'): console_capture.export(self.lab)
        with patch('saved_config.read_node',side_effect=AssertionError('disk touched')):
            with self.assertRaisesRegex(ValueError,'Arista'): saved_config.export(self.lab,lab_server.run)
        data=copy.deepcopy(self.lab.topology)
        data['nodes'][0]['ethernet']=16
        data['nodes'].append({'id':'b','name':'B','type':'switch','image':self.name})
        data['links']=[{'id':'l','a':{'node':'a','port':'Ethernet15'},'b':{'node':'b','port':'Ethernet1'}}]
        self.lab.save(data)
        self.assertEqual(lab_server.netmap_port(self.lab.node('a'),'Ethernet15'),'3/3')
        data['links'][0]['a']['port']='Gi3/3'
        with self.assertRaises(lab_server.LabError): self.lab.save(data)

    def test_boot_media_upload_validation_and_missing_preflight(self):
        with patch('vios.require_kvm'), patch('vios.disk_for',side_effect=AssertionError('disk created')):
            with self.assertRaisesRegex(lab_server.LabError,'Aboot'): self.lab.start_vios(self.lab.node('a'))
        for name, content in [(vios.ABOOT_IMAGE,b'x'*40000),('anything.iso',iso_bytes())]:
            with self.assertRaises(lab_server.LabError): self.lab.upload_image(name,io.BytesIO(content),len(content))
        self.assertFalse(list(self.lab.image_dir.glob('.upload-*')))
        content=iso_bytes()
        self.lab.upload_image(vios.ABOOT_IMAGE,io.BytesIO(content),len(content))
        self.assertEqual(vios.boot_image(self.lab.node('a'),self.lab.image_dir,ValueError).name,vios.ABOOT_IMAGE)
        self.assertNotIn(vios.ABOOT_IMAGE,[i['name'] for i in self.lab.catalog()])
        with self.assertRaisesRegex(lab_server.LabError,'already exists'):
            self.lab.upload_image(vios.ABOOT_IMAGE,io.BytesIO(content),len(content))
        self.assertEqual((self.lab.image_dir/vios.ABOOT_IMAGE).read_bytes(),content)

    @unittest.skipUnless(shutil.which('qemu-img') and shutil.which('qemu-io'),'Requires qemu-utils')
    def test_archive_restores_overlay_and_requires_matching_aboot(self):
        image=self.lab.image_dir/self.name
        image.unlink();lab_server.run('qemu-img','create','-f','qcow2',str(image),'16M')
        boot=self.lab.image_dir/vios.ABOOT_IMAGE;boot.write_bytes(iso_bytes())
        node=self.lab.node('a');directory=self.lab.node_dir('a')
        disk=vios.disk_for(node,directory,self.lab.image_dir,lab_server.run,ValueError)
        lab_server.run('qemu-io','-f','qcow2','-c','write -P 0x42 0 4096',str(disk))
        result=lab_backup.create(self.lab)
        stream,_=lab_backup.take(self.lab,result['url'].rsplit('/',1)[1])
        try: content=stream.read()
        finally: stream.close()
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            manifest=json.loads(z.read('manifest.json'))
            self.assertEqual({i['name'] for i in manifest['images']},{self.name,vios.ABOOT_IMAGE})
            self.assertFalse(any(n.endswith('.iso') for n in z.namelist()))
        disk.unlink();lab_backup.restore(self.lab,io.BytesIO(content),lab_server.run)
        lab_server.run('qemu-io','-f','qcow2','-c','read -P 0x42 0 4096',str(disk))
        original=disk.read_bytes()
        boot.write_bytes(iso_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError,'does not match backup'):
            lab_backup.restore(self.lab,io.BytesIO(content),lab_server.run)
        self.assertEqual(disk.read_bytes(),original)
        boot.unlink()
        with self.assertRaisesRegex(ValueError,'Aboot'): lab_backup.restore(self.lab,io.BytesIO(content),lab_server.run)
        with self.assertRaisesRegex(ValueError,'Aboot'): lab_backup.create(self.lab)

    @unittest.skipUnless(shutil.which('qemu-img') and shutil.which('qemu-io'), 'Requires qemu-utils')
    def test_compact_backup_exact_restore_and_legacy_option(self):
        import disk_delta
        image = self.lab.image_dir/self.name
        raw = self.root/'base.raw'
        payload = self.root/'payload'
        payload.write_bytes(os.urandom(2 * 1024**2))
        with raw.open('wb') as f:
            f.write(payload.read_bytes())
            f.truncate(16 * 1024**2)
        image.unlink()
        lab_server.run('qemu-img', 'convert', '-f', 'raw', '-O', 'qcow2', str(raw), str(image))
        (self.lab.image_dir/vios.ABOOT_IMAGE).write_bytes(iso_bytes())
        node = self.lab.node('a')
        disk = vios.disk_for(node, self.lab.node_dir('a'), self.lab.image_dir, lab_server.run, ValueError)
        lab_server.run('qemu-io', '-f', 'qcow2', '-c', f'write -s {payload} 4M 2M', str(disk))
        original = disk.read_bytes()
        original_base = image.read_bytes()

        def export(compact):
            result = lab_backup.create(self.lab, compact_veos=compact)
            stream, _ = lab_backup.take(self.lab, result['url'].rsplit('/', 1)[1])
            with stream:
                return stream.read()

        compact, legacy = export(True), export(False)
        self.assertLess(len(compact), len(legacy) // 10)
        self.assertEqual(disk.read_bytes(), original)
        self.assertEqual(image.read_bytes(), original_base)
        with zipfile.ZipFile(io.BytesIO(compact)) as z:
            manifest = json.loads(z.read('manifest.json'))
            self.assertEqual(manifest['version'], 2)
            self.assertEqual(manifest['files'][0]['encoding'], disk_delta.ENCODING)
            self.assertTrue(manifest['files'][0]['path'].endswith('.wl-delta'))
        with zipfile.ZipFile(io.BytesIO(legacy)) as z:
            self.assertEqual(json.loads(z.read('manifest.json'))['version'], 1)
        for archive in (compact, legacy):
            disk.write_bytes(b'old state')
            lab_backup.restore(self.lab, io.BytesIO(archive), lab_server.run)
            self.assertEqual(disk.read_bytes(), original)

        def broken(change):
            out = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(compact)) as src, zipfile.ZipFile(out, 'w') as dst:
                meta = json.loads(src.read('manifest.json'))
                change(meta)
                for name in src.namelist():
                    dst.writestr(name, json.dumps(meta) if name == 'manifest.json' else src.read(name))
            return out.getvalue()

        cases = [lambda m: m.update(version=1),
                 lambda m: m['files'][0].update(encoding='unknown'),
                 lambda m: m['files'][0].update(restored_size=lab_backup.MAX_BYTES + 1),
                 lambda m: m['files'][0].update(restored_sha256='0'*64),
                 lambda m: m['files'][0].update(sha256='0'*64)]
        for change in cases:
            with self.assertRaises(ValueError):
                lab_backup.restore(self.lab, io.BytesIO(broken(change)), lab_server.run)
            self.assertEqual(disk.read_bytes(), original)
            self.assertFalse(list(self.lab.directory.glob('.restore-*')))
        with self.assertRaisesRegex(ValueError, 'compact_veos'):
            lab_backup.create(self.lab, compact_veos='yes')
