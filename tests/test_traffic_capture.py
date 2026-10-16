"""Ethernet relay observations, bounded storage, PCAP and authenticated HTTP."""
import struct
import time
import unittest
from unittest.mock import patch

import traffic_capture as capture
import test_link_fabric
import test_web_auth

ARP = bytes.fromhex('ffffffffffff0200000000010806' '0001080006040001' '0200000000010a000001' '0000000000000a000002')
TCP = bytes.fromhex('0200000000020200000000010800' '4500002800000000400600000a0000010a000002' '04d2005000000000000000005002000000000000')


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.manager = capture.Captures()
        self.source = object()
        self.ident = self.manager.start(self.source, 'link', {'a':'A','b':'B'}, 'owner')['id']

    def record(self, frame=ARP, direction='a_to_b', outcome='forwarded'):
        self.manager.observe(self.source, 'link', frame, direction, outcome)

    def test_summary_protocols_vlans_fragments_and_malformed(self):
        self.assertEqual(capture.summarize(ARP)['protocol'], 'ARP')
        self.assertEqual(capture.summarize(ARP)['source'], '10.0.0.1')
        tcp = capture.summarize(TCP)
        self.assertEqual(tcp['protocol'], 'TCP')
        self.assertIn('1234 → 80', tcp['info'])
        tagged = TCP[:12] + bytes.fromhex('88a80064810000c8') + TCP[12:]
        summary = capture.summarize(tagged)
        self.assertEqual(summary['vlans'], [100,200])
        self.assertEqual(summary['tags'], ['VLAN','IPv4','TCP'])
        fragment = bytearray(TCP); fragment[20:22] = b'\x00\x01'
        self.assertEqual(capture.summarize(bytes(fragment))['protocol'], 'IPv4')
        for data in (b'', bytes(5), ARP[:20], TCP[:26], tagged[:18]):
            self.assertIn('malformed', capture.summarize(data)['info'])
        lldp = ARP[:12] + bytes.fromhex('88cc') + b'\x00\x00'
        self.assertEqual(capture.summarize(lldp)['protocol'], 'LLDP')
        stp = ARP[:12] + bytes.fromhex('0026424203') + bytes(35)
        self.assertEqual(capture.summarize(stp)['protocol'], 'STP')
        ipv6 = ARP[:12] + bytes.fromhex('86dd6000000000083a40') + bytes(31) + b'\x01' + bytes.fromhex('8000000000000000')
        self.assertEqual(capture.summarize(ipv6)['protocol'], 'ICMPv6')
        for proto, label in ((17,'UDP'),(1,'ICMP'),(89,'OSPF')):
            frame = bytearray(TCP); frame[23] = proto
            self.assertEqual(capture.summarize(bytes(frame))['protocol'], label)

    def test_exact_pcap_pagination_detail_and_owner(self):
        self.record(); self.record(TCP, 'b_to_a', 'blocked')
        page = self.manager.read(self.ident, 'owner')
        self.assertEqual(page['total'], 2)
        self.assertEqual(page['packets'][1]['outcome'], 'blocked')
        self.assertEqual(self.manager.read(self.ident, 'owner', 1)['packets'][0]['number'], 2)
        self.assertEqual(self.manager.packet(self.ident, 'owner', 1)['hex'], ARP.hex())
        data = self.manager.pcap(self.ident, 'owner')
        self.assertEqual(struct.unpack('<IHHIIII', data[:24]), (0xa1b2c3d4,2,4,0,0,65535,1))
        pos=24
        for frame in (ARP,TCP):
            sec, usec, length, original = struct.unpack_from('<IIII', data, pos)
            self.assertLess(usec, 1_000_000)
            self.assertEqual((length, original), (len(frame), len(frame)))
            self.assertEqual(data[pos+16:pos+16+length], frame)
            pos += 16+length
        self.assertEqual(pos, len(data))
        for operation in (lambda:self.manager.read(self.ident,'other'), lambda:self.manager.pcap(self.ident,'other'),
                          lambda:self.manager.action(self.ident,'other','close'),lambda:self.manager.packet(self.ident,'other',1)):
            with self.assertRaises(ValueError): operation()

    def test_limits_expiry_restart_and_nonblocking(self):
        with patch.object(capture, 'MAX_PACKETS', 2):
            self.record(); self.record(); self.record()
        self.assertFalse(self.manager.read(self.ident,'owner')['running'])
        self.assertEqual(self.manager.read(self.ident,'owner')['total'],2)
        ident2 = self.manager.start(self.source, 'other', {}, 'owner')['id']
        self.manager.stop_source(self.source)
        self.assertFalse(self.manager.read(ident2,'owner')['running'])
        self.manager.observe(object(), 'link', TCP, 'a_to_b','forwarded')
        self.assertEqual(self.manager.read(self.ident,'owner')['total'],2)
        self.manager.lock.acquire()
        try:
            self.record()  # This would deadlock if capture waited on HTTP readers.
        finally:
            self.manager.lock.release()
        self.assertEqual(self.manager.contention_drops, 1)
        self.manager.sessions[self.ident]['created'] -= capture.RETENTION+1
        with self.assertRaises(ValueError): self.manager.read(self.ident,'owner')
        self.manager.sessions[ident2]['running'] = True
        self.manager.sessions[ident2]['deadline'] = 0
        self.assertFalse(self.manager.read(ident2,'owner')['running'])
        self.manager.revoke('owner')
        self.assertFalse(self.manager.sessions)
        with patch.object(capture,'MAX_BYTES',1):
            ident = self.manager.start(self.source,'link',{},'owner')['id'];self.record()
            self.assertFalse(self.manager.read(ident,'owner')['running'])
            self.assertEqual(self.manager.read(ident,'owner')['total'],0)
        with patch.object(capture,'MAX_SESSIONS',1):
            with self.assertRaises(ValueError): self.manager.start(self.source,'link',{},'owner')


class RelayCaptureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_link_fabric.FabricTests();self.fixture.setUp()
        self.manager = capture.Captures()
        self.fixture.fabric.captures = self.manager
        self.ident = self.manager.start(self.fixture.fabric.capture_source,'cable',{},None)['id']

    def tearDown(self): self.fixture.tearDown()

    def test_real_relay_forward_drop_stop_and_capture_failure(self):
        f = self.fixture
        f.send(0,ARP); f.receive(1,ARP)
        f.fabric.set_blocked('cable',True,False)
        f.send(0,ARP);f.absent(1)
        f.send(1,TCP);f.receive(0,TCP)
        for _ in range(100):
            if self.manager.read(self.ident,None)['total'] == 3: break
            time.sleep(.01)
        page = self.manager.read(self.ident,None)
        self.assertEqual([p['outcome'] for p in page['packets']],['forwarded','blocked','forwarded'])
        self.assertEqual([p['direction'] for p in page['packets']],['a_to_b','a_to_b','b_to_a'])
        f.fabric.set_blocked('cable',False,False)
        f.ends[1].close()
        f.send(0,ARP)
        # Synchronize with the relay without assuming an arbitrary sleep duration.
        for _ in range(100):
            if self.manager.read(self.ident,None)['total'] == 4: break
            time.sleep(.01)
        self.assertEqual(self.manager.read(self.ident,None)['packets'][-1]['outcome'],'undelivered')
        with patch.object(self.manager,'observe',side_effect=RuntimeError('monitor failure')):
            f.send(0,TCP)
            for _ in range(100):
                if self.manager.contention_drops: break
                time.sleep(.01)
            self.assertGreater(self.manager.contention_drops,0)
        self.assertFalse(f.fabric.error)
        f.fabric.close()
        self.assertFalse(self.manager.read(self.ident,None)['running'])
        self.assertEqual(self.manager.packet(self.ident,None,1)['hex'],ARP.hex())


