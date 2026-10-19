"""Diagram groups persist as annotations, never as runnable devices."""
import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import lab_server
import lab_automation
import lab_backup
import saved_config
import console_capture


class DiagramGroupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.lab = lab_server.Lab(root/'data', root/'images')
        self.group = {'id':'core', 'name':'Core', 'type':'group', 'x':500, 'y':350,
                      'width':600, 'height':400, 'notes':'AS 65000\nOSPF area 0',
                      'color':'#AABBCC', 'label_position':'right', 'move_contents':True}
        self.topology = {'name':'Diagram', 'nodes':[self.group], 'links':[]}

    def tearDown(self):
        self.lab.runtime.clear()
        lab_backup.expire(self.lab, all_files=True)
        self.lab.file_lock.close()
        self.tmp.cleanup()

    def test_no_ports_runtime_or_resources(self):
        self.lab.save(self.topology)
        group = self.lab.node('core')
        self.assertEqual(lab_server.ports(group), [])
        self.assertEqual(group['color'], '#aabbcc')
        self.assertEqual((group['memory'],group['ethernet']), (0,0))
        self.assertEqual(self.lab.snapshot()['status']['core']['state'], 'diagram')
        with self.assertRaisesRegex(lab_server.LabError, 'diagram-only'):
            self.lab.start('core')
        with patch.object(self.lab,'start') as start:
            self.lab.start_all()
            start.assert_not_called()
        data=copy.deepcopy(self.topology)
        data['nodes'].append({'id':'wan','type':'network','name':'WAN'})
        data['links']=[{'id':'bad','a':{'node':'core','port':'connection'},'b':{'node':'wan','port':'connection'}}]
        with self.assertRaisesRegex(lab_server.LabError,'unknown interface'):
            self.lab.validate(data)

    def test_validation_and_bounds(self):
        for change in ({'width':159},{'height':1201},{'width':True},{'notes':[]},
                       {'notes':'x'*2001},{'notes':'a\x00b'},{'move_contents':1},
                       {'label_position':'left'},{'color':'red'},{'image':'alpine:latest'},
                       {'startup_config':'hostname Core'},{'ipv4':'192.0.2.1/24'}):
            data=copy.deepcopy(self.topology);data['nodes'][0].update(change)
            with self.subTest(change=change),self.assertRaises(lab_server.LabError):
                self.lab.validate(data)
        data=copy.deepcopy(self.topology);data['nodes'][0].update(x=70,y=60,width=1600,height=1200)
        group=self.lab.validate(data)['nodes'][0]
        self.assertEqual((group['x'],group['y']),(800,600))
        data['nodes'][0].update(x=2330,y=1540)
        group=self.lab.validate(data)['nodes'][0]
        self.assertEqual((group['x'],group['y']),(1600,1000))

    def test_style_edits_while_running_and_delete_keeps_devices(self):
        self.topology['nodes'].append({'id':'r1','name':'R1','type':'router','image':'quay.io/frrouting/frr:10.7.1'})
        self.lab.save(self.topology)
        self.lab.runtime['r1']={}
        changed=copy.deepcopy(self.lab.topology)
        changed['nodes'][0].update(width=700,height=450,notes='MPLS',color='#112233',label_position='above',move_contents=False)
        self.lab.save(changed)
        self.assertEqual(self.lab.node('core')['notes'],'MPLS')
        changed['nodes']=changed['nodes'][1:]
        with self.assertRaisesRegex(lab_server.LabError,'Stop all'):
            self.lab.save(changed)
        self.lab.runtime.clear();self.lab.save(changed)
        self.assertEqual([n['id'] for n in self.lab.topology['nodes']],['r1'])

    def test_archive_and_agent_roundtrip(self):
        self.lab.save(self.topology)
        expected=copy.deepcopy(self.lab.node('core'))
        self.assertEqual(saved_config.export(self.lab,Mock())['nodes'][0],expected)
        self.assertEqual(console_capture.export(self.lab)['nodes'][0],expected)
        self.assertFalse(lab_backup.saved_path(expected,'startup-config'))
        result=lab_backup.create(self.lab)
        stream,_=lab_backup.take(self.lab,result['url'].rsplit('/',1)[1])
        with stream: archive=stream.read()
        self.lab.save({'name':'Empty','nodes':[],'links':[]})
        lab_backup.restore(self.lab,io.BytesIO(archive),Mock())
        self.assertEqual(self.lab.node('core'),expected)
        self.lab.save({'name':'Empty','nodes':[],'links':[]})
        api=lab_automation.Automation(self.lab,lab_server.LabError,lab_server.ports,Mock(return_value=''))
        proposal=api.preview(self.topology,'Inspect Core notes.')
        self.assertEqual(proposal['start_order'],[])
        self.assertEqual(proposal['topology']['nodes'][0]['notes'],expected['notes'])
        self.assertEqual(proposal['resources']['configured_memory_mb_excluding_pcs'],0)
        profile=next(p for p in api.catalog()['profiles'] if p['type']=='group')
        self.assertEqual(profile['ports_at_default'],[])
