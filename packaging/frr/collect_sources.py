#!/usr/bin/env python3
"""Collect exact Alpine package recipes, patches and verified upstream sources.

Run in a disposable Alpine container. Snapshot installed APK metadata BEFORE
installing build tools. The output accompanies the binary image release.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import urllib.request
import tempfile


FRR_REVISION = 'f8c0b08dcb0c78f9e42b9b86ae70b049e4e617c1'
LIBYANG_SHA256 = '19a5fe2742ccd68cc90bd7b28736d518ebea4241e798fa49a01b5e6f8a79928e'
CUSTOM = {'frr': '10.7.1_git-r0', 'libyang': '2.1.128-r0'}


def inventory(text):
    rows = []
    for block in text.strip().split('\n\n'):
        fields = {}
        for line in block.splitlines():
            if ':' in line:
                key, value = line.split(':', 1)
                if key in {'P', 'V', 'o', 'c', 'L', 'U'}:
                    fields[key] = value
        if not fields:
            continue
        if not all(fields.get(k) for k in ('P', 'V', 'o', 'c', 'L')):
            raise ValueError('Package metadata missing source identity')
        custom = fields['o'] in CUSTOM and fields['V'] == CUSTOM[fields['o']] and fields['c'] == '-dirty'
        if not re.fullmatch(r'[a-zA-Z0-9+_.-]+', fields['o']) or (not custom and not re.fullmatch(r'[0-9a-f]{7,40}', fields['c'])):
            raise ValueError('Invalid package source identity')
        rows.append(dict(name=fields['P'], version=fields['V'], origin=fields['o'],
                         commit=fields['c'], license=fields['L'], upstream=fields.get('U', ''),
                         source_repository='FRRouting/frr' if custom else 'alpinelinux/aports'))
    if not rows:
        raise ValueError('Empty package inventory')
    return sorted(rows, key=lambda r: r['name'])


def run(*args, **kwargs):
    return subprocess.check_output(args, **kwargs)


def collect(database, output):
    rows = inventory(database.read_text())
    output.mkdir(parents=True, exist_ok=True)
    (output / 'packages.json').write_text(json.dumps(rows, indent=2) + '\n')
    (output / 'apk-installed.txt').write_bytes(database.read_bytes())
    repo = Path(tempfile.mkdtemp(prefix='weblab-frr-aports-'))
    run('git', 'init', '-q', str(repo))
    run('git', '-C', str(repo), 'remote', 'add', 'origin', 'https://github.com/alpinelinux/aports.git')
    for origin, commit in sorted({(r['origin'], r['commit']) for r in rows}):
        if commit == '-dirty':
            continue  # Explicitly accounted for below; never guess an aports revision.
        print('Collecting sources:', origin, commit, flush=True)
        run('git', '-C', str(repo), 'fetch', '-q', '--depth=1', '--filter=blob:none', 'origin', commit)
        entries = run('git', '-C', str(repo), 'ls-tree', '-d', '--name-only', 'FETCH_HEAD',
                      'main/' + origin, 'community/' + origin).decode().splitlines()
        if len(entries) != 1:
            raise RuntimeError('Cannot identify recipe for ' + origin)
        destination = output / 'aports' / origin / commit
        destination.mkdir(parents=True, exist_ok=True)
        archive = repo / 'recipe.tar'
        archive.write_bytes(run('git', '-C', str(repo), 'archive', 'FETCH_HEAD:' + entries[0]))
        with tarfile.open(archive) as bundle:
            bundle.extractall(destination, filter='data')
        series = '.'.join(Path('/etc/alpine-release').read_text().strip().split('.')[:2])
        had_src = (destination / 'src').exists()
        env = dict(os.environ, SRCDEST=str(destination / 'distfiles'),
                   DISTFILES_MIRROR='https://distfiles.alpinelinux.org/distfiles/v' + series)
        # abuild validates each fetched source against the recipe's checksums.
        subprocess.run(['abuild', '-F', 'fetch', 'verify'], cwd=destination, env=env, check=True)
        # abuild creates scratch symlinks with container-absolute paths.
        # Keep the actual recipes, patches and distfiles; omit this scratch tree.
        if not had_src and (destination / 'src').is_dir():
            shutil.rmtree(destination / 'src')
    custom_sources(output)
    shutil.rmtree(repo)
    (output / 'README.txt').write_text(
        'Sources accompanying Weblab FRR. packages.json inventories inherited APK versions.\n'
        'aports/<origin>/<commit>/ contains Alpine build recipes, patches and verified distfiles.\n'
        'Build with the matching Alpine release and abuild toolchain; see APKBUILD and upstream\n'
        'license files inside each source archive. FRR is rebuilt over the base APK; see custom/README.txt.\n')


def custom_sources(output):
    source = Path('/usr/share/weblab-frr/frr-source.tar')
    destination = output / 'custom'
    destination.mkdir()
    with tarfile.open(source) as archive:
        for name in ('docker/alpine/libyang/APKBUILD', 'docker/alpine/Dockerfile',
                     'alpine/APKBUILD.in'):
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(name).read())
    url = 'https://codeload.github.com/CESNET/libyang/tar.gz/refs/tags/v2.1.128'
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != LIBYANG_SHA256:
        raise ValueError('libyang source checksum mismatch')
    (destination / 'libyang-2.1.128.tar.gz').write_bytes(data)
    (destination / 'README.txt').write_text(
        'The pinned upstream FRR base contains custom FRR and libyang APKs marked -dirty.\n'
        'Their recipes are from FRRouting/frr commit ' + FRR_REVISION + '.\n'
        'FRR source is ../.. / frr-source.tar in the accompanying build context.\n'
        'Weblab rebuilds FRR executables/libraries using its Dockerfile and patch; the inherited\n'
        'APK database continues to describe upstream FRR, including debug/docs packages.\n'
        'libyang 2.1.128 source is included with its upstream recipe. That recipe generates\n'
        'checksums during the upstream build; this collector verifies a separately pinned SHA256.\n'
        'Use the original docker/alpine/Dockerfile and its abuild stages to rebuild the base.\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    collect(args.database, args.output.resolve())
