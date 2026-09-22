"""Junos profiles: port mapping, vendor guards and portable stopped disks."""
import copy
import io
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid
from unittest.mock import patch

import console_capture
import lab_backup
import lab_server
import saved_config
import vios


class JunosTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='junos-test-')
        self.root = Path(self.tmp.name)
        self.lab = lab_server.Lab(self.root/'data', self.root/'images')
        self.names = ['vJunos-switch-26.2R1.7.qcow2', 'vJunosEvolved-26.2R1.7-EVO.qcow2']
        for name in self.names:
            (self.lab.image_dir/name).write_bytes(b'QFI\xfb'+bytes(100))
        self.lab.save({'name':'Junos', 'nodes':[
            {'id':'s','name':'Switch','type':'switch','image':self.names[0]},
            {'id':'r','name':'Evolved','type':'router','image':self.names[1]}], 'links':[]})

    def tearDown(self):
        lab_backup.expire(self.lab, all_files=True)
        self.lab.file_lock.close()
        self.tmp.cleanup()

    def test_profiles(self):
        for id, memory, mgmt, prefix in [('s',5120,'fxp0','ge'),('r',8192,'re0:mgmt-0','et')]:
            n=self.lab.node(id)
            self.assertEqual(n['memory'],memory)
            self.assertEqual(lab_server.ports(n),[mgmt]+[f'{prefix}-0/0/{i}' for i in range(4)])
            self.assertEqual(lab_server.netmap_port(n,f'{prefix}-0/0/3'),'1/0')
            self.assertFalse(vios.is_vios(n))
            self.assertTrue(vios.is_junos(n))
            disk=vios.disk_paths(n,self.root)[1]
            self.assertTrue(lab_backup.saved_path(n,disk.name))
            args=vios.command(n,disk,self.root,boot=Path('/firmware'))
            self.assertIn('4,sockets=1,cores=4,threads=1',args)
            self.assertIn('stdio,id=console,signal=off',args)
            self.assertTrue(any('addr=0x3' in a for a in args))
            self.assertTrue(any('addr=0x8' in a for a in args))
            self.assertEqual(sum('virtio-net-pci,netdev=' in a for a in args),5)
            if id=='r': self.assertIn('-bios',args)
            else: self.assertIn('type=1,product=VM-VEX',args)
        self.assertIn({'name':self.names[0],'type':'switch'},self.lab.catalog())
        self.assertIn({'name':self.names[1],'type':'router'},self.lab.catalog())

    def test_evolved_uuid_survives_restart_and_restore(self):
        node = self.lab.node('r')
        def identity(n, directory):
            args = vios.command(n, directory/'disk.qcow2', directory/'sockets', boot=Path('/firmware'))
            self.assertEqual(args.count('-uuid'), 1)
            return uuid.UUID(args[args.index('-uuid')+1])
        first = identity(node, self.root/'first')
        self.assertNotEqual(first.int, 0)
        self.assertEqual(first, uuid.uuid5(uuid.NAMESPACE_DNS, 'weblab.network:r'))
        restored = copy.deepcopy(node)
        restored.update(name='Renamed', iol_id=999)
        self.assertEqual(first, identity(restored, self.root/'restored'))
        self.assertNotEqual(first, identity({**node, 'id':'another-node'}, self.root/'first'))
        switch_args = vios.command(self.lab.node('s'), self.root/'disk', self.root)
        self.assertNotIn('-uuid', switch_args)

    def test_guards(self):
        for index in (0,1):
            for change in ({'memory':1024},{'ethernet':1},{'ethernet':17},{'startup_config':'hostname wrong'},
                           {'type':'router' if index==0 else 'switch'}):
                data=copy.deepcopy(self.lab.topology); data['nodes'][index].update(change)
                with self.subTest(index=index,change=change),self.assertRaises(lab_server.LabError):self.lab.save(data)
            with self.assertRaisesRegex(ValueError,'Junos'):console_capture.handler_for(self.lab.topology['nodes'][index])
        with patch('saved_config.read_node',side_effect=AssertionError('disk read')):
            with self.assertRaisesRegex(ValueError,'Junos'):saved_config.export(self.lab,lab_server.run)
        with patch('pathlib.Path.read_text',return_value='flags: svm'):
            with self.assertRaisesRegex(ValueError,'Intel VT-x'):vios.boot_image(self.lab.node('s'),self.lab.image_dir,ValueError,launch=True)
        with patch('pathlib.Path.is_file',return_value=False):
            with self.assertRaisesRegex(ValueError,'OVMF'):vios.boot_image(self.lab.node('r'),self.lab.image_dir,ValueError,launch=True)
        with self.assertRaisesRegex(ValueError,'UEFI'):vios.command(self.lab.node('r'),self.root/'disk',self.root)

    @unittest.skipUnless(shutil.which('qemu-img') and shutil.which('qemu-io'),'Requires qemu-utils')
    def test_saved_disk_roundtrip(self):
        disks=[]
        for n in self.lab.topology['nodes']:
            image=self.lab.image_dir/n['image'];image.unlink()
            lab_server.run('qemu-img','create','-f','qcow2',str(image),'16M')
            disk=vios.disk_for(n,self.lab.node_dir(n['id']),self.lab.image_dir,lab_server.run,ValueError)
            lab_server.run('qemu-io','-f','qcow2','-c','write -P 0x42 0 4096',str(disk))
            disks.append(disk)
        # Portable backup operations must not require Intel VMX or firmware.
        with patch('vios.Path.read_text', side_effect=AssertionError('host CPU inspected')), \
                patch('vios.Path.is_file', return_value=False):
            for n in self.lab.topology['nodes']:
                self.assertIsNone(vios.boot_image(n,self.lab.image_dir,ValueError))
        result=lab_backup.create(self.lab)
        stream,_=lab_backup.take(self.lab,result['url'].rsplit('/',1)[1])
        try:content=stream.read()
        finally:stream.close()
        for disk in disks:disk.unlink()
        lab_backup.restore(self.lab,io.BytesIO(content),lab_server.run)
        for disk in disks:lab_server.run('qemu-io','-f','qcow2','-c','read -P 0x42 0 4096',str(disk))
