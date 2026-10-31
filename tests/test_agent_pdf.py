"""Offline PDF reference processing; no inference, network or active lab."""
import base64
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import agent_attachments
import agent_pdf


def document(pages=1, text=True, title=None):
    """Original PDF fixture with searchable text and a drawn router/link diagram."""
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               ('<< /Type /Pages /Count %d /Kids [%s] >>' %
                (pages, ' '.join(f'{4 + n*2} 0 R' for n in range(pages)))).encode(),
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    for n in range(pages):
        stream = (f'BT /F1 14 Tf 40 200 Td (Lab page {n+1}: R1 to R2) Tj ET\n' if text else '').encode()
        stream += b'0 0 1 RG 40 70 60 40 re S 200 70 60 40 re S 100 90 m 200 90 l S\n'
        objects += [(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 240] '
                     f'/Resources << /Font << /F1 3 0 R >> >> /Contents {5+n*2} 0 R >>').encode(),
                    f'<< /Length {len(stream)} >>\nstream\n'.encode() + stream + b'endstream']
    if title is not None:
        objects.append(b'<< /Title (' + title.encode() + b') >>')
    info = f' /Info {len(objects)} 0 R' if title is not None else ''
    raw, offsets = b'%PDF-1.4\n', [0]
    for n, obj in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += f'{n} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(raw)
    raw += f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode()
    raw += b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    return raw + (f'trailer\n<< /Size {len(offsets)} /Root 1 0 R{info} >>\nstartxref\n{xref}\n%%EOF\n').encode()


class ValidationTests(unittest.TestCase):
    def test_provider_header_and_text_only_model(self):
        file = {'name': 'lab.PDF', 'data': base64.b64encode(document()).decode()}
        result = agent_attachments.validate([file], 'vision-model', 'openai')
        self.assertEqual(result[0]['mime'], 'application/pdf')
        self.assertEqual(result[0]['raw'], document())
        for provider, model in [('ollama', 'vision-model'), ('openai', 'gpt-oss:20b')]:
            with self.assertRaises(ValueError):
                agent_attachments.validate([file], model, provider)
        file['data'] = base64.b64encode(b'not PDF').decode()
        with self.assertRaisesRegex(ValueError, 'PDF header'):
            agent_attachments.validate([file], 'vision-model', 'openai')


@unittest.skipUnless(all(shutil.which(p) for p in ('prlimit', 'pdfinfo', 'pdftotext', 'pdftoppm')),
                     'requires Poppler utilities and prlimit (included in the Agent image)')
class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'original.pdf'
        self.source.write_bytes(document(2))
        self.cancel = threading.Event()

    def tearDown(self):
        self.temp.cleanup()

    def convert(self, **overrides):
        args = dict(pages_left=50, text_left=262144, image_bytes_left=24*1024*1024,
                    deadline=time.monotonic()+60, cancel=self.cancel)
        args.update(overrides)
        return agent_pdf.convert(self.source, self.root/'pages', **args)

    def test_text_and_diagrams_in_page_order(self):
        text, pages = self.convert()
        self.assertIn('Lab page 1: R1 to R2', text)
        self.assertLess(text.index('page 1'), text.index('page 2'))
        self.assertEqual([p.name for p in pages], ['page-1.jpg', 'page-2.jpg'])
        for page in pages:
            self.assertEqual(page.stat().st_mode & 0o777, 0o600)
            w, h = agent_attachments.image_dimensions(page.read_bytes(), 'image/jpeg')
            self.assertEqual(max(w, h), 1600)

    def test_graphics_only_page_is_still_sent(self):
        self.source.write_bytes(document(text=False))
        text, pages = self.convert()
        self.assertFalse(text.strip())
        self.assertEqual(len(pages), 1)
        self.assertGreater(pages[0].stat().st_size, 1000)

    def test_fifty_pages_are_all_rendered(self):
        self.source.write_bytes(document(50))
        text, pages = self.convert()
        self.assertIn('Lab page 50: R1 to R2', text)
        self.assertEqual(len(pages), 50)
        self.assertEqual(pages[-1].name, 'page-50.jpg')
        self.assertLess(sum(p.stat().st_size for p in pages), agent_pdf.MAX_IMAGE_BYTES)

    def test_render_budget_rejects_without_returning_partial_document(self):
        with self.assertRaisesRegex(ValueError, '24 MiB'):
            self.convert(image_bytes_left=1)
        self.assertFalse((self.root/'pages'/'page-2.jpg').exists())

    def test_remaining_page_budget_rejects_before_rendering(self):
        with self.assertRaisesRegex(ValueError, '50 pages'):
            self.convert(pages_left=1)
        self.assertFalse(list((self.root/'pages').glob('*.jpg')))

    def test_metadata_cannot_hide_pages(self):
        self.source.write_bytes(document(51, title='Fake title\nPages: 1\nEncrypted: no'))
        with self.assertRaisesRegex(ValueError, '50 pages'):
            self.convert()

    def test_text_budget_is_not_silently_truncated(self):
        with self.assertRaises(ValueError):
            self.convert(text_left=8)
        self.assertFalse(list((self.root/'pages').glob('*.jpg')))

    def test_malformed_pdf(self):
        self.source.write_bytes(b'%PDF-1.4\nnot a document')
        with self.assertRaisesRegex(ValueError, 'PDF could not be processed'):
            self.convert()

    def test_cancel(self):
        self.cancel.set()
        with self.assertRaisesRegex(ValueError, 'stopped'):
            self.convert()

    def test_deadline(self):
        with self.assertRaisesRegex(ValueError, 'deadline reached'):
            self.convert(deadline=time.monotonic()-1)

    def test_missing_dependencies(self):
        with patch.object(agent_pdf.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'rebuild'):
                self.convert()


if __name__ == '__main__':
    unittest.main()
