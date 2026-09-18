"""Create an initial raw IOL NVRAM image; never edit existing device storage."""
import os
from pathlib import Path
import struct
import tempfile


def seed_iol(path, config):
    if path.exists():
        return False
    # IOL NVRAM's uncompressed configuration layout: a 36-byte startup header,
    # four-byte-aligned text, a 16-byte empty private header, and a one's-
    # complement checksum over the first half of the 64 KiB NVRAM image.
    text = config.encode('utf-8')
    if len(text) > 16_384:
        raise ValueError('Initial configuration exceeds 16 KiB')
    text += b'\n' * (-len(text) % 4)
    image = bytearray(65536)
    address = 0x10000000
    struct.pack_into('>HHHHIII', image, 0, 0xABCD, 1, 0, 0x0F04,
                     address + 36, address + 36 + len(text), len(text))
    image[36:36 + len(text)] = text
    private = 36 + len(text)
    struct.pack_into('>HHIII', image, private, 0xFEDC, 1,
                     address + private + 16, address + private + 16, 0)
    total = sum(word[0] for word in struct.iter_unpack('>H', image[:32768]))
    while total >> 16:
        total = (total & 65535) + (total >> 16)
    struct.pack_into('>H', image, 4, total ^ 65535)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.initial-nvram-', delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(image)
            output.flush()
            os.fsync(output.fileno())
            # Hard-link publication fails rather than overwriting any existing NVRAM.
            try:
                os.link(temporary, path)
            except FileExistsError:
                return False
        finally:
            temporary.unlink(missing_ok=True)
    return True
