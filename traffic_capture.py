"""Bounded, in-memory Ethernet observations; decoding never runs in the relay.

Capture is opt-in. PCAP contains original Ethernet bytes, without the IOL envelope.
No guest commands or host-interface sniffing. Batches and selected packets are
decoded by bounded offline TShark processes outside the forwarding path.
"""
import ipaddress
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import uuid

MAX_SESSIONS = 4
MAX_PACKETS = 2000
MAX_BYTES = 8 * 1024 * 1024
DURATION = 60
RETENTION = 15 * 60
PAGE_SIZE = 100
PROTOCOLS = ('All', 'ARP', 'IPv4', 'IPv6', 'ICMP', 'ICMPv6', 'TCP', 'UDP', 'OSPF', 'LLDP', 'STP', 'PVST+', 'CDP', 'DTP', 'VTP', 'LACP', 'VLAN', 'Other')
DECODE_LIMIT = 256 * 1024
DECODE_SLOTS = threading.BoundedSemaphore(2)


def pcap_bytes(packets):
    chunks = [struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1)]
    for stamp, _, _, frame in packets:
        seconds = int(stamp)
        chunks.append(struct.pack('<IIII', seconds, int((stamp-seconds)*1_000_000), len(frame), len(frame)))
        chunks.append(frame)
    return b''.join(chunks)


def run_decoder(packets, arguments):
    """Bounded offline dissection: no interface, shell or user-supplied options."""
    tshark, prlimit = shutil.which('tshark'), shutil.which('prlimit')
    if not tshark or not prlimit:
        return {'status': 'unavailable', 'text': 'Protocol decoding needs TShark and util-linux on the server. Hex/ASCII and PCAP remain available.'}
    if not DECODE_SLOTS.acquire(blocking=False):
        return {'status': 'busy', 'text': 'Decoder busy. Select this packet again to retry.'}
    try:
        data = pcap_bytes(packets)
        # File-size limit bounds output even for pathological dissectors. The
        # anonymous temporary file is closed on every path, never retained.
        with tempfile.TemporaryFile() as output:
            privileges = dict(user=65534, group=65534, extra_groups=[]) if os.geteuid() == 0 else {}
            result = subprocess.run([prlimit, '--as=536870912', '--cpu=3',
                f'--fsize={DECODE_LIMIT}', '--core=0', '--', tshark, '-n', '-r', '-', '-c', str(len(packets)),
                '-o', 'tcp.desegment_tcp_streams:FALSE', '-o', 'ip.defragment:FALSE',
                '-o', 'ipv6.defragment:FALSE', *arguments],
                input=data, stdout=output, stderr=subprocess.DEVNULL, timeout=5,
                cwd='/', env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent',
                             'XDG_CONFIG_HOME': '/nonexistent', 'LANG': 'C.UTF-8'}, **privileges)
            output.seek(0)
            raw = output.read(DECODE_LIMIT)
        text = ''.join(c for c in raw.decode('utf-8', 'replace') if c >= ' ' or c in '\n\t')
        if len(raw) >= DECODE_LIMIT:
            return {'status': 'limited', 'text': text + '\n[Decode output limit reached]'}
        if result.returncode or not text.strip():
            return {'status': 'error', 'text': 'This frame could not be decoded. Inspect Hex/ASCII or download the PCAP.'}
        return {'status': 'ok', 'text': text}
    except subprocess.TimeoutExpired:
        return {'status': 'timeout', 'text': 'Decoding exceeded the 5-second limit. Hex/ASCII and PCAP remain available.'}
    except OSError:
        return {'status': 'error', 'text': 'Decoder unavailable. Hex/ASCII and PCAP remain available.'}
    finally:
        DECODE_SLOTS.release()


def decode_packet(frame, stamp):
    return run_decoder([(stamp, '', '', frame)], ['-V'])


