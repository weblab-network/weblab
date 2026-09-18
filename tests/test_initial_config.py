"""Fresh-node initialization and stopped-PC-only editing exceptions."""
import copy
import io
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

import lab_backup
import lab_server
import test_lab
import vios


class InitialConfigTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_lab.LabTests()
        self.fixture.setUp()
        self.lab = self.fixture.lab

    def tearDown(self):
        lab_backup.expire(self.lab, all_files=True)
        self.fixture.tearDown()

    def test_only_stopped_pc_addresses_editable_while_router_runs(self):
        topology = copy.deepcopy(self.lab.topology)
        topology['nodes'].append({'id':'pc1','name':'PC1','type':'pc','image':'alpine:latest'})
        topology['links'].append({'id':'pc-link','a':{'node':'r1','port':'0/1'},'b':{'node':'pc1','port':'eth0'}})
        self.lab.save(topology)
        self.lab.start('r1')
        before = copy.deepcopy(self.lab.runtime)
        topology = copy.deepcopy(self.lab.topology)
        pc = topology['nodes'][-1]
        pc.update(ipv4='192.0.2.10/24',gateway='192.0.2.1')
        self.lab.save(topology)
        self.assertEqual(self.lab.node('pc1')['ipv4'],'192.0.2.10/24')
        self.assertEqual(self.lab.runtime,before)
        for field,value in [('image','alpine:3.20'),('memory',2048),('ethernet',3)]:
            bad=copy.deepcopy(topology);bad['nodes'][-1][field]=value
            with self.subTest(field=field), self.assertRaises(lab_server.LabError): self.lab.save(bad)
        bad=copy.deepcopy(topology);bad['links']=[]
        with self.assertRaises(lab_server.LabError): self.lab.save(bad)
        bad=copy.deepcopy(topology);bad['nodes'][-1]['gateway']='198.51.100.1'
        with self.assertRaises(lab_server.LabError): self.lab.save(bad)
        self.lab.runtime['pc1']={}
        try:
            bad=copy.deepcopy(topology);bad['nodes'][-1]['ipv4']='192.0.2.11/24'
            with self.assertRaises(lab_server.LabError): self.lab.save(bad)
        finally: del self.lab.runtime['pc1']

    def test_snippet_validation_and_round_trip(self):
        topology=copy.deepcopy(self.lab.topology)
        topology['nodes'][0]['startup_config']='hostname Seed\r\nend\r\n'
        result=self.lab.save(topology)
        self.assertEqual(result['topology']['nodes'][0]['startup_config'],'hostname Seed\nend\n')
        for invalid in [None,[],123,'x'*16385,'x'*16384,'é'*8192,'hostname X\x00','hostname X\x1b']:
            bad=copy.deepcopy(topology);bad['nodes'][0]['startup_config']=invalid
            with self.subTest(invalid=repr(invalid)[:30]), self.assertRaises(lab_server.LabError): self.lab.save(bad)
        bad=copy.deepcopy(topology);bad['nodes'][0].update(type='pc',image='alpine:latest')
        with self.assertRaisesRegex(lab_server.LabError,'PCs use'): self.lab.save(bad)

    def test_iol_seed_first_start_and_archive_nvram_precedence(self):
        topology=copy.deepcopy(self.lab.topology);topology['nodes'][0]['startup_config']='hostname First\nend\n'
        self.lab.save(topology);self.lab.start('r1')
        ws=test_lab.WebSocket(self.fixture.port,'r1')
        try: ws.until(b'BOOT READY')
        finally: ws.close()
        self.lab.stop_all()
        nvram=self.lab.node_dir('r1')/f"nvram_{self.lab.node('r1')['iol_id']:05d}"
        self.assertIn(b'hostname First\nend\n', nvram.read_bytes())
        changed=copy.deepcopy(self.lab.topology);changed['nodes'][0]['startup_config']='hostname MustNotOverride\nend\n';self.lab.save(changed)
        prepared=lab_backup.create(self.lab);archive,_=lab_backup.take(self.lab,prepared['url'].rsplit('/',1)[1])
        with archive: lab_backup.restore(self.lab,archive,lab_server.run)
        self.lab.start('r1')
        self.assertIn(b'hostname First\nend\n', nvram.read_bytes())
        command=Path(f"/proc/{self.lab.runtime['r1']['console']['pid']}/cmdline").read_bytes().split(b'\x00')
        self.assertNotIn(b'-c',command)

    @unittest.skipUnless(shutil.which('mformat') and shutil.which('mcopy'),'mtools required')
    def test_iosv_seed_contents_and_command_attachment(self):
        node={'image':'vios.qcow2','startup_config':'hostname Seed\nend\n','id':'v','memory':1024,'ethernet':1}
        cwd=self.lab.node_dir('v')
        seed=vios.config_disk(node,cwd,lab_server.run,lab_server.LabError)
        content=lab_server.run('mtype','-i',str(seed)+'@@32256','::ios_config.txt')
        self.assertEqual(content.strip(),node['startup_config'].strip())
        import hashlib
        checksum=lab_server.run('mtype','-i',str(seed)+'@@32256','::ios_config_checksum')
        self.assertEqual(checksum,hashlib.md5(node['startup_config'].encode()).hexdigest())
        args=vios.command(node,cwd/'vios.qcow2',cwd/'sockets',seed)
        self.assertIn(f'file={seed},format=raw,if=virtio',args)
        self.assertNotIn(f'file={seed},format=raw,if=virtio',vios.command(node,cwd/'vios.qcow2',cwd/'sockets'))

    def test_validator_cli_is_read_only_and_checks_links(self):
        root=Path(lab_server.__file__).parent
        result=subprocess.run(['python3',str(root/'tools/validate_topology.py'),str(root/'examples/ospf-practice.json')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        path=self.fixture.root/'invalid.json'
        topology=copy.deepcopy(self.lab.topology);topology['links'][0]['a']['port']='9/9';path.write_text(json.dumps(topology))
        result=subprocess.run(['python3',str(root/'tools/validate_topology.py'),str(path)],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('interface',result.stderr)


if __name__=='__main__': unittest.main()
