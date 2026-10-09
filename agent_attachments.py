"""Bounded user attachments. No file paths, URLs, archives or executable inputs."""
import base64
import binascii
from pathlib import PurePosixPath
import re
import struct

MAX_FILES = 4
MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 64 * 1024
TEXT_EXTENSIONS = {'.txt', '.md', '.json', '.yaml', '.yml', '.cfg', '.conf',
                   '.config', '.log', '.csv', '.ini', '.xml'}
IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}


def image_dimensions(raw, mime):
    """Inspect bounded image headers before handing decoding to Codex/browser."""
    try:
        if (mime == 'image/png' and len(raw) >= 33 and raw[:8] == b'\x89PNG\r\n\x1a\n'
                and raw[8:16] == b'\x00\x00\x00\x0dIHDR'):
            return struct.unpack('>II', raw[16:24])
        if mime == 'image/webp' and raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
            kind = raw[12:16]
            if kind == b'VP8X' and len(raw) >= 30:
                if raw[20] & 2:
                    raise ValueError('Animated images are not supported')
                return int.from_bytes(raw[24:27], 'little') + 1, int.from_bytes(raw[27:30], 'little') + 1
            if kind == b'VP8 ' and raw[23:26] == b'\x9d\x01\x2a':
                w, h = struct.unpack('<HH', raw[26:30])
                return w & 0x3fff, h & 0x3fff
            if kind == b'VP8L' and len(raw) >= 25 and raw[20:21] == b'\x2f':
                bits = int.from_bytes(raw[21:25], 'little')
                return (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
        if mime == 'image/jpeg' and raw[:2] == b'\xff\xd8':
            pos = 2
            while pos + 4 <= len(raw):
                if raw[pos] != 255:
                    break
                while pos < len(raw) and raw[pos] == 255:
                    pos += 1
                marker = raw[pos]; pos += 1
                if marker in (0xd9, 0xda):
                    break
                length = int.from_bytes(raw[pos:pos+2], 'big')
                if length < 2 or pos + length > len(raw):
                    break
                if marker in (0xc0, 0xc1, 0xc2):
                    h, w = struct.unpack('>HH', raw[pos+3:pos+7])
                    return w, h
                pos += length
    except (IndexError, struct.error):
        pass
    raise ValueError('Invalid image header; use a PNG, JPEG or WebP screenshot')


def validate(items, model=''):
    if not isinstance(items, list) or len(items) > MAX_FILES:
        raise ValueError('Attach up to 4 files per message')
    result, total, text_total = [], 0, 0
    for item in items:
        if not isinstance(item, dict) or set(item) != {'name', 'data'}:
            raise ValueError('Invalid attachment fields')
        name, data = item['name'], item['data']
        if (not isinstance(name, str) or not 1 <= len(name) <= 160 or
                '/' in name or '\\' in name or not name.isprintable() or name in ('.', '..')):
            raise ValueError('Invalid attachment filename')
        extension = PurePosixPath(name).suffix.lower()
        if extension not in TEXT_EXTENSIONS and extension not in IMAGE_TYPES:
            raise ValueError('Use PNG/JPEG/WebP screenshots or UTF-8 text/config files; PDF and office documents are not supported yet')
        if not isinstance(data, str) or len(data) > (MAX_BYTES + 2) // 3 * 4:
            raise ValueError('Attachments may total at most 2 MiB per message')
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('Invalid attachment encoding') from None
        total += len(raw)
        if not raw or total > MAX_BYTES:
            raise ValueError('Attachments must be nonempty and total at most 2 MiB per message')
        mime = IMAGE_TYPES.get(extension, 'text/plain')
        attachment = {'name': name, 'mime': mime, 'size': len(raw), 'raw': raw}
        if mime == 'text/plain':
            text_total += len(raw)
            if text_total > MAX_TEXT:
                raise ValueError('Text attachments may total at most 64 KiB per message')
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                raise ValueError('Text attachments must use UTF-8 encoding') from None
            if any(ord(c) < 32 and c not in '\t\r\n' or ord(c) == 127 for c in text):
                raise ValueError('Binary or control characters are not allowed in text attachments')
            attachment['text'] = text
        else:
            width, height = image_dimensions(raw, mime)
            if not (0 < width <= 8192 and 0 < height <= 8192 and width * height <= 16_000_000):
                raise ValueError('Screenshots must be at most 8192 pixels per side and 16 megapixels')
            if re.search(r'(^|/)gpt-oss(?=[:.\-]|$)', model, re.I):
                raise ValueError('gpt-oss is text-only. Choose an image-capable model or attach text instead')
        result.append(attachment)
    return result