def decode_summaries(packets):
    fields = ['frame.number', '_ws.col.Protocol', '_ws.col.Info', 'frame.protocols',
              '_ws.col.Source', '_ws.col.Destination']
    args = ['-T', 'fields', '-E', 'occurrence=f']
    for field in fields:
        args.extend(['-e', field])
    result = run_decoder(packets, args)
    summaries = {}
    if result['status'] == 'ok':
        for line in result['text'].splitlines():
            values = line.split('\t')
            if len(values) != len(fields) or not values[0].isdigit():
                continue
            number = int(values[0])
            if not 1 <= number <= len(packets):
                continue
            protocol, info, layers, source, destination = (v[:512] for v in values[1:])
            tags = [v.upper() for v in layers.split(':') if v and not v.startswith('_')][:32]
            tags = [{'IP': 'IPv4', 'IPV6': 'IPv6', 'ICMPV6': 'ICMPv6'}.get(v, v) for v in tags]
            if protocol:
                summaries[number] = dict(protocol=protocol[:64], info=info, source=source,
                                         destination=destination, tags=tags + [protocol[:64]])
    return result['status'], summaries


def summarize(frame):
    """Small header summary, not a full dissector. Unknown/truncated bytes survive."""
    result = {'source': '', 'destination': '', 'protocol': 'Other', 'tags': [], 'info': '', 'vlans': []}
    def label(name):
        result['protocol'] = name
        result['tags'].append(name)
    try:
        if len(frame) < 14:
            raise ValueError('Short Ethernet header')
        result['source'] = ':'.join(f'{v:02x}' for v in frame[6:12])
        result['destination'] = ':'.join(f'{v:02x}' for v in frame[:6])
        kind = struct.unpack_from('!H', frame, 12)[0]
        pos = 14
        while kind in (0x8100, 0x88a8, 0x9100) and len(result['vlans']) < 4:
            tag, kind = struct.unpack_from('!HH', frame, pos)
            result['vlans'].append(tag & 0xfff)
            pos += 4
        if result['vlans']:
            result['tags'].append('VLAN')
        if kind == 0x88cc:
            label('LLDP')
            result['info'] = 'Link Layer Discovery Protocol'
        elif kind <= 1500 and frame[pos:pos + 3] == b'\x42\x42\x03':
            label('STP')
            result['info'] = 'Spanning-tree BPDU'
        elif kind <= 1500 and frame[pos:pos + 3] == b'\xaa\xaa\x03':
            oui = frame[pos+3:pos+6]
            pid = struct.unpack_from('!H', frame, pos+6)[0]
            protocol = {0x010b: ('PVST+', 'Per-VLAN spanning-tree BPDU'),
                        0x2000: ('CDP', 'Cisco Discovery Protocol'),
                        0x2004: ('DTP', 'Dynamic Trunking Protocol'),
                        0x2003: ('VTP', 'VLAN Trunking Protocol')}.get(pid) if oui == b'\x00\x00\x0c' else None
            if protocol:
                label(protocol[0]); result['info'] = protocol[1]
                if protocol[0] == 'PVST+': result['tags'].append('STP')
            else:
                result['info'] = f'LLC/SNAP OUI {oui.hex(":")}, protocol 0x{pid:04x}'
        elif kind == 0x8809 and frame[pos] == 1:
            label('LACP')
            result['info'] = 'Link Aggregation Control Protocol'
        elif kind == 0x0806:
            label('ARP')
            hardware, protocol, hlen, plen, op = struct.unpack_from('!HHBBH', frame, pos)
            result['info'] = f'Operation {op}'
            if hardware == 1 and protocol == 0x0800 and hlen == 6 and plen == 4:
                result['source'] = str(ipaddress.IPv4Address(frame[pos+14:pos+18]))
                result['destination'] = str(ipaddress.IPv4Address(frame[pos+24:pos+28]))
                if op == 1:
                    result['info'] = 'Who has ' + result['destination'] + '? Tell ' + result['source']
                elif op == 2:
                    result['info'] = result['source'] + ' is at ' + ':'.join(f'{v:02x}' for v in frame[pos+8:pos+14])
        elif kind in (0x0800, 0x86dd):
            if kind == 0x0800:
                label('IPv4')
                ihl = (frame[pos] & 15) * 4
                length = struct.unpack_from('!H', frame, pos+2)[0]
                if frame[pos] >> 4 != 4 or ihl < 20 or length < ihl or len(frame) < pos + ihl:
                    raise ValueError('Invalid IPv4 header')
                result['source'] = str(ipaddress.IPv4Address(frame[pos+12:pos+16]))
                result['destination'] = str(ipaddress.IPv4Address(frame[pos+16:pos+20]))
                proto = frame[pos+9]
                fragment = struct.unpack_from('!H', frame, pos+6)[0] & 0x1fff
                end = min(len(frame), pos + length)
                pos += ihl
                if fragment:
                    result['info'] = 'IPv4 fragment (non-initial)'
                    return result
            else:
                label('IPv6')
                if frame[pos] >> 4 != 6 or len(frame) < pos + 40:
                    raise ValueError('Invalid IPv6 header')
                result['source'] = str(ipaddress.IPv6Address(frame[pos+8:pos+24]))
                result['destination'] = str(ipaddress.IPv6Address(frame[pos+24:pos+40]))
                proto = frame[pos+6]
                end = min(len(frame), pos + 40 + struct.unpack_from('!H', frame, pos+4)[0])
                pos += 40
                # Extension headers are intentionally left for a full dissector.
                if proto in (0, 43, 44, 50, 51, 60):
                    result['info'] = f'IPv6 extension header {proto}; inspect PCAP for details'
                    return result
            if proto in (6, 17):
                label('TCP' if proto == 6 else 'UDP')
                minimum = 20 if proto == 6 else 8
                if pos + minimum > end:
                    raise ValueError('Short transport header')
                source, dest = struct.unpack_from('!HH', frame, pos)
                result['info'] = f'{source} → {dest}'
                if proto == 6:
                    flags = frame[pos+13]
                    result['info'] += ' [' + ', '.join(name for bit, name in ((1,'FIN'),(2,'SYN'),(4,'RST'),(8,'PSH'),(16,'ACK'),(32,'URG')) if flags & bit) + ']'
            elif proto in (1, 58):
                label('ICMP' if proto == 1 else 'ICMPv6')
                if pos + 4 > end:
                    raise ValueError('Short ICMP header')
                result['info'] = f'Type {frame[pos]}, code {frame[pos+1]}'
            elif proto == 89:
                label('OSPF')
                if pos + 2 > end:
                    raise ValueError('Short OSPF header')
                result['info'] = f'OSPFv{frame[pos]}, type {frame[pos+1]}'
            else:
                result['info'] = f'IP protocol {proto}'
        else:
            result['info'] = f'IEEE 802.3 length {kind}' if kind <= 1500 else f'Ethernet type 0x{kind:04x}'
    except (ValueError, IndexError, struct.error):
        result['info'] = 'Truncated or malformed header; inspect raw bytes'
    return result


