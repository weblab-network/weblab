"""Lossless file-block references to an already checksum-verified base image.

No guest filesystem parsing or modification: decoding reproduces the original
QCOW2 bytes. ZIP supplies compression for literal blocks. Format v1 is an 8-byte
magic, big-endian uint64 decoded length, then operations: uint8 kind + uint32
length; kind 0 = zeros, 1 = literal bytes, 2 = uint64 base-file offset.
Only the final operation may have a length not divisible by BLOCK.
"""
import hashlib
import struct

MAGIC = b'WLBLK01\n'
ENCODING = 'base-blocks-v1'
SUFFIX = '.wl-delta'
BLOCK = 4096
CHUNK = 1024 * 1024
# Bound the dictionary to at most 262144 entries; larger images use ordinary ZIP.
MAX_BASE = 1024**3


def encode(source, base, output, size, base_size):
    if not 0 < base_size <= MAX_BASE:
        raise ValueError('Base image exceeds block-index limit')
    index = {}
    offset = 0
    while data := base.read(BLOCK):
        if offset + len(data) > base_size:
            raise ValueError('Base image changed during export')
        if any(data):
            index.setdefault(hashlib.sha256(data).digest(), offset)
        offset += len(data)
    if offset != base_size:
        raise ValueError('Base image changed during export')
    output.write(MAGIC + struct.pack('>Q', size))
    checksum = hashlib.sha256()
    pending = None
    literal = bytearray()

    def flush():
        if pending is None:
            return
        kind, length, start = pending
        output.write(struct.pack('>BI', kind, length))
        if kind == 1:
            output.write(literal)
        elif kind == 2:
            output.write(struct.pack('>Q', start))
        literal.clear()

    total = 0
    while data := source.read(BLOCK):
        checksum.update(data)
        total += len(data)
        if total > size:
            raise ValueError('Device storage changed during export')
        start = 0
        kind = 0 if not any(data) else 1
        if kind:
            candidate = index.get(hashlib.sha256(data).digest())
            if candidate is not None:
                base.seek(candidate)
                # Verify equality too, rather than relying on hash collisions.
                if base.read(len(data)) == data:
                    kind, start = 2, candidate
        merge = (pending is not None and pending[0] == kind and
                 pending[1] + len(data) <= CHUNK and
                 (kind != 2 or pending[2] + pending[1] == start))
        if merge:
            pending[1] += len(data)
        else:
            flush()
            pending = [kind, len(data), start]
        if kind == 1:
            literal.extend(data)
    flush()
    if total != size:
        raise ValueError('Device storage changed during export')
    return checksum.hexdigest()


def decode(source, base, output, expected_size, base_size):
    """Decode a bounded stream, returning (payload SHA256, reconstructed SHA256)."""
    payload_hash, restored_hash = hashlib.sha256(), hashlib.sha256()

    def read(length):
        data = source.read(length)
        if len(data) != length:
            raise ValueError('Truncated disk delta')
        payload_hash.update(data)
        return data

    if read(len(MAGIC)) != MAGIC or struct.unpack('>Q', read(8))[0] != expected_size:
        raise ValueError('Invalid disk delta header or size')
    written = 0
    while written < expected_size:
        kind, length = struct.unpack('>BI', read(5))
        if not 0 < length <= CHUNK or written + length > expected_size:
            raise ValueError('Invalid disk delta operation length')
        if written + length < expected_size and length % BLOCK:
            raise ValueError('Unaligned disk delta operation')
        if kind == 0:
            data = bytes(length)
            output.seek(length, 1)
        elif kind == 1:
            data = read(length)
            output.write(data)
        elif kind == 2:
            offset = struct.unpack('>Q', read(8))[0]
            if offset + length > base_size:
                raise ValueError('Disk delta reference exceeds base image')
            base.seek(offset)
            data = base.read(length)
            if len(data) != length:
                raise ValueError('Truncated base image')
            output.write(data)
        else:
            raise ValueError('Unknown disk delta operation')
        restored_hash.update(data)
        written += length
    if source.read(1):
        raise ValueError('Trailing disk delta data')
    output.truncate(written)
    return payload_hash.hexdigest(), restored_hash.hexdigest()