class CaptureHTTPTests(unittest.TestCase):
    setUp = test_web_auth.AuthTests.setUp
    tearDown = test_web_auth.AuthTests.tearDown
    node = staticmethod(test_web_auth.AuthTests.node)
    enable = test_web_auth.AuthTests.enable
    http = test_web_auth.AuthTests.http
    login = test_web_auth.AuthTests.login

    def test_roles_ownership_csrf_logout_and_stopped_lab(self):
        self.enable()
        admin, csrf = self.login()
        user, user_csrf = self.login('user')
        guest, guest_csrf = self.login('guest')
        path = '/api/links/cable/capture'
        self.assertEqual(self.http(path,'POST',{})[0],401)
        self.assertEqual(self.http(path,'POST',{},admin)[0],403)
        self.assertEqual(self.http(path,'POST',{},guest,guest_csrf)[0],403)
        self.assertEqual(self.http(path,'POST',{},admin,csrf)[0],400)
        self.lab.start('r1')
        status, _, result = self.http(path,'POST',{},user,user_csrf)
        self.assertEqual(status,200,result)
        ident=result['id']; url='/api/captures/'+ident
        self.lab.captures.observe(self.lab.fabric.capture_source,'cable',ARP,'a_to_b','forwarded')
        self.assertEqual(self.http(url,cookie=user)[2]['packets'][0]['protocol'],'ARP')
        self.assertEqual(self.http(url,cookie=admin)[0],409)
        self.assertEqual(self.http(url+'/pcap',cookie=admin)[0],409)
        self.assertEqual(self.http(url,cookie=guest)[0],403)
        self.assertEqual(self.http(url+'/stop','POST',{},user)[0],403)
        self.assertEqual(self.http(url+'/stop','POST',{},admin,csrf)[0],400)
        self.assertEqual(self.http(url+'/stop','POST',{},user,user_csrf)[0],200)
        self.assertEqual(self.http(url+'/packets/1',cookie=user)[2]['hex'],ARP.hex())
        self.assertEqual(self.http(url+'?after=-1',cookie=user)[0],409)
        self.assertEqual(self.http('/api/auth/logout','POST',{},user,user_csrf)[0],200)
        self.assertNotIn(ident,self.lab.captures.sessions)


def protocol_frames():
    """Synthetic protocol headers, independent of any running guest/capture."""
    ethernet = bytes.fromhex('01000ccccccc020000000001')
    def snap(pid, body):
        payload = bytes.fromhex('aaaa0300000c') + struct.pack('!H', pid) + body
        return ethernet + struct.pack('!H', len(payload)) + payload
    def ip(proto, body):
        return ethernet + bytes.fromhex('08004500') + struct.pack('!H',20+len(body)) + bytes.fromhex('0000000040') + bytes([proto]) + bytes.fromhex('00000a000001e0000005') + body
    return {
        'LLDP': ethernet + bytes.fromhex('88cc0207040200000000010403057031060200780000'),
        'CDP': snap(0x2000, bytes.fromhex('02b4000000010008526f7574')),
        'DTP': snap(0x2004, bytes.fromhex('010001000854535400000200058100030005a50004000a020000000001')),
        'VTP': snap(0x2003, bytes.fromhex('01010004') + b'TEST' + bytes(28) + struct.pack('!I',1) + bytes(32)),
        'STP': ethernet + bytes.fromhex('0026424203') + bytes(35),
        'PVST+': snap(0x010b, bytes(35)),
        'LACP': ethernet + bytes.fromhex('880901010114') + bytes(18) + bytes.fromhex('0214') + bytes(18) + bytes.fromhex('0310') + bytes(14) + bytes(52),
        'OSPF': ip(89, bytes.fromhex('0201002c010101010000000000000000') + bytes(8) + bytes.fromhex('ffffff00000a0201000000280000000000000000')),
        'BGP': ip(6, bytes.fromhex('04d200b300000001000000005018100000000000') + b'\xff'*16 + bytes.fromhex('001304')),
    }


