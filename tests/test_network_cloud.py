"""Diagram clouds must remain portable annotations, never runtime devices."""
import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import lab_server
import lab_backup
import lab_automation
import saved_config
import console_capture


class NetworkCloudTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.lab = lab_server.Lab(root/'data', root/'images')
        self.lab.netio, self.lab.netl1 = root/'netio', root/'netl1'
        self.topology = {'name':'Network diagram','nodes':[
            {'id':'wan','name':'Provider network','type':'network','size':150,'color':'#AABBCC'},
            {'id':'r1','name':'R1','type':'router','image':'quay.io/frrouting/frr:10.7.1'},
            {'id':'r2','name':'R2','type':'router','image':'quay.io/frrouting/frr:10.7.1'}],
            'links':[{'id':'a','a':{'node':'r1','port':'eth0'},'b':{'node':'wan','port':'connection'}},
                     {'id':'b','a':{'node':'r2','port':'eth0'},'b':{'node':'wan','port':'connection'}}]}

    def tearDown(self):
        self.lab.runtime.clear()
        self.lab.file_lock.close()
        lab_backup.expire(self.lab, all_files=True)
        self.tmp.cleanup()

    def test_shared_anchor_without_runtime_or_netmap_routes(self):
        self.lab.save(self.topology)
        self.assertEqual(self.lab.node('wan')['color'], '#aabbcc')
        self.assertEqual(self.lab.node('wan')['memory'], 0)
        self.assertEqual(self.lab.snapshot()['status']['wan']['state'], 'diagram')
        self.assertFalse(self.lab.link_states()['a']['available'])
        with self.assertRaisesRegex(lab_server.LabError, 'diagram-only'):
            self.lab.start('wan')
        with patch.object(self.lab, 'start') as start:
            self.lab.start_all()
            self.assertEqual([c.args[0] for c in start.call_args_list], ['r1','r2'])
        with patch('lab_server.link_fabric.LinkFabric') as fabric, patch('lab_server.iol_l1.Controller'):
            self.lab.ensure_fabric()
            self.assertEqual(fabric.call_args.args[2], [])
            self.lab.write_netmap()
            self.assertEqual((self.lab.directory/'NETMAP').read_text().strip(), '')
        self.lab.fabric = None
        with self.assertRaisesRegex(lab_server.LabError, 'Diagram-only'):
            self.lab.start_capture('a', None)
        with self.assertRaises(lab_server.LabError):
            self.lab.set_link_traffic('a', {'blocked_a_to_b':True,'blocked_b_to_a':False})
        with self.assertRaises(lab_server.LabError):
            self.lab.set_link_carrier('a', {'side':'a','up':False})
        self.assertFalse(self.lab.runtime)

    def test_validation_and_real_port_exclusivity(self):
        for changes in ({'size':True},{'size':59},{'size':201},{'color':'red'},
                        {'color':'#123456;display:none'},{'image':'alpine:latest'},
                        {'internet':{'address':'192.0.2.1/24'}},{'startup_config':'echo hi'},
                        {'ipv4':'10.0.0.1/24'},{'memory':256}):
            data=copy.deepcopy(self.topology);data['nodes'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(lab_server.LabError):
                self.lab.validate(data)
        data=copy.deepcopy(self.topology);data['links'][1]['a']['node']='r1'
        with self.assertRaisesRegex(lab_server.LabError, 'already used'):
            self.lab.validate(data)

    def test_visual_edits_while_running_but_no_structural_changes(self):
        self.lab.save(self.topology);self.lab.runtime['r1']={}
        data=copy.deepcopy(self.lab.topology)
        data['nodes'][0].update(size=200,color='#112233',name='Campus')
        self.lab.save(data)
        self.assertEqual(self.lab.node('wan')['size'],200)
        data['links'].pop()
        with self.assertRaisesRegex(lab_server.LabError,'Stop all'):
            self.lab.save(data)

    def test_diagram_zip_config_exports_and_no_device_files(self):
        self.lab.save({'name':'Diagram','nodes':[self.topology['nodes'][0]],'links':[]})
        self.assertEqual(saved_config.export(self.lab,Mock())['nodes'][0]['size'],150)
        self.assertEqual(console_capture.export(self.lab)['nodes'][0]['color'],'#aabbcc')
        self.assertFalse(lab_backup.saved_path(self.lab.node('wan'),'startup-config'))
        result=lab_backup.create(self.lab)
        stream,_=lab_backup.take(self.lab,result['url'].rsplit('/',1)[1])
        with stream: archive=stream.read()
        self.lab.save({'name':'Blank','nodes':[],'links':[]})
        lab_backup.restore(self.lab,io.BytesIO(archive),Mock())
        self.assertEqual(self.lab.node('wan')['size'],150)
        self.assertEqual(self.lab.node('wan')['color'],'#aabbcc')
        self.assertEqual(lab_automation.resources(self.lab.topology['nodes'])['configured_memory_mb_excluding_pcs'],0)

    def test_pc_needs_an_operational_connection(self):
        data=copy.deepcopy(self.topology);data['nodes'][1]={'id':'r1','name':'PC','type':'pc'}
        self.lab.save(data)
        with self.assertRaisesRegex(lab_server.LabError,'diagram-only'):
            self.lab.start('r1')
        self.assertFalse(self.lab.runtime)

    def test_agent_preview_excludes_diagram_from_start_order(self):
        api = lab_automation.Automation(self.lab, lab_server.LabError, lab_server.ports, Mock(return_value=''))
        proposal = api.preview(self.topology, 'Inspect the provider network drawing.')
        cloud_id = proposal['node_id_map']['wan']
        self.assertNotIn(cloud_id, proposal['start_order'])
        self.assertEqual(len(proposal['start_order']), 2)
        cloud = next(n for n in proposal['topology']['nodes'] if n['id'] == cloud_id)
        self.assertEqual(cloud['size'], 150)
        self.assertEqual(cloud['color'], '#aabbcc')
        profile = next(p for p in api.catalog()['profiles'] if p['family'] == 'diagram')
        self.assertEqual(profile['defaults'], {'memory': 0, 'ethernet': 0})
        self.assertFalse(profile['startup_config'])
