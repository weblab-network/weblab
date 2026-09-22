"""Bounded, lossless base-image references and malicious stream rejection."""
import hashlib
import io
import os
import struct
import unittest

import disk_delta as delta


class DiskDeltaTests(unittest.TestCase):
    def test_moved_blocks_zero_runs_and_literal_tail(self):
        base = os.urandom(delta.BLOCK * 4)
        original = base[delta.BLOCK:] + bytes(delta.CHUNK + 19) + b'new saved config' + base[:37]
        encoded = io.BytesIO()
        checksum = delta.encode(io.BytesIO(original), io.BytesIO(base), encoded, len(original), len(base))
        self.assertLess(len(encoded.getvalue()), len(original) // 20)
        restored = io.BytesIO()
        payload, decoded = delta.decode(io.BytesIO(encoded.getvalue()), io.BytesIO(base), restored, len(original), len(base))
        self.assertEqual(restored.getvalue(), original)
        self.assertEqual(decoded, checksum)
        self.assertEqual(payload, hashlib.sha256(encoded.getvalue()).hexdigest())

    def test_invalid_streams(self):
        header = delta.MAGIC + struct.pack('>Q', 16)
        bad = [b'', b'wrong', header, header + struct.pack('>BI', 0, 0),
               header + struct.pack('>BI', 0, 17), header + struct.pack('>BI', 7, 16),
               header + struct.pack('>BI', 1, 16) + b'short',
               header + struct.pack('>BI', 0, 1),
               header + struct.pack('>BIQ', 2, 16, 1),
               header + struct.pack('>BI', 0, 16) + b'trailing']
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                delta.decode(io.BytesIO(data), io.BytesIO(bytes(16)), io.BytesIO(), 16, 16)

    def test_size_bounds_and_changed_source(self):
        for size in (2, 4):
            with self.assertRaisesRegex(ValueError, 'storage changed'):
                delta.encode(io.BytesIO(b'abc'), io.BytesIO(b'base'), io.BytesIO(), size, 4)
        with self.assertRaisesRegex(ValueError, 'block-index limit'):
            delta.encode(io.BytesIO(), io.BytesIO(), io.BytesIO(), 0, delta.MAX_BASE + 1)
        with self.assertRaisesRegex(ValueError, 'Base image changed'):
            delta.encode(io.BytesIO(), io.BytesIO(b'ab'), io.BytesIO(), 0, 3)
        with self.assertRaisesRegex(ValueError, 'Base image changed'):
            delta.encode(io.BytesIO(), io.BytesIO(b'abc'), io.BytesIO(), 0, 2)
        with self.assertRaisesRegex(ValueError, 'header or size'):
            delta.decode(io.BytesIO(delta.MAGIC + struct.pack('>Q', 32)), io.BytesIO(), io.BytesIO(), 16, 0)
        with self.assertRaisesRegex(ValueError, 'Truncated base'):
            delta.decode(io.BytesIO(delta.MAGIC + struct.pack('>QBIQ', 16, 2, 16, 0)),
                         io.BytesIO(b'short'), io.BytesIO(), 16, 16)
