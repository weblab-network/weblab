"""Internet cloud validation, persisted settings, and owned-resource cleanup."""
import copy
import importlib.util
import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import internet_cloud as cloud
import lab_server
import lab_backup
import saved_config
import console_capture

spec = importlib.util.spec_from_file_location('gateway', Path(__file__).resolve().parents[1]/'packaging/internet/gateway.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class InternetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.lab = lab_server.Lab(root/'data', root/'images')
        self.lab.netio = root/'netio'
        self.lab.netl1 = root/'netl1'
        self.topology = {'name':'Internet test', 'nodes':[
            {'id':'cloud', 'name':'Internet', 'type':'cloud'},
            {'id':'pc', 'name':'PC', 'type':'pc'}],
            'links':[{'id':'wire', 'a':{'node':'cloud','port':'eth0'}, 'b':{'node':'pc','port':'eth0'}}]}

    def tearDown(self):
        self.lab.runtime.clear()
        if self.lab.fabric:self.lab.fabric.close()
        lab_backup.expire(self.lab, all_files=True)
        self.lab.file_lock.close()
        self.tmp.cleanup()

    def test_cloud_defaults_and_exports(self):
        self.lab.save(self.topology)
        node = self.lab.node('cloud')
        self.assertEqual(lab_server.ports(node), ['eth0'])
        self.assertEqual(lab_server.netmap_port(node, 'eth0'), '0/0')
        self.assertEqual(cloud.pc_address(node), ('192.0.2.2/24', '192.0.2.1'))
        self.assertFalse(lab_backup.saved_path(node, 'startup-config'))
        self.assertEqual(saved_config.export(self.lab, Mock())['nodes'][0]['internet'], node['internet'])
        self.assertEqual(console_capture.export(self.lab)['nodes'][0]['internet'], node['internet'])
        self.assertEqual(self.lab.validate(self.lab.topology), self.lab.topology)

    def test_zip_round_trip_preserves_routes_not_helper_files(self):
        self.topology['nodes'][0]['internet']={'routes':[{'destination':'10.44.0.0/24','via':'192.0.2.2'}]}
        self.lab.save(self.topology)
        original=copy.deepcopy(self.lab.node('cloud')['internet'])
        (self.lab.node_dir('cloud')/'gateway.json').write_text('runtime only')
        result=lab_backup.create(self.lab)
        stream,_=lab_backup.take(self.lab,result['url'].rsplit('/',1)[1])
        with stream:archive=stream.read()
        self.lab.save({'name':'Blank','nodes':[],'links':[]})
        lab_backup.restore(self.lab,io.BytesIO(archive),Mock())
        self.assertEqual(self.lab.node('cloud')['internet'],original)
        self.assertFalse((self.lab.node_dir('cloud')/'gateway.json').exists())

    def test_invalid_routes_and_images(self):
        invalid = [
            {'address':'192.0.2.0/24'}, {'address':'127.0.0.1/24'}, {'address':'192.0.2.1/32'},
            {'address':'not an IP'}, {'routes':'not a list'}, {'shell':'echo invalid'},
            {'routes':[{'destination':'0.0.0.0/0','via':'192.0.2.2'}]},
            {'routes':[{'destination':'10.0.0.0/24','via':'198.51.100.1'}]},
            {'routes':[{'destination':'10.0.0.0/24','via':'192.0.2.1'}]},
            {'routes':[{'destination':'192.0.2.0/25','via':'192.0.2.2'}]},
            {'uplink_subnet':'192.0.2.0/24'},
        ]
        for config in invalid:
            with self.subTest(config=config), self.assertRaises((ValueError, TypeError)):
                cloud.settings(config)
        for extra in ({'image':'alpine:latest'}, {'startup_config':'foo'}, {'ipv4':'1.2.3.4/24'}, {'ethernet':2}):
            topology=copy.deepcopy(self.topology);topology['nodes'][0].update(extra)
            with self.subTest(extra=extra), self.assertRaises(lab_server.LabError):self.lab.validate(topology)

    def test_return_routes_nat_and_firewall(self):
        config=cloud.settings({'routes':[{'destination':'10.44.0.0/16','via':'192.0.2.2'}]})
        rules=gateway.firewall(config,'eth1','eth0')
        self.assertIn(':FORWARD DROP',rules)
        self.assertIn('-i eth0 -o eth1 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT',rules)
        self.assertIn('-A POSTROUTING -s 10.44.0.0/16 -o eth0 -j MASQUERADE',rules)
        self.assertIn('-A INPUT -i eth1 -s 10.44.0.0/16 -p udp --dport 53 -j ACCEPT',rules)
        self.assertNotIn('-A FORWARD -i eth0 -o eth1 -j ACCEPT',rules)
        self.lab.save(self.topology)
        self.lab.runtime['cloud']={}
        changed=copy.deepcopy(self.lab.topology);changed['nodes'][0]['internet']=config
        with self.assertRaisesRegex(lab_server.LabError,'Stop all'):self.lab.save(changed)

    def test_cloud_starts_before_pc_even_if_order_reversed(self):
        self.topology['nodes'].reverse();self.lab.save(self.topology)
        with patch.object(self.lab,'start') as start:
            self.lab.start_all()
        self.assertEqual([c.args[0] for c in start.call_args_list],['cloud','pc'])

    def test_cleanup_ownership_and_partial_retry(self):
        runtime={'cloud_container_created':True,'cloud_container_id':'abc','cloud_label':'owner:cloud'}
        run=Mock(return_value=json.dumps([{'Config':{'Labels':{cloud.LABEL:'different'}}}]))
        with self.assertRaisesRegex(lab_server.LabError,'ownership'):
            cloud.cleanup(self.lab,runtime,run,lab_server.LabError)
        self.assertTrue(runtime['cloud_container_created'])
        self.assertEqual(run.call_count,1)
        run=Mock(side_effect=[json.dumps([{'Config':{'Labels':{cloud.LABEL:'owner:cloud'}}}]),''])
        cloud.cleanup(self.lab,runtime,run,lab_server.LabError)
        self.assertNotIn('cloud_container_created',runtime)
        run.assert_any_call('docker','rm','-f','abc')

    def test_start_failure_cleans_journaled_resources(self):
        self.lab.save(self.topology)
        def fail(lab,node,*args):
            lab.runtime[node['id']]['cloud']=True
            raise lab_server.LabError('fixture launch failure')
        with patch('internet_cloud.start',side_effect=fail), patch('internet_cloud.cleanup') as cleanup:
            with self.assertRaisesRegex(lab_server.LabError,'fixture launch failure'):
                self.lab.start('cloud')
        cleanup.assert_called_once()
        self.assertFalse(self.lab.runtime)
        self.assertIsNone(self.lab.fabric)
