"""EXOS profile and port contracts without booting vendor images."""
import copy
import io
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import lab_server
import lab_backup
import console_capture
import saved_config
import vios

class ExosTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='exos-test-')
        self.root=Path(self.tmp.name)
        self.lab=lab_server.Lab(self.root/'data',self.root/'images')
        self.name='EXOS-VM_33.1.1.31.qcow2'
        (self.lab.image_dir/self.name).write_bytes(b'QFI\xfb'+bytes(100))
        self.lab.save({'name':'EXOS','nodes':[{'id':'x','name':'X','type':'switch','image':self.name}], 'links':[]})
    def tearDown(self):
        self.lab.file_lock.close();self.tmp.cleanup()
    def test_profile_ports_and_disk_state(self):
        node=self.lab.node('x')
        self.assertIn({'name':self.name,'type':'switch'},self.lab.catalog())
        self.assertTrue(vios.is_qemu(node));self.assertTrue(vios.is_exos(node));self.assertFalse(vios.is_vios(node))
        self.assertEqual(lab_server.ports(node),['Mgmt']+list(map(str,range(1,13))))
        self.assertEqual(lab_server.netmap_port(node,'Mgmt'),'0/0')
        self.assertEqual(lab_server.netmap_port(node,'12'),'3/0')
        disk=vios.disk_paths(node,self.root)[1]
        self.assertTrue(lab_backup.saved_path(node,disk.name))
        args=vios.command(node,disk,self.root)
        self.assertEqual(args[args.index('-cpu')+1], 'Nehalem-v1,rdtscp=on')
        self.assertEqual(sum('rtl8139,netdev=' in a for a in args),13)
        self.assertTrue(any('if=ide' in a for a in args))
        self.assertIn('stdio',args)
        self.assertNotIn('e1000',str(args))
        topology=copy.deepcopy(self.lab.topology)
        self.lab.save(topology);self.assertEqual(self.lab.topology,topology)
    def test_limits_snippets_and_cisco_handlers_rejected(self):
        for change in ({'type':'router'},{'ethernet':1},{'ethernet':14},{'startup_config':'hostname Wrong\nend\n'}):
            data=copy.deepcopy(self.lab.topology);data['nodes'][0].update(change)
            with self.subTest(change=change),self.assertRaises(lab_server.LabError):self.lab.save(data)
        with patch('console_capture.Console',side_effect=AssertionError('console touched')):
            with self.assertRaisesRegex(ValueError,'EXOS'):console_capture.export(self.lab)
        with patch('saved_config.read_node',side_effect=AssertionError('storage touched')):
            with self.assertRaisesRegex(ValueError,'EXOS'):saved_config.export(self.lab,lab_server.run)
    def test_management_and_data_port_cables(self):
        data=copy.deepcopy(self.lab.topology)
        data['nodes'].append({'id':'y','name':'Y','type':'switch','image':self.name})
        data['links']=[{'id':'l','a':{'node':'x','port':'12'},'b':{'node':'y','port':'1'}}]
        self.lab.save(data);self.lab.write_netmap()
        self.assertIn(':3/0@',(self.lab.directory/'NETMAP').read_text())
        for port in ('Gi0/0','0','13'):
            data['links'][0]['a']['port']=port
            with self.assertRaises(lab_server.LabError):self.lab.save(data)

    @unittest.skipUnless(shutil.which('qemu-img') and shutil.which('qemu-io'), 'Requires qemu-utils')
    def test_exos_overlay_archive_roundtrip(self):
        node=self.lab.node('x');image=self.lab.image_dir/self.name
        image.unlink();lab_server.run('qemu-img','create','-f','qcow2',str(image),'16M')
        directory=self.lab.node_dir('x')
        disk=vios.disk_for(node,directory,self.lab.image_dir,lab_server.run,ValueError)
        lab_server.run('qemu-io','-f','qcow2','-c','write -P 0x42 0 4096',str(disk))
        result=lab_backup.create(self.lab)
        stream,_=lab_backup.take(self.lab,result['url'].rsplit('/',1)[1])
        try:archive=stream.read()
        finally:stream.close()
        disk.unlink()
        lab_backup.restore(self.lab,io.BytesIO(archive),lab_server.run)
        lab_server.run('qemu-io','-f','qcow2','-c','read -P 0x42 0 4096',str(disk))
        self.assertEqual(vios.disk_paths(node,directory)[0].resolve(),image.resolve())
