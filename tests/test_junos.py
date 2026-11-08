"""Junos profiles: port mapping, vendor guards and portable stopped disks."""
import copy
import io
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

import console_capture
import lab_backup
import lab_server
import qmp
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

    def test_native_console_prompts_with_boot_logs_and_input_echo(self):
        # Fragments observed during real boots. A boot log after the prompt
        # must not hide readiness, and the input's echoed prompt is not a reply.
        import junos_backup_native as native
        terminal = native.Terminal(None, 'test', self.root, [])
        cases = [
            ('show chassis fpc | no-more', 'root@JZSW>show chassis fpc | no-more', native.OP, False),
            ('show chassis fpc | no-more', 'root@JZSW>sho', native.OP, False),
            ('set interfaces et-0/0/0 unit 0 family inet address 192.0.2.1/24',
             'root@JZR# ...family inet address 192.0.2.1/24', native.CONFIG, False),
            ('show chassis fpc | no-more', 'root@JZSW> cat: /etc/vmxt/init.conf: No such file', native.OP, True),
            ('show chassis fpc | no-more', 'root@JZSW>nested_env.sh sees AWS as no', native.OP, True),
            ('cli', 'root@:~ #cli', native.SHELL, False),
            ('show chassis fpc | no-more', 'root@:~ #', native.SHELL, True),
            ('configure exclusive', '[edit]\nroot@JZR# [ 248.30] ztp.py: aborted', native.CONFIG, True),
            ('show chassis fpc | no-more', 'root@JZSW>Linux VMX #1', native.SHELL, False),
        ]
        for command, output, pattern, expected in cases:
            with self.subTest(output=output):
                terminal.last_input, terminal.output = command, output
                self.assertEqual(bool(terminal.match(pattern)), expected)

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

    def test_clean_guest_exit_is_stopped_but_failures_remain_visible(self):
        for node_id, key, code, failed in [('s', 'console', 0, False), ('r', 'console', 0, False),
                                           ('s', 'console', 1, True), ('s', 'console', None, True),
                                           ('s', 'bridge', 0, True)]:
            with self.subTest(node=node_id, process=key, code=code):
                self.lab.errors.clear()
                self.lab.runtime = {node_id: {key: {'pid': 12345}}}
                self.lab.children = {12345: Mock(poll=Mock(return_value=code))}
                shutdown = Mock(wait=Mock(side_effect=[False, True]))
                with patch.object(self.lab, 'alive', return_value=False), \
                        patch.object(self.lab, 'stop', side_effect=lambda ident: self.lab.runtime.pop(ident)) as stop:
                    self.lab.monitor(shutdown)
                    stop.assert_called_once_with(node_id)
                self.assertEqual(bool(self.lab.errors.get(node_id)), failed)
                self.assertEqual(self.lab.snapshot()['status'][node_id]['state'], 'error' if failed else 'stopped')
        self.lab.children.clear()

    def test_stop_acknowledgement_is_not_completion_and_timeout_never_kills(self):
        runtime = {'console': {'pid': 12345}, 'qemu_sockets': str(self.root / 'sockets')}
        self.lab.runtime['s'] = runtime
        with patch.object(self.lab, 'alive', return_value=True), \
                patch.object(self.lab, 'stop') as stop, patch('qmp.powerdown') as powerdown:
            self.lab.request_stop('s')
            self.lab.request_stop('s')
            powerdown.assert_called_once_with(self.root / 'sockets' / 'qmp')
            status = self.lab.snapshot()['status']['s']
            self.assertEqual(status['state'], 'running')
            self.assertTrue(status['shutdown_pending'])
            stop.assert_not_called()
            deadline = self.lab.junos_shutdowns['s']['deadline']
            with patch('lab_server.time.monotonic', return_value=deadline + 1):
                self.lab.monitor(Mock(wait=Mock(side_effect=[False, True])))
            self.assertFalse(self.lab.snapshot()['status']['s']['shutdown_pending'])
            self.assertIn('VM kept running', self.lab.errors['s'])
            self.assertIs(self.lab.runtime['s'], runtime)
            stop.assert_not_called()
            self.lab.request_stop('s')  # Explicit retry only.
            self.assertEqual(powerdown.call_count, 2)
        self.lab.children[12345] = Mock(poll=Mock(return_value=0))
        with patch.object(self.lab, 'alive', return_value=False), patch.object(self.lab, 'stop') as stop:
            self.lab.monitor(Mock(wait=Mock(side_effect=[False, True])))
            stop.assert_called_once_with('s')
        self.assertNotIn('s', self.lab.errors)
        self.lab.runtime.clear()
        self.lab.children.clear()

    def test_stop_failure_and_untested_profile_retain_guest(self):
        runtime = {'console': {'pid': 12345}, 'qemu_sockets': str(self.root / 'sockets')}
        self.lab.runtime['s'] = runtime
        with patch.object(self.lab, 'alive', return_value=True), \
                patch.object(self.lab, 'stop') as stop, \
                patch('qmp.powerdown', side_effect=qmp.QMPError('socket closed')) as powerdown:
            with self.assertRaisesRegex(lab_server.LabError, 'could not be confirmed'):
                self.lab.request_stop('s')
            self.lab.monitor(Mock(wait=Mock(side_effect=[False, True])))
            self.assertEqual(powerdown.call_count, 1)
            self.assertIs(self.lab.runtime['s'], runtime)
            self.lab.node('s')['image'] = 'vJunos-switch-untested.qcow2'
            with self.assertRaisesRegex(lab_server.LabError, 'manual CLI power-off'):
                self.lab.request_stop('s')
            self.assertEqual(powerdown.call_count, 1)
            stop.assert_not_called()
        self.lab.runtime.clear()

    def test_stop_all_requests_other_guests_even_if_one_request_fails(self):
        self.lab.runtime = {ident: {'console': {'pid': pid}, 'qemu_sockets': str(self.root / ident)}
                            for ident, pid in [('s', 12345), ('r', 12346)]}
        with patch.object(self.lab, 'alive', return_value=True), patch.object(self.lab, 'stop') as stop, \
                patch('qmp.powerdown', side_effect=[qmp.QMPError('failed'), None]) as powerdown:
            with self.assertRaisesRegex(lab_server.LabError, 'failed'):
                self.lab.request_stop()
            self.assertEqual(powerdown.call_count, 2)
            self.assertEqual(set(self.lab.runtime), {'s', 'r'})
            self.assertFalse(self.lab.snapshot()['status']['s']['shutdown_pending'])
            self.assertTrue(self.lab.snapshot()['status']['r']['shutdown_pending'])
            stop.assert_not_called()
        self.lab.runtime.clear()

    def test_bridge_failure_requests_shutdown_once_and_preserves_timeout(self):
        runtime = {'console': {'pid': 12345}, 'bridge': {'pid': 12346},
                   'qemu_sockets': str(self.root / 'sockets')}
        self.lab.runtime['s'] = runtime
        self.lab.children[12346] = Mock(poll=Mock(return_value=1))
        with patch.object(self.lab, 'alive', side_effect=lambda record: record['pid'] == 12345), \
                patch.object(self.lab, 'stop') as stop, patch('qmp.powerdown') as powerdown:
            self.lab.monitor(Mock(wait=Mock(side_effect=[False, False, True])))
            powerdown.assert_called_once()
            deadline = self.lab.junos_shutdowns['s']['deadline']
            with patch('lab_server.time.monotonic', return_value=deadline + 1):
                self.lab.monitor(Mock(wait=Mock(side_effect=[False, False, True])))
            self.assertIn('180 seconds', self.lab.errors['s'])
            self.assertEqual(powerdown.call_count, 1)
            stop.assert_not_called()
            self.assertIs(self.lab.runtime['s'], runtime)
        self.lab.runtime.clear()
        self.lab.children.clear()

    def test_shutdown_record_does_not_follow_a_replacement_runtime(self):
        runtime = {'console': {'pid': 12345}, 'qemu_sockets': str(self.root / 'sockets')}
        self.lab.runtime['s'] = runtime
        self.lab.junos_shutdowns['s'] = {'runtime': dict(runtime), 'phase': 'pending', 'deadline': 0}
        with patch.object(self.lab, 'alive', return_value=True), patch('qmp.powerdown') as powerdown:
            self.lab.monitor(Mock(wait=Mock(side_effect=[False, True])))
            self.assertNotIn('s', self.lab.junos_shutdowns)
            self.assertNotIn('s', self.lab.errors)
            powerdown.assert_not_called()
        self.lab.runtime.clear()

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