class DecoderTests(unittest.TestCase):
    def test_switching_protocol_fallbacks(self):
        for name, frame in protocol_frames().items():
            if name != 'BGP':
                self.assertEqual(capture.summarize(frame)['protocol'], name)
        for pid in ('010b', '2000', '2003', '2004'):
            frame = protocol_frames()['DTP'][:12] + bytes.fromhex('810000640025aaaa0300000c'+pid) + bytes(40)
            self.assertEqual(capture.summarize(frame)['vlans'], [100])
            self.assertNotEqual(capture.summarize(frame)['protocol'], 'Other')

    def test_missing_busy_timeout_and_output_limits(self):
        import subprocess
        from types import SimpleNamespace
        with patch.object(capture.shutil,'which',return_value=None):
            self.assertEqual(capture.decode_packet(ARP,1)['status'],'unavailable')
        with patch.object(capture.shutil,'which',side_effect=lambda name:'/usr/bin/'+name):
            with patch.object(capture,'DECODE_SLOTS',capture.threading.BoundedSemaphore(0)):
                self.assertEqual(capture.decode_packet(ARP,1)['status'],'busy')
            with patch.object(capture.subprocess,'run',side_effect=subprocess.TimeoutExpired('tshark',5)):
                self.assertEqual(capture.decode_packet(ARP,1)['status'],'timeout')
            def output(args, **kwargs):
                self.assertIn('-n', args)
                self.assertIn('--cpu=3', args)
                self.assertIn('ipv6.defragment:FALSE', args)
                self.assertNotIn('shell', kwargs)
                self.assertEqual(kwargs['timeout'],5)
                self.assertEqual(kwargs['input'][-len(ARP):],ARP)
                if capture.os.geteuid() == 0:
                    self.assertEqual(kwargs['user'],65534)
                    self.assertEqual(kwargs['extra_groups'],[])
                kwargs['stdout'].write(b'x'*capture.DECODE_LIMIT)
                return SimpleNamespace(returncode=0)
            with patch.object(capture.subprocess,'run',side_effect=output):
                self.assertEqual(capture.decode_packet(ARP,1)['status'],'limited')

    def test_cache_and_revocation_during_decode(self):
        manager=capture.Captures(); source=object()
        ident=manager.start(source,'link',{},'owner')['id']
        for _ in range(18): manager.observe(source,'link',ARP,'a_to_b','forwarded')
        with patch.object(capture,'decode_packet',return_value={'status':'ok','text':'<script>packet</script>'}) as decoder:
            manager.packet(ident,'owner',1);manager.packet(ident,'owner',1)
            self.assertEqual(decoder.call_count,1)
            for i in range(2,19): manager.packet(ident,'owner',i)
            self.assertEqual(len(manager.sessions[ident]['decoded']),16)
        def revoke(*args):
            self.assertTrue(manager.lock.acquire(blocking=False))
            manager.lock.release()
            manager.revoke('owner')
            return {'status':'ok','text':'private'}
        with patch.object(capture,'decode_packet',side_effect=revoke):
            with self.assertRaises(ValueError):manager.packet(ident,'owner',1)

    @unittest.skipUnless(capture.shutil.which('tshark') and capture.shutil.which('prlimit'), 'Install TShark/util-linux or run in the core image')
    def test_real_tshark_switching_and_routing(self):
        frames=protocol_frames()
        status, rows=capture.decode_summaries([(1,'','',frame) for frame in frames.values()])
        self.assertEqual(status,'ok')
        self.assertEqual(len(rows),len(frames))
        for number,(name,frame) in enumerate(frames.items(),1):
            with self.subTest(protocol=name):
                expected='STP' if name=='PVST+' else name
                self.assertIn(expected, rows[number]['tags'])
                result=capture.decode_packet(frame,1)
                self.assertEqual(result['status'],'ok',result)
                self.assertGreater(len(result['text']),100)
        result=capture.decode_packet(ARP,1)
        self.assertIn('Address Resolution Protocol',result['text'])
        self.assertIn('10.0.0.1',result['text'])
