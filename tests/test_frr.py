"""FRR topology, saved-state safety and lifecycle rollback without Docker/root."""
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import console_capture
import frr
import lab_backup
import lab_server
import saved_config
import vios


class FrrTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='frr-unit-')
        self.root = Path(self.temp.name)
        self.lab = lab_server.Lab(self.root/'data', self.root/'images')
        self.lab.netio = self.root/'netio'
        self.lab.netl1 = self.root/'netl1'
        self.lab.save({'name':'FRR test', 'nodes':[{'id':'r','name':'R','type':'router','image':frr.IMAGE}], 'links':[]})

    def tearDown(self):
        # Runtime entries below are synthetic, never real containers/processes.
        self.lab.runtime.clear()
        if self.lab.fabric:self.lab.fabric.close()
        lab_backup.expire(self.lab,all_files=True)
        self.lab.file_lock.close()
        self.temp.cleanup()

    def test_profile_and_validation(self):
        node=self.lab.node('r')
        self.assertEqual(node['memory'],512)
        self.assertEqual(lab_server.ports(node),['eth0','eth1','eth2','eth3'])
        self.assertEqual(lab_server.netmap_port(node,'eth3'),'0/3')
        self.assertEqual(vios.required_images([node]),set())
        self.assertIn({'name':frr.IMAGE,'type':'router'},self.lab.catalog())
        for change in ({'type':'switch'},{'image':'quay.io/frrouting/frr:latest'},{'ethernet':9}):
            data=copy.deepcopy(self.lab.topology);data['nodes'][0].update(change)
            with self.subTest(change=change),self.assertRaises(lab_server.LabError):self.lab.save(data)
        data=copy.deepcopy(self.lab.topology)
        data['nodes'][0].update(ethernet=8,startup_config='hostname BASELINE\n')
        self.lab.save(data)
        self.assertEqual(lab_server.netmap_port(self.lab.node('r'),'eth7'),'1/3')
        with self.assertRaisesRegex(ValueError,'FRR live capture'):console_capture.handler_for(node)

    def test_image_is_pinned_and_missing_pull_is_actionable(self):
        self.assertEqual(frr.check_image(lambda *a:json.dumps([{'Id':'sha256:'+'a'*64,'RepoDigests':[frr.DIGEST]}]),ValueError),'sha256:'+'a'*64)
        with self.assertRaisesRegex(ValueError,'tested digest'):
            frr.check_image(lambda *a:json.dumps([{'Id':'sha256:'+'b'*64,'RepoDigests':[]}]),ValueError)
        with self.assertRaisesRegex(ValueError,'docker pull'):
            frr.check_image(lambda *a:'',ValueError)

    def test_untested_image_requires_operator_opt_in(self):
        image_id = 'sha256:' + 'b'*64
        run = lambda *a: json.dumps([{'Id':image_id, 'RepoDigests':[]}])
        with self.assertRaisesRegex(ValueError, 'WL_ALLOW_UNTESTED_FRR'):
            frr.check_image(run, ValueError)
        self.assertEqual(frr.check_image(run, ValueError, True), image_id)
        self.assertEqual(frr.archive_image(run, ValueError, True), {'name':frr.IMAGE,'digest':image_id})
        with self.assertRaisesRegex(ValueError, 'invalid FRR image ID'):
            frr.check_image(lambda *a:json.dumps([{'Id':'bad'}]), ValueError, True)
        # Importing topology cannot opt the server into an untested image.
        topology = copy.deepcopy(self.lab.topology)
        topology['allow_untested_frr'] = True
        self.lab.save(topology)
        self.assertFalse(self.lab.allow_untested_frr)

    def test_custom_image_archive_requires_the_exact_image_and_opt_in(self):
        path = self.config()
        self.lab.allow_untested_frr = True
        image_id = 'sha256:' + 'b'*64
        inspect = {'Id':image_id,'RepoDigests':[]}
        with patch('lab_server.run', return_value=json.dumps([inspect])):
            result = lab_backup.create(self.lab)
        stream, _ = lab_backup.take(self.lab,result['url'].rsplit('/',1)[1])
        with stream: content = stream.read()
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['containers'], [{'name':frr.IMAGE,'digest':image_id}])
        path.write_text('hostname KEEP\n')
        self.lab.allow_untested_frr = False
        run = lambda *a: json.dumps([inspect])
        with self.assertRaisesRegex(ValueError, 'tested digest'):
            lab_backup.restore(self.lab,io.BytesIO(content),run)
        self.lab.allow_untested_frr = True
        inspect['Id'] = 'sha256:'+'c'*64
        with self.assertRaisesRegex(ValueError, 'exact saved image'):
            lab_backup.restore(self.lab,io.BytesIO(content),run)
        self.assertEqual(path.read_text(), 'hostname KEEP\n')
        inspect['Id'] = image_id
        lab_backup.restore(self.lab,io.BytesIO(content),run)
        self.assertEqual(path.read_text(),'hostname SAVED\n')

    def test_image_policy_options(self):
        with patch.dict(os.environ, {'WL_ALLOW_UNTESTED_FRR':'0'}):
            self.assertFalse(lab_server.parse_args([]).allow_untested_frr)
            self.assertTrue(lab_server.parse_args(['--allow-untested-frr']).allow_untested_frr)
        with patch.dict(os.environ, {'WL_ALLOW_UNTESTED_FRR':'1'}):
            self.assertTrue(lab_server.parse_args([]).allow_untested_frr)
        with patch.dict(os.environ, {'WL_ALLOW_UNTESTED_FRR':'typo'}):
            with self.assertRaises(SystemExit): lab_server.parse_args([])

    def config(self, data=b'hostname SAVED\n'):
        path=frr.config_path(self.lab.node('r'),self.lab.node_dir('r'))
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        return path

    def test_saved_json_and_archive_restore_preserve_config(self):
        path=self.config()
        self.lab.node('r')['startup_config']='hostname INITIAL\n'
        result=saved_config.export(self.lab,lambda *a: self.fail('Cisco disk reader used'))
        self.assertEqual(result['nodes'][0]['startup_config'],'hostname SAVED\n')
        self.assertEqual(self.lab.node('r')['startup_config'],'hostname INITIAL\n')
        with patch('frr.inspect_image',return_value={'Id':'sha256:'+'a'*64,'tested':True}):
            result=lab_backup.create(self.lab)
            stream,_=lab_backup.take(self.lab,result['url'].rsplit('/',1)[1])
            with stream: content=stream.read()
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                manifest=json.loads(archive.read('manifest.json'))
                self.assertEqual(manifest['images'],[])
                self.assertEqual(manifest['containers'],[{'name':frr.IMAGE,'digest':frr.DIGEST}])
                self.assertEqual(len(manifest['files']),1)
                self.assertTrue(manifest['files'][0]['path'].endswith('/frr.conf'))
            path.write_text('hostname OLD\n')
            lab_backup.restore(self.lab,io.BytesIO(content),lambda *a: self.fail('QEMU called'))
            self.assertEqual(path.read_text(),'hostname SAVED\n')
            bad=io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(content)) as src,zipfile.ZipFile(bad,'w') as dst:
                manifest['containers'][0]['digest']='wrong'
                for name in src.namelist():dst.writestr(name,json.dumps(manifest) if name=='manifest.json' else src.read(name))
            with self.assertRaisesRegex(ValueError,'container image metadata'):
                lab_backup.restore(self.lab,io.BytesIO(bad.getvalue()),lambda *a:None)
            self.assertEqual(path.read_text(),'hostname SAVED\n')
        self.assertFalse(lab_backup.saved_path(self.lab.node('r'),'daemons'))
        self.lab.runtime['r']={}
        with self.assertRaisesRegex(ValueError,'Stop R'):saved_config.export(self.lab,lambda *a:None)

    def test_config_is_bounded_and_not_a_symlink(self):
        path=self.config(b'x'*(frr.MAX_CONFIG+1))
        with self.assertRaisesRegex(ValueError,'oversized'):frr.read_config(path)
        path.write_bytes(b'bad\0config')
        with self.assertRaises(ValueError):frr.read_config(path)
        path.unlink();path.symlink_to(self.root/'outside')
        with self.assertRaises(OSError):frr.read_config(path)
        path.unlink();path.write_bytes(b'!'+b'x'*16384)
        with self.assertRaisesRegex(ValueError,'16 KiB'):saved_config.export(self.lab,lambda *a:None)

    def test_start_failure_cleans_partial_ports_without_adopting_conflicts(self):
        calls=[]
        def fail_second_network(*args,**kwargs):
            calls.append(args)
            if args[:3]==('docker','network','create') and args[-1].endswith('-1'):
                raise lab_server.LabError('deliberate network failure')
            return ''
        with patch('frr.inspect_image',return_value={'Id':'sha256:'+'a'*64,'tested':True}), patch('lab_server.run',side_effect=fail_second_network):
            with self.assertRaisesRegex(lab_server.LabError,'deliberate network failure'):self.lab.start('r')
        self.assertEqual(self.lab.runtime,{})
        self.assertEqual(sum(c[:3]==('docker','network','rm') for c in calls),1)
        self.assertEqual(sum(c[:3]==('ip','link','delete') for c in calls),2)
        calls.clear()
        def conflict(*args,**kwargs):
            calls.append(args)
            if args[:3]==('ip','tuntap','add'):raise lab_server.LabError('File exists')
            return ''
        with patch('frr.inspect_image',return_value={'Id':'sha256:'+'a'*64,'tested':True}),patch('lab_server.run',side_effect=conflict):
            with self.assertRaisesRegex(lab_server.LabError,'File exists'):self.lab.start('r')
        self.assertFalse(any(c[:3]==('ip','link','delete') for c in calls))

    def test_failed_copy_keeps_container_and_previous_saved_config(self):
        path=self.config()
        self.lab.runtime['r']={'frr':True,'container':'test','container_created':True,'frr_configured':True}
        with patch('lab_server.run',side_effect=lab_server.LabError('copy failed')) as run:
            with self.assertRaisesRegex(lab_server.LabError,'container retained'):self.lab.stop('r')
        self.assertEqual(path.read_text(),'hostname SAVED\n')
        self.assertTrue(self.lab.runtime['r']['container_created'])
        self.assertFalse(any(c.args[:2]==('docker','rm') for c in run.call_args_list))


class TapAdapterTests(unittest.TestCase):
    def test_only_expected_fabric_peer_reaches_the_right_port(self):
        import socket
        import struct
        import tap_net
        bridge=tap_net.TapBridge({'id':100,'netio':'/unused','taps':['a','b'],
                                 'routes':{'1':{'id':900,'port':16}}})
        source,bridge.iou=socket.socketpair(socket.AF_UNIX,socket.SOCK_DGRAM)
        output=io.BytesIO()
        bridge.clients={1:{'socket':output}}
        frame=bytes.fromhex('01005e000005020000000001810000640800')+bytes(50)
        try:
            for header in ((101,900,16,16,0x100),(100,901,16,16,0x100),
                           (100,900,0,16,0x100),(100,900,16,0,0x100)):
                source.send(struct.pack('>HHBBH',*header)+frame)
                bridge.from_iou()
            self.assertEqual(output.getvalue(),b'')
            source.send(struct.pack('>HHBBH',100,900,16,16,0x100)+frame)
            bridge.from_iou()
            self.assertEqual(output.getvalue(),frame)
        finally:
            source.close();bridge.iou.close();bridge.selector.close()
