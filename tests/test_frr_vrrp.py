"""VRRP provisioning validates intent, respects ownership and preserves FRR state."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import frr
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'packaging/frr'))
import vrrp_backend as vr


class VrrpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = vr.Vrrp(Path(self.tmp.name)/'owners.json')
        self.group = {'interface': 'eth0', 'vrid': 10, 'autoconfigured': False,
                      'v4': {'interface': 'None', 'addresses': ['10.0.0.1']}}
        self.parent = {'ifname': 'eth0', 'ifindex': 2, 'flags': ['UP'],
                       'addr_info': [{'family': 'inet', 'local': '10.0.0.2', 'prefixlen': 24}]}
        self.child = {'ifname': 'wv0g10', 'ifindex': 3, 'link': 'eth0',
                      'address': '00:00:5e:00:01:0a', 'flags': ['UP'], 'protodown': True,
                      'linkinfo': {'info_kind': 'macvlan', 'info_data': {'mode': 'bridge'}},
                      'addr_info': [{'family': 'inet', 'local': '10.0.0.1', 'prefixlen': 24}]}
        self.links = {'eth0': self.parent, 'wv0g10': self.child}
        self.record = {'parent': 'eth0', 'vrid': 10, 'index': 3, 'parent_index': 2, 'initialized': True}

    def test_names_macs_and_prefixes(self):
        self.assertEqual(vr.identity('eth7.4094', 255), ('wv7v4094g255', '00:00:5e:00:01:ff'))
        for parent, vid in [('lo',10),('eth0;id',10),('eth0.0',10),('eth0',0),('eth0',256),('eth0',True)]:
            with self.assertRaises(ValueError):
                vr.identity(parent,vid)
        self.assertEqual(vr.addresses(self.group,self.parent), ['10.0.0.1/24'])
        for vip in ['10.0.0.2','10.0.0.0','10.0.0.255','192.0.2.1','224.0.0.1','127.0.0.1']:
            self.group['v4']['addresses']=[vip]
            with self.assertRaises(ValueError, msg=vip):
                vr.addresses(self.group,self.parent)

    def test_failed_daemon_read_is_not_removal(self):
        self.service.owners={'wv0g10':self.record}
        with patch.object(vr,'read_groups',side_effect=RuntimeError('unavailable')), patch.object(vr.vlan_backend,'ip') as ip:
            self.service.reconcile()
            ip.assert_not_called()
        self.assertIn('unavailable',self.service.show())
        self.assertIn('wv0g10',self.service.owners)

    def test_incomplete_or_duplicate_daemon_state_is_rejected(self):
        for groups in ([self.group, {}], [self.group, self.group], [{'interface': 'eth0', 'vrid': 10}]):
            with patch.object(vr.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(groups))):
                with self.assertRaises(ValueError):
                    vr.read_groups()

    def test_existing_backup_does_not_reset_protodown_or_addresses(self):
        self.service.owners={'wv0g10':self.record}
        self.service.save()
        recovered=vr.Vrrp(self.service.path)
        with patch.object(vr,'read_groups',return_value=[self.group]), \
                patch.object(recovered,'snapshot',return_value=self.links), patch.object(vr.vlan_backend,'ip') as ip:
            recovered.apply()
            ip.assert_not_called()
            self.assertIn('ready',recovered.show())

    def test_foreign_or_replaced_links_are_never_deleted(self):
        for owners, index in [({},3),({'wv0g10':self.record},4)]:
            links=copy.deepcopy(self.links);links['wv0g10']['ifindex']=index
            self.service.owners=owners
            with patch.object(vr,'read_groups',return_value=[self.group]), \
                    patch.object(self.service,'snapshot',return_value=links), patch.object(vr.vlan_backend,'ip') as ip:
                self.service.apply()
                ip.assert_not_called()
                self.assertNotIn('ready',self.service.show())

    def test_removed_group_cleans_only_recorded_interface(self):
        self.service.owners={'wv0g10':self.record}
        with patch.object(vr,'read_groups',return_value=[]), \
                patch.object(self.service,'snapshot',return_value=self.links), patch.object(vr.vlan_backend,'ip') as ip:
            self.service.apply()
            ip.assert_called_once_with('link','delete','dev','wv0g10')
        self.assertFalse(self.service.owners)

    def test_prefix_removed_withdraws_owned_interface(self):
        self.service.owners={'wv0g10':self.record}
        self.parent['addr_info']=[]
        with patch.object(vr,'read_groups',return_value=[self.group]), \
                patch.object(self.service,'snapshot',return_value=self.links), patch.object(vr.vlan_backend,'ip') as ip:
            self.service.apply()
            ip.assert_called_once_with('link','delete','dev','wv0g10')
        self.assertIn('configure a different real',self.service.show())

    def test_image_identity(self):
        labels={'network.weblab.frr.extension':'lldp-1','network.weblab.frr.snmp':'v2c-1','network.weblab.frr.vlan':'8021q-1'}
        image={'Id':'sha256:'+'c'*64,'Config':{'Labels':labels}}
        run=lambda *args:json.dumps([image])
        with self.assertRaisesRegex(ValueError,'VRRP image'):
            frr.inspect_image(run,ValueError,True,frr.VRRP_IMAGE)
        labels['network.weblab.frr.vrrp']='ipv4-1'
        self.assertEqual(frr.archive_image(run,ValueError,True,frr.VRRP_IMAGE)['digest'],image['Id'])


if __name__ == '__main__':
    unittest.main()
