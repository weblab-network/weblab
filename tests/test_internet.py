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
        self.assertEqual(node['internet']['firewall'], {'default_action':'allow','rules':[]})
        self.assertFalse(lab_backup.saved_path(node, 'startup-config'))
        self.assertEqual(saved_config.export(self.lab, Mock())['nodes'][0]['internet'], node['internet'])
        self.assertEqual(console_capture.export(self.lab)['nodes'][0]['internet'], node['internet'])
        self.assertEqual(self.lab.validate(self.lab.topology), self.lab.topology)

    def test_zip_round_trip_preserves_routes_and_firewall_not_helper_files(self):
        self.topology['nodes'][0]['internet']={
            'routes':[{'destination':'10.44.0.0/24','via':'192.0.2.2'}],
            'port_forwards':[{'protocol':'tcp','external_port':22022,'target_address':'10.44.0.2','target_port':22}],
            'firewall':{'default_action':'deny','rules':[
                {'action':'allow','protocol':'tcp','destination':'203.0.113.7','destination_port':'443'},
                {'action':'deny','protocol':'any','source':'10.44.0.0/24'}]}}
        self.lab.save(self.topology)
        original=copy.deepcopy(self.lab.node('cloud')['internet'])
        self.assertEqual(saved_config.export(self.lab, Mock())['nodes'][0]['internet'], original)
        self.assertEqual(console_capture.export(self.lab)['nodes'][0]['internet'], original)
        (self.lab.node_dir('cloud')/'gateway.json').write_text('runtime only')
        result=lab_backup.create(self.lab)
        stream,_=lab_backup.take(self.lab,result['url'].rsplit('/',1)[1])
        with stream:archive=stream.read()
        self.lab.save({'name':'Blank','nodes':[],'links':[]})
        lab_backup.restore(self.lab,io.BytesIO(archive),Mock())
        self.assertEqual(self.lab.node('cloud')['internet'],original)
        self.assertFalse((self.lab.node_dir('cloud')/'gateway.json').exists())

    def test_firewall_normalization_and_rejected_settings(self):
        normalized = cloud.firewall_settings({'rules':[{
            'action':'allow','protocol':'tcp','source':'192.0.2.2',
            'destination':'203.0.113.0/24','source_port':'01024-65535','destination_port':'00443'}]})
        self.assertEqual(normalized, {'default_action':'allow','rules':[{
            'action':'allow','protocol':'tcp','source':'192.0.2.2/32',
            'destination':'203.0.113.0/24','source_port':'1024-65535','destination_port':'443'}]})
        self.assertEqual(cloud.firewall_settings(normalized), normalized)
        rule = {'action':'deny','protocol':'tcp'}
        bad_rules = [None, [], {'action':'accept','protocol':'any'}, {'action':'deny'},
                     {**rule,'destination':'203.0.113.5/24'}, {**rule,'source':'::/0'},
                     {**rule,'source':['192.0.2.2']}, {**rule,'command':'ACCEPT'},
                     {**rule,'protocol':'any','destination_port':'80'},
                     {**rule,'protocol':'icmp','source_port':'1024'}]
        for port in ('0','65536','80-79','-1','1:80','80,443','http','80\n-j ACCEPT',80,True,' 80','1-2-3'):
            bad_rules.append({**rule,'destination_port':port})
        for invalid in (None, [], {'default_action':'ACCEPT'}, {'rules':{}},
                        {'rules':[rule]*65}, {'extra':True}, *({'rules':[r]} for r in bad_rules)):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                cloud.settings({'firewall':invalid})
        self.assertEqual(len(cloud.firewall_settings({'rules':[rule]*64})['rules']),64)
        topology=copy.deepcopy(self.topology)
        topology['nodes'][0]['internet']={'firewall':{'rules':[{'action':'allow','protocol':'tcp','destination':'0.0.0.0/0\n-j ACCEPT'}]}}
        with self.assertRaisesRegex(lab_server.LabError,'Firewall rule 1'):
            self.lab.validate(topology)

    def test_firewall_order_default_and_authorized_sources(self):
        config = cloud.settings({'routes':[{'destination':'10.44.0.0/24','via':'192.0.2.2'}],
            'firewall':{'default_action':'deny','rules':[
                {'action':'allow','protocol':'tcp','source':'10.44.0.7','destination':'203.0.113.7','source_port':'1024-65535','destination_port':'443'},
                {'action':'deny','protocol':'any','destination':'203.0.113.0/24'},
                {'action':'allow','protocol':'udp','destination_port':'53'}]}})
        rules = gateway.firewall(config,'eth1','eth0').splitlines()
        self.assertIn('-A FORWARD -i eth1 -o eth0 -s 192.0.2.0/24 -j WL_EGRESS',rules)
        self.assertIn('-A FORWARD -i eth1 -o eth0 -s 10.44.0.0/24 -j WL_EGRESS',rules)
        self.assertEqual([r for r in rules if r.startswith('-A WL_EGRESS')], [
            '-A WL_EGRESS -p tcp -s 10.44.0.7/32 -d 203.0.113.7/32 --sport 1024:65535 --dport 443 -j ACCEPT',
            '-A WL_EGRESS -d 203.0.113.0/24 -j DROP',
            '-A WL_EGRESS -p udp --dport 53 -j ACCEPT',
            '-A WL_EGRESS -j DROP'])
        forward = [r for r in rules if r.startswith('-A FORWARD')]
        self.assertFalse(any('-i eth1' in r and '-j ACCEPT' in r for r in forward))
        self.assertFalse(any('-i eth1' in r and 'ESTABLISHED' in r for r in forward))
        self.assertIn('-A FORWARD -i eth0 -o eth1 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT',forward)
        self.assertIn('-A INPUT -i eth1 -s 10.44.0.0/24 -p udp --dport 53 -j ACCEPT',rules)
        self.assertIn('-A POSTROUTING -s 10.44.0.0/24 -o eth0 -j MASQUERADE',rules)
        config['firewall']={'default_action':'allow','rules':[]}
        self.assertIn('-A WL_EGRESS -j ACCEPT',gateway.firewall(config,'eth1','eth0'))

    def test_old_helper_cannot_silently_ignore_firewall(self):
        for policy in ({'default_action':'deny'}, {'rules':[{'action':'deny','protocol':'icmp'}]}):
            with self.subTest(policy=policy):
                self.topology['nodes'][0]['internet']={'firewall':policy}
                self.lab.save(self.topology)
                run=Mock(return_value=json.dumps([{'Id':'old','Config':{'Labels':{cloud.LABEL:'1'}}}]))
                with self.assertRaisesRegex(lab_server.LabError,'Rebuild.*firewall controls'):
                    cloud.start(self.lab,self.lab.node('cloud'),run,lab_server.LabError,Path('.'))
                self.assertEqual(run.call_count,1)
                self.assertFalse(self.lab.runtime)

    def test_original_policy_passes_only_compatible_settings_to_each_helper(self):
        self.lab.save(self.topology)
        for supported in (False,True):
            with self.subTest(supported=supported):
                captured={}
                def run(*args):
                    if args[:3]==('docker','image','inspect'):
                        labels={cloud.LABEL:'1', **({cloud.FIREWALL_LABEL:'1'} if supported else {})}
                        return json.dumps([{'Id':'fixture','Config':{'Labels':labels}}])
                    if args[:3]==('docker','network','inspect'):
                        return json.dumps([{'IPAM':{'Config':[{'Subnet':'172.30.0.0/24'}]}}])
                    if args[:3]==('ip','-j','link'):return '[{"ifindex":123}]'
                    if args[:2]==('docker','create'):
                        captured.update(json.loads(args[args.index('--env')+1].split('=',1)[1]))
                        raise lab_server.LabError('Captured helper configuration')
                    return ''
                self.lab.runtime['cloud']={}
                with patch.object(self.lab,'journal'), self.assertRaisesRegex(lab_server.LabError,'Captured helper'):
                    cloud.start(self.lab,self.lab.node('cloud'),run,lab_server.LabError,Path('.'))
                self.lab.runtime.clear()
                self.assertEqual('firewall' in captured,supported)
                self.assertEqual(captured['address'],'192.0.2.1/24')

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

    def test_port_forward_validation_bind_conflicts_and_old_helper_refusal(self):
        forward={'protocol':'tcp','external_port':'02222','target_address':'192.0.2.2','target_port':'22'}
        config=cloud.settings({'port_forwards':[forward]})
        self.assertEqual(config['port_forwards'],[{
            'protocol':'tcp','bind_address':'127.0.0.1','external_port':2222,
            'target_address':'192.0.2.2','target_port':22,'source':''}])
        self.assertEqual(cloud.settings(config),config)
        for invalid in (None,{},[forward]*33,[{**forward,'protocol':'icmp'}],
                [{**forward,'external_port':True}],[{**forward,'external_port':'1-10'}],
                [{**forward,'target_port':65536}],[{**forward,'target_port':0}],
                [{**forward,'target_address':'10.44.0.2'}],[{**forward,'target_address':'192.0.2.1'}],
                [{**forward,'target_address':'192.0.2.0'}],[{**forward,'target_address':'192.0.2.255'}],
                [{**forward,'target_address':3221225986}],[{**forward,'bind_address':'::1'}],
                [{**forward,'bind_address':'127.0.0.2'}],[{**forward,'source':'127.0.0.1'}],
                [{**forward,'bind_address':'0.0.0.0','source':'::/0'}],
                [{**forward,'target_port':'22\n-j ACCEPT'}],[{**forward,'shell':'echo bad'}]):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                cloud.settings({'port_forwards':invalid})
        self.assertEqual(cloud.settings({})['port_forwards'],[])
        for second in (forward,{**forward,'bind_address':'0.0.0.0'}):
            with self.assertRaisesRegex(ValueError,'conflict'):
                cloud.settings({'port_forwards':[forward,second]})
        # Distinct bind addresses and protocols may reuse one numeric host port.
        self.assertEqual(len(cloud.settings({'port_forwards':[forward,
            {**forward,'bind_address':'192.0.2.99'},{**forward,'protocol':'udp'}]})['port_forwards']),3)
        self.topology['nodes'][0]['internet']=config
        self.lab.save(self.topology)
        run=Mock(return_value=json.dumps([{'Id':'old','Config':{'Labels':{cloud.LABEL:'1',cloud.FIREWALL_LABEL:'1'}}}]))
        with self.assertRaisesRegex(lab_server.LabError,'Rebuild.*port forwarding'):
            cloud.start(self.lab,self.lab.node('cloud'),run,lab_server.LabError,Path('.'))
        self.assertEqual(run.call_count,1)
        self.assertFalse(self.lab.runtime)
        # Two Internet nodes cannot reserve overlapping host endpoints.
        other={**copy.deepcopy(self.topology['nodes'][0]),'id':'other','name':'Other'}
        with self.assertRaisesRegex(lab_server.LabError,'conflict'):
            self.lab.validate({**self.topology,'nodes':[*self.topology['nodes'],other]})
        self.lab.runtime['cloud']={'cloud':True}
        with self.assertRaisesRegex(lab_server.LabError,'Stop this Internet node'):
            self.lab.update_internet('cloud',self.internet_change(port_forwards=[]))

    def test_port_forward_publish_arguments_and_namespace_rules(self):
        self.topology['nodes'][0]['internet']={'port_forwards':[
            {'protocol':'tcp','external_port':22022,'target_address':'192.0.2.2','target_port':22},
            {'protocol':'udp','bind_address':'192.168.1.2','external_port':20161,
             'target_address':'192.0.2.3','target_port':161,'source':'198.51.100.0/24'}],
            'firewall':{'default_action':'deny','rules':[]}}
        self.lab.save(self.topology)
        captured=[]
        def run(*args,**kwargs):
            if args[:3]==('docker','image','inspect'):
                return json.dumps([{'Id':'fixture','Config':{'Labels':{
                    cloud.LABEL:'1',cloud.FIREWALL_LABEL:'1',cloud.PORT_FORWARD_LABEL:'1'}}}])
            if args[:3]==('docker','network','inspect'):
                return json.dumps([{'IPAM':{'Config':[{'Subnet':'172.30.0.0/24'}]}}])
            if args[:3]==('ip','-j','link'):return '[{"ifindex":123}]'
            if args[:2]==('docker','create'):
                captured.extend(args)
                raise lab_server.LabError('Captured publication')
            return ''
        self.lab.runtime['cloud']={}
        with patch.object(self.lab,'journal'),self.assertRaisesRegex(lab_server.LabError,'Captured publication'):
            cloud.start(self.lab,self.lab.node('cloud'),run,lab_server.LabError,Path('.'))
        self.assertEqual([captured[i+1] for i,arg in enumerate(captured) if arg=='--publish'],[
            '127.0.0.1:22022:40000/tcp','192.168.1.2:20161:40001/udp'])
        rules=gateway.firewall(self.lab.node('cloud')['internet'],'eth1','eth0','172.30.0.2','172.30.0.1').splitlines()
        self.assertIn('-A PREROUTING -i eth0 -p tcp -s 172.30.0.1/32 -d 172.30.0.2 --dport 40000 -j DNAT --to-destination 192.0.2.2:22',rules)
        self.assertIn('-A PREROUTING -i eth0 -p udp -s 198.51.100.0/24 -d 172.30.0.2 --dport 40001 -j DNAT --to-destination 192.0.2.3:161',rules)
        self.assertFalse(any(r.startswith('-A PREROUTING -i eth0 -p tcp -d ') for r in rules),'Loopback forwards must retain a helper-side source guard')
        reply=next(r for r in rules if r.startswith('-A FORWARD -i eth1') and '--ctdir REPLY' in r)
        egress=next(r for r in rules if r.startswith('-A FORWARD -i eth1') and '-j WL_EGRESS' in r)
        self.assertLess(rules.index(reply),rules.index(egress),'Explicit-forward replies must precede outbound default deny')
        self.assertIn('-A POSTROUTING -o eth1 -m conntrack --ctstate DNAT --ctdir ORIGINAL -j SNAT --to-source 192.0.2.1',rules)

    def test_gateway_update_normalizes_older_saved_topology(self):
        self.lab.save(self.topology)
        self.lab.node('cloud')['internet'].pop('port_forwards')
        data=self.internet_change(firewall={'default_action':'deny','rules':[]})
        result=self.lab.update_internet('cloud',data)
        self.assertEqual(result['topology']['nodes'][0]['internet']['port_forwards'],[])

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

    def internet_change(self, **changes):
        previous = copy.deepcopy(self.lab.node('cloud')['internet'])
        return {'expected_internet': previous, 'internet': {**copy.deepcopy(previous), **changes}}

    def test_stopped_gateway_settings_with_running_peer_and_stale_editor(self):
        self.lab.save(self.topology)
        self.lab.runtime['pc'] = {'fixture': True}
        peer = self.lab.runtime['pc']
        data = self.internet_change(address='198.51.100.1/24', routes=[
            {'destination':'10.44.0.0/24','via':'198.51.100.2'}])
        with patch('lab_server.run') as run:
            result = self.lab.update_internet('cloud', data)
        run.assert_not_called()
        self.assertEqual(result['status']['pc']['state'], 'running')
        self.assertIs(self.lab.runtime['pc'], peer)
        self.assertEqual(self.lab.node('cloud')['internet'], data['internet'])
        self.assertEqual(json.loads(self.lab.topology_path.read_text()), self.lab.topology)
        with patch('lab_server.run') as run, self.assertRaisesRegex(lab_server.LabConflict, 'settings changed'):
            self.lab.update_internet('cloud', data)
        run.assert_not_called()
        with self.assertRaisesRegex(lab_server.LabError, 'Only Internet'):
            self.lab.update_internet('pc', data)
        for invalid in ({}, {**self.internet_change(), 'name':'Changed'}):
            with self.assertRaises(lab_server.LabError):
                self.lab.update_internet('cloud', invalid)

    def test_live_firewall_persists_without_restarting_or_changing_other_settings(self):
        self.lab.save(self.topology)
        self.lab.runtime.update(cloud={'cloud':True,'cloud_container_id':'immutable'}, pc={})
        runtime = copy.deepcopy(self.lab.runtime)
        data = self.internet_change(firewall={'default_action':'deny','rules':[
            {'action':'allow','protocol':'tcp','destination_port':'443'}]})
        metadata = {'Config':{'Labels':{cloud.LABEL:f'{self.lab.owner}:cloud',cloud.LIVE_FIREWALL_LABEL:'1'}},
                    'State':{'Running':True}}
        with patch('lab_server.run', side_effect=[json.dumps([metadata]), '']) as run:
            result = self.lab.update_internet('cloud', data)
        self.assertEqual(run.call_count, 2)
        args = run.call_args.args
        self.assertEqual(args[:-1], ('docker','exec','immutable','python3','/opt/weblab/gateway.py','firewall'))
        self.assertEqual(json.loads(args[-1]), cloud.firewall_settings(data['internet']['firewall']))
        self.assertEqual(self.lab.runtime, runtime)
        self.assertEqual(result['status']['cloud']['state'], 'running')
        self.assertEqual(json.loads(self.lab.topology_path.read_text()), self.lab.topology)
        for change in ({'address':'198.51.100.1/24'}, {'uplink_subnet':'172.25.0.0/24'},
                       {'routes':[{'destination':'10.44.0.0/24','via':'192.0.2.2'}]}):
            with patch('lab_server.run') as run, self.assertRaisesRegex(lab_server.LabError, 'Stop this Internet node'):
                self.lab.update_internet('cloud', self.internet_change(**change))
            run.assert_not_called()
        with patch('lab_server.run') as run:
            self.lab.update_internet('cloud', self.internet_change())
        run.assert_not_called()

    def test_live_firewall_refuses_wrong_owner_old_helper_or_dead_container(self):
        self.lab.save(self.topology)
        self.lab.runtime['cloud'] = {'cloud':True,'cloud_container_id':'immutable'}
        original = copy.deepcopy(self.lab.topology)
        for labels, running, message in (
            ({cloud.LABEL:'other-owner'}, True, 'ownership'),
            ({cloud.LABEL:f'{self.lab.owner}:cloud'}, True, 'Rebuild.*live firewall'),
            ({cloud.LABEL:f'{self.lab.owner}:cloud',cloud.LIVE_FIREWALL_LABEL:'1'}, False, 'not running')):
            metadata = {'Config':{'Labels':labels},'State':{'Running':running}}
            with patch('lab_server.run',return_value=json.dumps([metadata])) as run:
                with self.assertRaisesRegex(lab_server.LabError, message):
                    self.lab.update_internet('cloud', self.internet_change(firewall={'default_action':'deny'}))
            self.assertEqual(run.call_count, 1, 'Preflight refusal must not attempt apply/rollback')
            self.assertEqual(self.lab.topology, original)

    def test_live_apply_and_save_failures_restore_previous_policy(self):
        self.lab.save(self.topology)
        self.lab.runtime['cloud'] = {'cloud':True,'cloud_container_id':'immutable'}
        original = copy.deepcopy(self.lab.topology)
        disk = self.lab.topology_path.read_bytes()
        revision = self.lab.workspace_revision
        previous = self.lab.node('cloud')['internet']['firewall']
        data = self.internet_change(firewall={'default_action':'deny'})
        with patch('internet_cloud.firewall_container',return_value='immutable'):
            for failure in ('apply', 'save', 'rollback'):
                with self.subTest(failure=failure):
                    effects = [lab_server.LabError('apply failed'), None] if failure=='apply' else (
                        [None, None] if failure=='save' else [lab_server.LabError('apply failed'),lab_server.LabError('rollback failed')])
                    with patch('internet_cloud.update_firewall',side_effect=effects) as update, \
                         patch('lab_server.atomic_json',side_effect=OSError('disk full')) as persist:
                        with self.assertRaisesRegex(lab_server.LabError, 'Could not restore' if failure=='rollback' else 'not saved'):
                            self.lab.update_internet('cloud', data)
                    self.assertEqual(update.call_count,2)
                    self.assertEqual(update.call_args.args,('immutable',previous,lab_server.run))
                    self.assertEqual(persist.call_count,1 if failure=='save' else 0)
                    self.assertEqual(self.lab.topology,original)
                    self.assertEqual(self.lab.topology_path.read_bytes(),disk)
                    self.assertEqual(self.lab.workspace_revision,revision)

    def test_helper_live_update_replaces_only_egress_chain(self):
        policy = {'default_action':'deny','rules':[{'action':'allow','protocol':'tcp','destination_port':'443'}]}
        with patch.object(gateway.subprocess,'run') as run:
            gateway.apply_firewall(policy)
        self.assertEqual(run.call_count,1)
        args, kwargs = run.call_args
        self.assertIn('--noflush',args[0])
        self.assertEqual(kwargs['input'].splitlines(),[
            '*filter', ':WL_EGRESS - [0:0]', '-A WL_EGRESS -p tcp --dport 443 -j ACCEPT',
            '-A WL_EGRESS -j DROP', 'COMMIT'])
        with patch.object(gateway.subprocess,'run') as run, self.assertRaises(ValueError):
            gateway.apply_firewall({'rules':[{'action':'deny','protocol':'tcp','destination_port':'443\n-j ACCEPT'}]})
        run.assert_not_called()

    def test_gateway_stop_keeps_direct_pc_running_even_when_repeated(self):
        self.lab.save(self.topology)
        self.lab.runtime.update(cloud={'cloud':True,'iol_id':self.lab.node('cloud')['iol_id']},
                                pc={'fixture':True})
        peer=self.lab.runtime['pc']
        with patch('internet_cloud.cleanup') as cleanup:
            self.lab.stop('cloud')
            self.lab.stop('cloud')
        cleanup.assert_called_once()
        self.assertNotIn('cloud',self.lab.runtime)
        self.assertIs(self.lab.runtime['pc'],peer)

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
