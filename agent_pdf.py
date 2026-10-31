"""Bounded, offline PDF conversion for the optional Agent companion only.

Poppler is a separate unprivileged process. No shell, URLs, embedded scripts or
model tools are used. The caller owns a temporary directory for the entire turn.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

MAX_PAGES = 50
MAX_TEXT = 256 * 1024
MAX_SECONDS = 60
PAGE_PIXELS = 1600
MAX_PAGE_BYTES = 2 * 1024 * 1024
# Keep the former worst-case rendering budget (12 x 2 MiB), shared across PDFs.
# Base64 expansion, attached screenshots and protocol overhead also need room
# inside the bounded provider transport; raising page count must not remove it.
MAX_IMAGE_BYTES = 24 * 1024 * 1024


def convert(source, directory, *, pages_left, text_left, image_bytes_left, deadline, cancel):
    """Return extracted text and ordered page images, or reject the whole PDF."""
    directory = Path(directory)
    directory.mkdir(mode=0o700)
    programs = {name: shutil.which(name) for name in ('prlimit', 'pdfinfo', 'pdftotext', 'pdftoppm')}
    if not all(programs.values()):
        raise ValueError('PDF support needs Poppler utilities in the Agent companion; rebuild its image')

    def run(name, args, output, limit):
        # prlimit applies bounds before exec without unsafe preexec_fn in a
        # threaded service. Capture output to a bounded file, never an open pipe.
        with output.open('xb') as stream:
            os.chmod(output, 0o600)
            process = subprocess.Popen([
                programs['prlimit'], '--as=536870912', '--cpu=10',
                '--fsize=' + str(limit), '--nofile=64', '--core=0', '--',
                programs[name], *map(str, args)], stdout=stream, stderr=subprocess.DEVNULL,
                cwd=directory, env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
                                    'HOME': str(directory)}, start_new_session=True)
            try:
                while True:
                    if cancel.is_set():
                        raise ValueError('PDF preparation stopped; no model request was sent')
                    if time.monotonic() >= deadline:
                        raise ValueError('PDF preparation deadline reached (up to 60 seconds, within the turn deadline); attach fewer or simpler pages')
                    try:
                        code = process.wait(timeout=.1)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                if code:
                    raise ValueError('PDF could not be processed within its resource limits; use a valid, unencrypted, smaller PDF')
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()

    info = directory / 'info.txt'
    run('pdfinfo', [source], info, 64 * 1024)
    metadata = info.read_text(errors='replace')
    # User-controlled metadata (e.g. Title) can contain newlines and imitate a
    # field. pdfinfo prints its actual page/encryption fields after metadata.
    encrypted = re.findall(r'^Encrypted:\s+(yes|no)\b', metadata, re.M)
    if encrypted and encrypted[-1] == 'yes':
        raise ValueError('Encrypted PDFs are not supported; attach an unencrypted copy')
    counts = re.findall(r'^Pages:[ \t]+(\d+)[ \t]*$', metadata, re.M)
    count = int(counts[-1]) if counts else 0
    if not 1 <= count <= pages_left:
        raise ValueError('PDF attachments may contain at most 50 pages combined per message; split the document first')
    text_file = directory / 'text.txt'
    run('pdftotext', ['-enc', 'UTF-8', '-layout', source, '-'], text_file, text_left + 1)
    raw = text_file.read_bytes()
    if len(raw) > text_left:
        raise ValueError('PDF extracted text and text attachments may total at most 256 KiB; split the document first')
    text = raw.decode('utf-8', errors='strict')
    pages = []
    for page in range(1, count + 1):
        stem = directory / ('page-' + str(page))
        run('pdftoppm', ['-f', page, '-l', page, '-singlefile', '-scale-to', PAGE_PIXELS,
                        '-jpeg', '-jpegopt', 'quality=90', source, stem],
            directory / ('render-' + str(page) + '.txt'), MAX_PAGE_BYTES)
        image = stem.with_suffix('.jpg')
        if not image.is_file() or not 0 < image.stat().st_size <= MAX_PAGE_BYTES:
            raise ValueError('PDF page could not be rendered; attach a simpler PDF')
        image_bytes_left -= image.stat().st_size
        if image_bytes_left < 0:
            raise ValueError('Rendered PDF pages exceed 24 MiB combined; attach fewer pages or a smaller excerpt')
        os.chmod(image, 0o600)
        pages.append(image)
    return text, pages