class Captures:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions = {}
        self.active = False
        self.contention_drops = 0

    def _expire(self):
        now = time.monotonic()
        for ident, session in list(self.sessions.items()):
            if now - session['created'] >= RETENTION:
                del self.sessions[ident]
            elif session['running'] and now >= session['deadline']:
                session.update(running=False, reason='60-second limit reached')
        self.active = any(s['running'] for s in self.sessions.values())

    def expire(self):
        with self.lock:
            self._expire()

    def start(self, source, link_id, endpoints, owner):
        with self.lock:
            self._expire()
            if len(self.sessions) >= MAX_SESSIONS:
                raise ValueError('Capture limit reached. Close an existing traffic monitor and try again.')
            ident = uuid.uuid4().hex
            self.sessions[ident] = dict(id=ident, source=source, link_id=link_id, endpoints=endpoints,
                owner=owner, created=time.monotonic(), deadline=time.monotonic()+DURATION,
                started=time.time(), running=True, reason='', packets=[], bytes=0,
                initial_drops=self.contention_drops, decoded={}, summaries={})
            self.active = True
            return {'id': ident}

    def observe(self, source, link_id, frame, direction, outcome):
        # Never wait on a browser request from the forwarding thread.
        if not self.lock.acquire(blocking=False):
            self.contention_drops += 1
            return
        try:
            self._expire()
            for session in self.sessions.values():
                if not session['running'] or session['source'] is not source or session['link_id'] != link_id:
                    continue
                if len(session['packets']) >= MAX_PACKETS or session['bytes'] + len(frame) > MAX_BYTES:
                    session.update(running=False, reason='Capture size limit reached')
                    continue
                session['packets'].append((time.time(), direction, outcome, frame))
                session['bytes'] += len(frame)
                if len(session['packets']) == MAX_PACKETS or session['bytes'] == MAX_BYTES:
                    session.update(running=False, reason='Capture size limit reached')
            self.active = any(s['running'] for s in self.sessions.values())
        finally:
            self.lock.release()

    def stop_source(self, source, reason='Lab link relay stopped'):
        with self.lock:
            for session in self.sessions.values():
                if session['source'] is source and session['running']:
                    session.update(running=False, reason=reason)
            self._expire()

    def _owned(self, ident, owner):
        self._expire()
        session = self.sessions.get(ident)
        if not session or session['owner'] != owner:
            raise ValueError('Capture unavailable or expired')
        return session

    def action(self, ident, owner, action):
        with self.lock:
            session = self._owned(ident, owner)
            if action == 'close':
                del self.sessions[ident]
            elif action == 'stop':
                session.update(running=False, reason=session['reason'] or 'Stopped by user')
            else:
                raise ValueError('Unknown capture action')
            self._expire()
        return {'ok': True}

    def revoke(self, owner):
        with self.lock:
            self.sessions = {k: s for k, s in self.sessions.items() if s['owner'] != owner}
            self._expire()

    def read(self, ident, owner, after=0):
        if type(after) is not int or after < 0 or after > MAX_PACKETS:
            raise ValueError('Invalid packet cursor')
        with self.lock:
            session = self._owned(ident, owner)
            packets = list(session['packets'][after:after+PAGE_SIZE])
            cached = {i: session['summaries'][i] for i in range(after+1, after+len(packets)+1) if i in session['summaries']}
            result = {key: session[key] for key in ('id', 'link_id', 'endpoints', 'started', 'running', 'reason', 'bytes')}
            result.update(total=len(session['packets']), next=after+len(packets),
                          capture_busy_drops=self.contention_drops-session['initial_drops'],
                          seconds_left=max(0, int(session['deadline']-time.monotonic())) if session['running'] else 0)
        result['packets'] = [dict(number=after+i+1, time=stamp, direction=direction, outcome=outcome,
                                  length=len(frame), **summarize(frame))
                             for i, (stamp, direction, outcome, frame) in enumerate(packets)]
        missing = [(i, packet) for i, packet in enumerate(packets, after+1) if i not in cached]
        status = 'ok'
        if missing:
            status, summaries = decode_summaries([packet for _, packet in missing])
            for relative, summary in summaries.items():
                cached[missing[relative-1][0]] = summary
        with self.lock:
            session = self._owned(ident, owner)
            session['summaries'].update(cached)
        for row in result['packets']:
            summary = cached.get(row['number'])
            if summary:
                tags = list(dict.fromkeys(row['tags'] + summary['tags']))
                row.update(summary)
                row['tags'] = tags
        result['summary_decoder'] = status
        return result

    def packet(self, ident, owner, number):
        with self.lock:
            session = self._owned(ident, owner)
            if type(number) is not int or not 1 <= number <= len(session['packets']):
                raise ValueError('Unknown packet')
            stamp, _, _, frame = session['packets'][number-1]
            decoded = session['decoded'].get(number)
        if decoded is None:
            decoded = decode_packet(frame, stamp)
        with self.lock:
            # Recheck ownership/expiry after decoding, including logout while busy.
            session = self._owned(ident, owner)
            if decoded['status'] in ('ok', 'limited'):
                if number not in session['decoded'] and len(session['decoded']) >= 16:
                    session['decoded'].pop(next(iter(session['decoded'])))
                session['decoded'][number] = decoded
        # Detail responses are bounded; PCAP always retains the complete captured frame.
        return {'number': number, 'hex': frame[:4096].hex(), 'length': len(frame), 'truncated': len(frame) > 4096,
                'decode': decoded}

    def pcap(self, ident, owner):
        with self.lock:
            packets = list(self._owned(ident, owner)['packets'])
        return pcap_bytes(packets)
