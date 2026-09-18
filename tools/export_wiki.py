#!/usr/bin/env python3
"""Render docs/ for the GitHub wiki without committing or pushing anything."""
import argparse
from pathlib import Path, PurePosixPath
import posixpath
import re
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
REPO = 'https://github.com/weblab-network/weblab'
PAGES = {
    'README.md': 'Home',
    'installation.md': 'Installation',
    'containers.md': 'Container-images-and-EXOS-demo',
    'devices.md': 'Device-profiles',
    'topology.md': 'Topology',
    'consoles.md': 'Consoles',
    'instructions.md': 'Instructions',
    'link-actions.md': 'Link-failures',
    'backups.md': 'Saving-and-restoring-labs',
    'practice-labs.md': 'Creating-practice-labs',
    'troubleshooting.md': 'Troubleshooting',
    'limitations.md': 'Supported-scope',
    'development.md': 'Development',
}


def render(text):
    def link(match):
        label, destination = match.groups()
        parts = urlsplit(destination)
        if parts.scheme or parts.netloc or not parts.path:
            return match[0]
        path = posixpath.normpath('docs/' + parts.path)
        page = PAGES.get(PurePosixPath(path).name) if path.startswith('docs/') else None
        if page:
            target = REPO + '/wiki/' + page
        else:
            if path.startswith('../') or not (ROOT / path).is_file():
                raise ValueError('Unknown documentation target: ' + destination)
            target = REPO + '/blob/main/' + quote(path, safe='/')
        if parts.fragment:
            target += '#' + parts.fragment
        return '[' + label + '](' + target + ')'
    return re.sub(r'\[([^\]\n]+)\]\(([^\s)]+)\)', link, text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='Wiki checkout or output directory')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    docs = ROOT / 'docs'
    if {p.name for p in docs.glob('*.md')} != set(PAGES):
        raise ValueError('Update PAGES to include every help chapter')
    for name, page in PAGES.items():
        text = (docs / name).read_text()
        if page == 'Home':
            text = text.replace(
                'Relative links work on GitHub or in a Markdown reader. The built-in Instructions\nviewer opens one file at a time; use **Open file** for another chapter.',
                'An offline copy of this help is included in the source repository under `docs/`.\nThe built-in Instructions viewer opens one file at a time; use **Open file**\nfor another chapter.')
        (args.output / (page + '.md')).write_text(render(text))
    sidebar = ['**Weblab help**', '']
    sidebar += ['- [' + page.replace('-', ' ') + '](' + REPO + '/wiki/' + page + ')' for page in PAGES.values()]
    (args.output / '_Sidebar.md').write_text('\n'.join(sidebar) + '\n')
    (args.output / '_Footer.md').write_text(
        '[Weblab source](' + REPO + ') · [Report an issue](' + REPO + '/issues)\n\n'
        'Help is maintained in the source repository’s `docs/` directory.\n')
    print(f'Wrote {len(PAGES)} help pages, sidebar and footer to {args.output}')


if __name__ == '__main__':
    main()
