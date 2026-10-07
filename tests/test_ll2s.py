"""LL2S validation, persistence, archive identity and failed-start ownership."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import console_capture
import container_console
import frr
import lab_backup
import lab_server
import ll2s_device as ll2s
import saved_config
import vios


class Ll2sTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ll2s-unit-')
        self.root = Path(self.temp.name)
        self.lab = lab_server.Lab(self.root/'data', self.root/'images')
        self.lab.netio = self.root/'netio'
        self.lab.netl1 = self.root/'netl1'
        self.lab.save({'name': 'Switch', 'nodes': [
            {'id': 's', 'name': 'SW1', 'type': 'switch', 'image': ll2s.IMAGE}], 'links': []})

    def tearDown(self):
        self.lab.runtime.clear()  # synthetic runtime entries only
        if self.lab.fabric:
            self.lab.fabric.close()
        lab_backup.expire(self.lab, all_files=True)
        self.lab.file_lock.close()
        self.temp.cleanup()

    def config(self, data=b'hostname SAVED\n'):
        path = ll2s.config_path(self.lab.node('s'), self.lab.node_dir('s'))
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        ll2s.write_identity(path, data, 'sha256:'+'a'*64)
        return path

    def test_profile(self):
        node = self.lab.node('s')
        self.assertEqual(node['memory'], 256)
        self.assertEqual(lab_server.ports(node), ['eth0', 'eth1', 'eth2', 'eth3'])
        self.assertEqual(lab_server.netmap_port(node, 'eth3'), '0/3')
        self.assertEqual(vios.required_images([node]), set())
        self.assertIn({'name': ll2s.IMAGE, 'type': 'switch'}, self.lab.catalog())
        for change in ({'type': 'router'}, {'image': 'll2s:latest'}, {'ethernet': 9}):
            data = copy.deepcopy(self.lab.topology)
            data['nodes'][0].update(change)
            with self.subTest(change=change), self.assertRaises(lab_server.LabError):
                self.lab.save(data)
        with self.assertRaisesRegex(ValueError, 'LL2S live capture'):
            console_capture.handler_for(node)
        baseline = ll2s.baseline(node)
        self.assertIn('spanning-tree mode rstp', baseline)
        self.assertEqual(baseline.count('interface eth'), 4)

    def test_image_error_and_identity(self):
        with self.assertRaisesRegex(ValueError, 'docker pull'):
            ll2s.inspect_image(lambda *a: '[]', ValueError)
        with self.assertRaisesRegex(ValueError, 'invalid LL2S image ID'):
            ll2s.inspect_image(lambda *a: '[{"Id":"bad"}]', ValueError)
        self.assertEqual(ll2s.archive_image(lambda *a: json.dumps([{'Id': 'sha256:'+'a'*64}]), ValueError),
                         {'name': ll2s.IMAGE, 'digest': 'sha256:'+'a'*64})

    def test_legacy_tag_and_mixed_selectors_zip_preserve_state(self):
        data = copy.deepcopy(self.lab.topology)
        data['nodes'].append({'id': 'legacy', 'name': 'OLD', 'type': 'switch',
                              'image': ll2s.LEGACY_IMAGE})
        self.lab.save(data)
        self.config()
        node = self.lab.node('legacy')
        path = ll2s.config_path(node, self.lab.node_dir('legacy'))
        path.parent.mkdir()
        path.write_bytes(b'hostname LEGACY\n')
        ll2s.write_identity(path, path.read_bytes(), 'sha256:'+'b'*64)
        self.assertIn({'name': ll2s.LEGACY_IMAGE, 'type': 'switch'}, self.lab.catalog())
        self.assertEqual(lab_server.ports(node), ['eth0', 'eth1', 'eth2', 'eth3'])
        with patch('ll2s_device.inspect_image') as inspect:
            result = lab_backup.create(self.lab)
            stream, _ = lab_backup.take(self.lab, result['url'].rsplit('/', 1)[1])
            with stream:
                content = stream.read()
            inspect.assert_not_called()
            calls = []
            def installed(run, error, name=ll2s.IMAGE):
                calls.append(name)
                return {'Id': 'sha256:' + ('b' if name == ll2s.LEGACY_IMAGE else 'a')*64}
            inspect.side_effect = installed
            lab_backup.restore(self.lab, io.BytesIO(content), lambda *a: None)
            self.assertEqual(set(calls), set(ll2s.IMAGES))
        self.assertEqual(path.read_bytes(), b'hostname LEGACY\n')
        self.assertEqual(self.lab.node('legacy')['image'], ll2s.LEGACY_IMAGE)
        self.assertEqual(ll2s.saved_image(path, ll2s.LEGACY_IMAGE)['name'], ll2s.LEGACY_IMAGE)
        # Legacy imports retain their old metadata name, not the new default.
        self.lab.save({**self.lab.topology, 'nodes': [self.lab.node('legacy')]})
        with patch('ll2s_device.inspect_image', return_value={'Id': 'sha256:'+'b'*64}):
            result = lab_backup.create(self.lab)
            stream, _ = lab_backup.take(self.lab, result['url'].rsplit('/', 1)[1])
            with stream:
                content = stream.read()
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                self.assertEqual(json.loads(archive.read('manifest.json'))['containers'],
                                 [{'name': ll2s.LEGACY_IMAGE, 'digest': 'sha256:'+'b'*64}])
            lab_backup.restore(self.lab, io.BytesIO(content), lambda *a: None)
        self.assertEqual(path.read_bytes(), b'hostname LEGACY\n')

    def test_image_inspection_uses_requested_selector(self):
        calls = []
        def run(*args):
            calls.append(args)
            return json.dumps([{'Id': 'sha256:'+'a'*64}])
        ll2s.inspect_image(run, ValueError, ll2s.LEGACY_IMAGE)
        self.assertEqual(calls, [('docker', 'image', 'inspect', ll2s.LEGACY_IMAGE)])
        with self.assertRaisesRegex(ValueError, 'docker build'):
            ll2s.inspect_image(lambda *a: '[]', ValueError, ll2s.LEGACY_IMAGE)
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            ll2s.inspect_image(run, ValueError, 'll2s:unknown')

    def test_mixed_archive_restore_and_saved_export(self):
        path = self.config()
        self.lab.node('s')['startup_config'] = 'hostname ORIGINAL\n'
        exported = saved_config.export(self.lab, lambda *a: self.fail('Cisco reader called'))
        self.assertEqual(exported['nodes'][0]['startup_config'], 'hostname SAVED\n')
        self.assertEqual(self.lab.node('s')['startup_config'], 'hostname ORIGINAL\n')
        data = copy.deepcopy(self.lab.topology)
        data['nodes'].append({'id': 'r', 'name': 'R', 'type': 'router', 'image': frr.IMAGE})
        self.lab.save(data)
        image = {'Id': 'sha256:'+'a'*64}
        with patch('ll2s_device.inspect_image', return_value=image), patch('frr.inspect_image', return_value={**image, 'tested': True}):
            result = lab_backup.create(self.lab)
            stream, _ = lab_backup.take(self.lab, result['url'].rsplit('/', 1)[1])
            with stream:
                content = stream.read()
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                manifest = json.loads(archive.read('manifest.json'))
                self.assertEqual(manifest['images'], [])
                self.assertEqual(len(manifest['containers']), 2)
                self.assertTrue(any(f['path'].endswith('/startup.conf') for f in manifest['files']))
            path.write_text('hostname KEEP\n')
            image['Id'] = 'sha256:'+'b'*64
            with self.assertRaisesRegex(ValueError, 'exact saved image'):
                lab_backup.restore(self.lab, io.BytesIO(content), lambda *a: None)
            self.assertEqual(path.read_text(), 'hostname KEEP\n')
            image['Id'] = 'sha256:'+'a'*64
            lab_backup.restore(self.lab, io.BytesIO(content), lambda *a: self.fail('QEMU called'))
            self.assertEqual(path.read_text(), 'hostname SAVED\n')
            for containers in ([], manifest['containers']*2, [manifest['containers'][0]]*2):
                bad = io.BytesIO()
                with zipfile.ZipFile(io.BytesIO(content)) as src, zipfile.ZipFile(bad, 'w') as dst:
                    for name in src.namelist():
                        dst.writestr(name, json.dumps({**manifest, 'containers': containers}) if name == 'manifest.json' else src.read(name))
                with self.assertRaisesRegex(ValueError, 'container image metadata'):
                    lab_backup.restore(self.lab, io.BytesIO(bad.getvalue()), lambda *a: None)
        self.assertFalse(lab_backup.saved_path(self.lab.node('s'), 'ovs/conf.db'))

    def test_export_uses_saved_image_after_tag_rebuild(self):
        self.config()
        with patch('ll2s_device.inspect_image', return_value={'Id': 'sha256:'+'b'*64}) as inspect:
            result = lab_backup.create(self.lab)
            stream, _ = lab_backup.take(self.lab, result['url'].rsplit('/', 1)[1])
            with stream:
                data = stream.read()
            inspect.assert_not_called()
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.assertEqual(json.loads(archive.read('manifest.json'))['containers'],
                                 [{'name': ll2s.IMAGE, 'digest': 'sha256:'+'a'*64}])
            with self.assertRaisesRegex(ValueError, 'exact saved image'):
                lab_backup.restore(self.lab, io.BytesIO(data), lambda *a: None)

    def test_legacy_and_mismatched_identity_require_verification(self):
        path = self.config()
        ll2s.identity_path(path).unlink()
        with self.assertRaisesRegex(ValueError, 'identity is unknown'):
            ll2s.export_image(self.lab, lambda *a: self.fail('must not guess tag'), ValueError)
        self.assertIn('hostname SAVED', saved_config.export(self.lab, lambda *a: None)['nodes'][0]['startup_config'])
        ll2s.write_identity(path, path.read_bytes(), 'sha256:'+'a'*64)
        path.write_text('hostname CHANGED\n')
        with self.assertRaisesRegex(ValueError, 'does not match'):
            ll2s.saved_image(path)
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'without its saved configuration'):
            ll2s.export_image(self.lab, lambda *a: None, ValueError)

    def test_save_persists_launch_identity_and_recovers_partial_write(self):
        path = self.config()
        self.lab.runtime['s'] = {'ll2s_configured': True, 'container_created': True,
                                'container': 'fake', 'll2s_image_id': 'sha256:'+'b'*64}
        def copy(*args):
            Path(args[-1]).write_text('hostname NEW\n')
        with patch('ll2s_device.write_identity', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                ll2s.save(self.lab, 's', copy)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            ll2s.saved_image(path)
        ll2s.save(self.lab, 's', copy)
        self.assertEqual(ll2s.saved_image(path)['digest'], 'sha256:'+'b'*64)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(ll2s.identity_path(path).stat().st_mode & 0o777, 0o600)
        self.lab.runtime['s'].update(ll2s_image_id='sha256:'+'c'*64, ll2s_ready=False)
        ll2s.save(self.lab, 's', copy)
        self.assertEqual(ll2s.saved_image(path)['digest'], 'sha256:'+'b'*64)

    def test_old_zip_restores_identity_and_conflicting_record_is_rejected(self):
        path = self.config()
        with patch('ll2s_device.inspect_image', return_value={'Id': 'sha256:'+'a'*64}):
            result = lab_backup.create(self.lab)
            stream, _ = lab_backup.take(self.lab, result['url'].rsplit('/', 1)[1])
            with stream:
                content = stream.read()
            for legacy in (True, False):
                rewritten = io.BytesIO()
                with zipfile.ZipFile(io.BytesIO(content)) as src, zipfile.ZipFile(rewritten, 'w') as dst:
                    manifest = json.loads(src.read('manifest.json'))
                    for f in list(manifest['files']):
                        data = src.read(f['path'])
                        if f['path'].endswith('/startup-image.json'):
                            if legacy:
                                manifest['files'].remove(f)
                                continue
                            record = json.loads(data)
                            record['image_id'] = 'sha256:'+'b'*64
                            data = json.dumps(record).encode()
                            f.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
                        dst.writestr(f['path'], data)
                    dst.writestr('topology.json', src.read('topology.json'))
                    dst.writestr('manifest.json', json.dumps(manifest))
                if legacy:
                    lab_backup.restore(self.lab, io.BytesIO(rewritten.getvalue()), lambda *a: None)
                    self.assertEqual(ll2s.saved_image(path)['digest'], 'sha256:'+'a'*64)
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                else:
                    with self.assertRaisesRegex(ValueError, 'does not match the archive'):
                        lab_backup.restore(self.lab, io.BytesIO(rewritten.getvalue()), lambda *a: None)
                    self.assertEqual(ll2s.saved_image(path)['digest'], 'sha256:'+'a'*64)

    def test_fresh_and_mixed_image_nodes(self):
        with patch('ll2s_device.inspect_image', return_value={'Id': 'sha256:'+'b'*64}):
            self.assertEqual(ll2s.export_image(self.lab, None, ValueError)['digest'], 'sha256:'+'b'*64)
            self.config()
            data = copy.deepcopy(self.lab.topology)
            data['nodes'].append({'id': 'fresh', 'name': 'Fresh', 'type': 'switch', 'image': ll2s.IMAGE})
            self.lab.save(data)
            with self.assertRaisesRegex(ValueError, 'different saved images'):
                ll2s.export_image(self.lab, None, ValueError)

    def test_config_limits_and_symlinks(self):
        path = self.config(b'x'*(ll2s.MAX_CONFIG+1))
        with self.assertRaisesRegex(ValueError, 'oversized'):
            ll2s.read_config(path)
        path.write_bytes(b'bad\0config')
        with self.assertRaises(ValueError):
            ll2s.read_config(path)
        path.unlink()
        path.symlink_to(self.root/'outside')
        with self.assertRaises(OSError):
            ll2s.read_config(path)
        path.unlink()
        path.write_bytes(b'x'*16385)
        with self.assertRaisesRegex(ValueError, '16 KiB'):
            saved_config.export(self.lab, lambda *a: None)

    def test_failed_copy_retains_container_and_saved_config(self):
        path = self.config()
        self.lab.runtime['s'] = {'ll2s': True, 'container': 'test', 'container_created': True, 'll2s_configured': True}
        with patch('lab_server.run', side_effect=lab_server.LabError('copy failed')) as run:
            with self.assertRaisesRegex(lab_server.LabError, 'container retained'):
                self.lab.stop('s')
        self.assertEqual(path.read_text(), 'hostname SAVED\n')
        self.assertTrue(self.lab.runtime['s']['container_created'])
        self.assertFalse(any(c.args[:2] == ('docker', 'rm') for c in run.call_args_list))

    def test_partial_start_cleanup_and_conflict_ownership(self):
        exists = Path.exists
        for conflict in (False, True):
            calls = []
            def fail(*args, **kwargs):
                calls.append(args)
                if conflict and args[:3] == ('ip', 'tuntap', 'add'):
                    raise lab_server.LabError('File exists')
                if args[:3] == ('docker', 'network', 'create') and args[-1].endswith('-1'):
                    raise lab_server.LabError('deliberate failure')
                if args[:3] == ('docker', 'network', 'create'):
                    return 'd'*64
                if args[:3] == ('docker', 'network', 'inspect'):
                    port = self.lab.runtime['s']['ll2s_ports'][0]
                    return json.dumps([{'Id': 'd'*64, 'Labels': {'iol.lab': self.lab.owner},
                                        'Driver': 'macvlan', 'Options': {'parent': port['tap']},
                                        'Containers': {}}])
                if args == ('ip', '-d', '-j', 'link', 'show'):
                    return json.dumps([{'ifname': p['tap'], 'ifalias': p.get('tap_alias', ''),
                        'linkinfo': {'info_kind': 'tun', 'info_data': {'type': 'tap'}}}
                        for p in self.lab.runtime['s']['ll2s_ports'] if p.get('tap_created')])
                return ''
            # Module presence is a host prerequisite, unrelated to this rollback test.
            with patch('ll2s_device.Path.exists', autospec=True, side_effect=lambda p: str(p) == '/sys/module/openvswitch' or exists(p)), patch('ll2s_device.read_config'), patch('ll2s_device.inspect_image', return_value={'Id': 'sha256:'+'a'*64}), patch('lab_server.run', side_effect=fail):
                with self.assertRaises(lab_server.LabError):
                    self.lab.start('s')
            self.assertFalse(self.lab.runtime)
            self.assertEqual(sum(c[:3] == ('ip', 'link', 'delete') for c in calls), 0 if conflict else 2)
            self.assertEqual(sum(c[:3] == ('docker', 'network', 'rm') for c in calls), 0 if conflict else 1)

    def recovery_fixture(self):
        path = self.config()
        node = self.lab.node('s')
        stem = f"wl-{self.lab.owner}-{node['iol_id']}"
        port = {'network': stem+'-0', 'tap': f"lt{self.lab.owner}{node['iol_id']:03x}0",
                'network_created': True, 'tap_created': True}
        runtime = {'ll2s': True, 'iol_id': node['iol_id'], 'container': stem,
                   'container_created': True, 'll2s_configured': True,
                   'll2s_image_id': 'sha256:'+'a'*64, 'll2s_ports': [port]}
        self.lab.runtime['s'] = runtime
        network = {'Id': 'd'*64, 'Labels': {'iol.lab': self.lab.owner},
                   'Driver': 'macvlan', 'Options': {'parent': port['tap']}, 'Containers': {}}
        calls = []
        def run(*args, **kwargs):
            calls.append(args)
            if args[:3] == ('docker', 'container', 'inspect'):
                raise lab_server.LabError('Error: No such container: '+stem)
            if args[:3] == ('docker', 'network', 'inspect'):
                return json.dumps([network])
            if args == ('ip', '-d', '-j', 'link', 'show'):
                return '[]'  # TAPs do not survive a reboot.
            if args[:3] == ('docker', 'network', 'rm'):
                return args[-1]
            self.fail('Unexpected recovery command: '+repr(args))
        return path, runtime, network, calls, run

    def test_missing_container_recovery_at_server_start(self):
        path, runtime, network, calls, run = self.recovery_fixture()
        before = (path.read_bytes(), ll2s.identity_path(path).read_bytes())
        self.lab.journal()
        self.lab.file_lock.close()
        with patch('lab_server.run', side_effect=run):
            self.lab = lab_server.Lab(self.root/'data', self.root/'images')
            self.lab.stop('s')  # idempotent after startup cleanup
        self.assertFalse(self.lab.runtime)
        self.assertEqual(before, (path.read_bytes(), ll2s.identity_path(path).read_bytes()))
        self.assertIn(('docker', 'network', 'rm', network['Id']), calls)

    def test_cleanup_refuses_foreign_and_in_use_networks_then_retries(self):
        for change in ({'Labels': {'iol.lab': 'foreign'}}, {'Containers': {'foreign': {}}},
                       {'Options': {'parent': 'another-tap'}}, {'Id': 'e'*64}):
            with self.subTest(change=change):
                path, runtime, network, calls, run = self.recovery_fixture()
                runtime['ll2s_ports'][0]['network_id'] = 'd'*64
                original = dict(network)
                network.update(change)
                with patch('lab_server.run', side_effect=run):
                    with self.assertRaisesRegex(lab_server.LabError, 'retry Stop'):
                        self.lab.stop('s')
                    self.assertFalse(any(c[:3] in (('docker','network','rm'),
                                                   ('ip','link','delete')) for c in calls))
                    self.assertNotIn('container_created', runtime)
                    network.clear(); network.update(original)
                    self.lab.stop('s')
                self.assertFalse(self.lab.runtime)

    def test_daemon_failure_and_foreign_container_are_not_absence(self):
        for result in (lab_server.LabError('Cannot connect to the Docker daemon'),
                       [{'Id': 'e'*64, 'Config': {'Labels': {'iol.lab': 'foreign'}}}]):
            path, runtime, network, calls, run = self.recovery_fixture()
            def inspect(*args, **kwargs):
                if args[:3] == ('docker', 'container', 'inspect'):
                    if isinstance(result, Exception):
                        raise result
                    return json.dumps(result)
                return run(*args, **kwargs)
            with patch('lab_server.run', side_effect=inspect):
                with self.assertRaisesRegex(lab_server.LabError, 'container retained'):
                    self.lab.stop('s')
            self.assertTrue(runtime['container_created'])
            self.assertEqual(path.read_text(), 'hostname SAVED\n')
            self.assertEqual(calls, [])

    def test_existing_container_copy_failure_keeps_resources(self):
        path, runtime, network, calls, run = self.recovery_fixture()
        def inspect(*args, **kwargs):
            if args[:3] == ('docker', 'container', 'inspect'):
                return json.dumps([{'Id': 'c'*64, 'Config': {'Labels': {'iol.lab': self.lab.owner}}}])
            if args[:2] == ('docker', 'cp'):
                raise lab_server.LabError('copy failed')
            return run(*args, **kwargs)
        with patch('lab_server.run', side_effect=inspect):
            with self.assertRaisesRegex(lab_server.LabError, 'container retained.*copy failed'):
                self.lab.stop('s')
        self.assertTrue(runtime['container_created'])
        self.assertTrue(runtime['ll2s_ports'][0]['network_created'])
        self.assertEqual(calls, [])
        self.assertEqual(path.read_text(), 'hostname SAVED\n')

    def test_missing_network_and_partial_journal_are_safe(self):
        path, runtime, network, calls, run = self.recovery_fixture()
        # A journaled name without a creation flag is not authority to remove it.
        runtime['ll2s_ports'].append({'network': 'foreign', 'tap': 'foreign'})
        def missing(*args, **kwargs):
            if args[:3] == ('docker', 'network', 'inspect'):
                raise lab_server.LabError('Error response from daemon: network '+args[-1]+' not found')
            return run(*args, **kwargs)
        with patch('lab_server.run', side_effect=missing):
            self.lab.stop('s')
        self.assertFalse(self.lab.runtime)
        self.assertFalse(any('foreign' in c for c in calls))

    def test_replaced_tap_is_retained_after_network_cleanup(self):
        path, runtime, network, calls, run = self.recovery_fixture()
        port = runtime['ll2s_ports'][0]
        port['tap_alias'] = 'weblab-ll2s:original'
        def replaced(*args, **kwargs):
            if args == ('ip', '-d', '-j', 'link', 'show'):
                return json.dumps([{'ifname': port['tap'], 'ifalias': 'foreign',
                    'linkinfo': {'info_kind': 'tun', 'info_data': {'type': 'tap'}}}])
            return run(*args, **kwargs)
        with patch('lab_server.run', side_effect=replaced):
            with self.assertRaisesRegex(lab_server.LabError, 'ownership/type changed'):
                self.lab.stop('s')
        self.assertNotIn('network_created', port)
        self.assertTrue(port['tap_created'])
        self.assertFalse(any(c[:3] == ('ip', 'link', 'delete') for c in calls))
        with patch('lab_server.run', side_effect=run):
            self.lab.stop('s')  # foreign TAP removed externally; now absent
        self.assertFalse(self.lab.runtime)

    @patch('container_console.shutil.which', return_value='/usr/bin/docker')
    def test_console_opens_ll2sh_and_shell_without_container_restart(self, which):
        calls = []
        def run(command):
            calls.append(command)
            return 125
        self.assertEqual(container_console.supervise('switch', ll2s=True, run=run), 125)
        self.assertEqual(calls[0][:6], ['/usr/bin/docker', 'exec', '-it', 'switch', '/bin/sh', '-c'])
        self.assertIn('ll2sh;', calls[0][-1])
        self.assertIn('exec /bin/sh', calls[0][-1])
