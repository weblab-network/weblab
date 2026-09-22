"""Read saved Cisco configuration without opening consoles or modifying storage.

Own bounded NVRAM decoder; QEMU handles QCOW2 and mtools reads FAT files from a
private sparse raw conversion. Only stopped Cisco nodes are eligible.
"""
import copy
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import stat
import struct
import subprocess
import tempfile
import time

import frr
import vios

MAX_NVRAM = 2 * 1024 * 1024
MAX_DISK = 8 * 1024**3


def regular(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('Saved device storage must be a regular file')


def config_text(data):
    text = data.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n').strip() + '\n'
    # Stored banners may use a literal control-C delimiter. Convert only paired
    # banner delimiters; other control bytes are rejected, never silently removed.
    def banner(match):
        delimiter = next((chr(c) for c in range(33, 127) if chr(c) not in match[2]), None)
        if delimiter is None:
            raise ValueError('Saved banner has no available printable delimiter')
        return match[1] + delimiter + match[2] + delimiter
    text = re.sub(r'(?ms)^(banner[ \t]+\S+[ \t]+)\x03(.*?)\x03', banner, text)
    if any(ord(c) < 32 and c not in '\n\t' for c in text) or '\x7f' in text:
        raise ValueError('Saved configuration contains unsupported control characters')
    if not re.search(r'(?:^|\n)end\n\Z', text):
        raise ValueError('Saved configuration is empty or incomplete')
    if len(text.encode('utf-8')) > 16_384:
        raise ValueError('Saved configuration exceeds the 16 KiB snippet limit; use Saved lab ZIP')
    return text


def decode_nvram(data):
    # IOL uses 64 KiB; the tested IOSv families use 512 KiB. The checksum covers
    # the first half, including the four-byte-aligned startup/private records.
    # Pointers are relocated by IOS; only their offsets and lengths are relevant.
    if len(data) not in (65536, 524288):
        raise ValueError('Unsupported NVRAM size')
    magic, version, _, _, start, end, length = struct.unpack_from('>HHHHIII', data)
    if magic != 0xABCD or version not in (1, 2):
        raise ValueError('Unsupported NVRAM configuration format')
    area = len(data) // 2
    total = sum(word[0] for word in struct.iter_unpack('>H', data[:area]))
    while total >> 16:
        total = (total & 65535) + (total >> 16)
    if total != 65535:
        raise ValueError('Saved NVRAM checksum is invalid')
    if not 0 < length <= area - 52 or end - start != length or start < 36:
        raise ValueError('Saved NVRAM configuration bounds are invalid')
    private = (36 + length + 3) & ~3
    pmagic, pversion, pstart, pend, plen = struct.unpack_from('>HHIII', data, private)
    if (pmagic != 0xFEDC or pversion != 1 or pstart != start - 36 + private + 16 or
            pend - pstart != plen or private + 16 + plen > area):
        raise ValueError('Saved NVRAM private header is invalid')
    payload = data[36:36 + length]
    if version == 2:
        expanded = struct.unpack_from('>I', data, 32)[0]
        if not 0 < expanded <= 16_384:
            raise ValueError('Compressed configuration exceeds the 16 KiB snippet limit')
        if not payload.startswith(b'\x1f\x9d'):
            raise ValueError('Unsupported NVRAM compression format')
        if not shutil.which('gzip'):
            raise ValueError('Install gzip or rebuild the lab container to read compressed NVRAM')
        with tempfile.TemporaryFile() as source:
            source.write(payload)
            source.seek(0)
            payload = command_bytes(['gzip', '-dc'], limit=16_384, stdin=source)
        if payload is None or len(payload) != expanded:
            raise ValueError('Compressed NVRAM configuration is damaged')
    return config_text(payload)


def command_bytes(args, limit=MAX_NVRAM, stdin=subprocess.DEVNULL):
    """Bound both output and elapsed time, including malformed FAT images."""
    with subprocess.Popen(args, stdin=stdin, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as child:
        try:
            output = bytearray()
            deadline = time.monotonic() + 20
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise ValueError('Timed out reading IOSv NVRAM')
                    chunk = os.read(child.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > limit:
                        raise ValueError('Saved configuration exceeds its extraction limit')
            child.wait(timeout=max(.01, deadline - time.monotonic()))
            if child.returncode:
                return None
            return bytes(output)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()


def disk_nvram(raw):
    size = raw.stat().st_size
    with raw.open('rb') as stream:
        mbr = stream.read(512)
    if len(mbr) != 512 or mbr[510:] != b'\x55\xaa':
        raise ValueError('Unsupported IOSv partition table')
    found = []
    for index in range(4):
        kind = mbr[446 + index * 16 + 4]
        start, sectors = struct.unpack_from('<II', mbr, 446 + index * 16 + 8)
        if kind not in (1, 4, 6, 11, 12, 14):
            continue
        if start < 1 or sectors < 1 or (start + sectors) * 512 > size:
            raise ValueError('Invalid IOSv FAT partition bounds')
        data = command_bytes(['mtype', '-i', str(raw) + '@@' + str(start * 512), '::nvram'])
        if data is not None:
            found.append(data)
    if len(found) != 1:
        raise ValueError('No unique saved NVRAM file found on the IOSv disk')
    return found[0]


def read_node(lab, node, run):
    directory = lab.directory / 'nodes' / node['id']
    if directory.is_symlink() or directory.parent.is_symlink():
        raise ValueError('Device storage directory cannot be a symlink')
    if frr.is_frr(node):
        config = frr.read_config(frr.config_path(node, directory)).decode()
        if len(config.encode()) > 16_384:
            raise ValueError('Saved FRR config exceeds 16 KiB; use Saved lab ZIP')
        return config
    if node['image'].endswith('.bin'):
        path = directory / f"nvram_{node['iol_id']:05d}"
        regular(path)
        with path.open('rb') as stream:
            data = stream.read(MAX_NVRAM + 1)
        return decode_nvram(data)
    if not vios.is_vios(node):
        raise ValueError('No saved configuration reader for this device')
    if not shutil.which('mtype') or not shutil.which('qemu-img'):
        raise ValueError('Install mtools and qemu-utils, or rebuild the lab container')
    backing, disk = vios.disk_paths(node, directory)
    regular(disk)
    base = lab.image_dir / node['image']
    if not backing.is_symlink() or backing.resolve() != base.resolve():
        raise ValueError('IOSv disk does not reference its expected installed image')
    vios.validate_image(base, run, ValueError)
    vios.validate_image(disk, run, ValueError, backing.name)
    info = json.loads(run('qemu-img', 'info', '-f', 'qcow2', '--output=json', str(disk)))
    size = info['virtual-size']
    if size > MAX_DISK:
        raise ValueError('Saved-config extraction supports IOSv disks up to 8 GiB; use live configs or Saved lab ZIP')
    if shutil.disk_usage(lab.directory).free < size + 64 * 1024**2:
        raise ValueError('Not enough temporary disk space to read the IOSv disk safely')
    with tempfile.TemporaryDirectory(prefix='.saved-config-', dir=lab.directory) as temporary:
        raw = Path(temporary) / 'disk.raw'
        # Never force shared access (-U): a disk in use must fail. Conversion
        # reads the source chain and writes only this private, sparse temp file.
        run('qemu-img', 'convert', '-f', 'qcow2', '-O', 'raw', str(disk), str(raw), timeout=120)
        return decode_nvram(disk_nvram(raw))


def export(lab, run):
    with lab.lock:
        for node in lab.topology['nodes']:
            if vios.is_junos(node):
                raise ValueError("Junos configuration-text export is not supported yet; use Saved lab ZIP")
            if vios.is_veos(node):
                raise ValueError("Arista vEOS configuration extraction is not supported yet; use Saved lab ZIP")
            if vios.is_exos(node):
                raise ValueError("EXOS configuration extraction is not supported yet; use Saved lab ZIP")
            if node['type'] != 'pc' and node['id'] in lab.runtime:
                raise ValueError(f"Stop {node['name']} before reading saved configurations")
        result = copy.deepcopy(lab.topology)
        for node in result['nodes']:
            if node['type'] == 'pc':
                continue
            try:
                node['startup_config'] = read_node(lab, node, run)
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                alternative = "Use Saved lab ZIP to preserve device storage." if frr.is_frr(node) else "Use JSON + live configs for console retrieval, or Saved lab ZIP to preserve device storage."
                raise ValueError(f"{node['name']}: {error}. No file exported. {alternative}") from error
        if len((json.dumps(result, indent=2) + '\n').encode()) > 1_000_000:
            raise ValueError('Result exceeds the 1 MB topology import limit; use Saved lab ZIP')
        return result
