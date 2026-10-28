#!/usr/bin/env python3
"""Collect exact, unmodified Alpine runtime sources in a disposable build stage."""
import argparse
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile


def inventory(text):
    rows = []
    for block in text.strip().split('\n\n'):
        fields = dict(line.split(':', 1) for line in block.splitlines()
                      if ':' in line and line.split(':', 1)[0] in {'P', 'V', 'o', 'c', 'L', 'U'})
        if not fields:
            continue
        if not all(fields.get(key) for key in ('P', 'V', 'o', 'c', 'L')):
            raise ValueError('Incomplete APK source identity')
        if not re.fullmatch(r'[a-zA-Z0-9+_.-]+', fields['o']) or not re.fullmatch(r'[0-9a-f]{7,40}', fields['c']):
            raise ValueError('Only unmodified Alpine packages with a pinned aports commit are supported')
        rows.append({'name': fields['P'], 'version': fields['V'], 'origin': fields['o'],
                     'commit': fields['c'], 'license': fields['L'], 'upstream': fields.get('U', '')})
    if not rows or len({row['name'] for row in rows}) != len(rows):
        raise ValueError('Empty or duplicate APK inventory')
    return sorted(rows, key=lambda row: row['name'])


def license_texts(distfiles, destination):
    if not distfiles.exists():
        return  # Meta-packages can consist entirely of the retained recipe.
    for path in sorted(distfiles.iterdir()):
        if not path.is_file() or not tarfile.is_tarfile(path):
            continue
        with tarfile.open(path) as archive:
            for member in archive:
                name = PurePosixPath(member.name)
                if (not member.isfile() or member.size > 2 * 1024 * 1024 or name.is_absolute()
                        or '..' in name.parts or not any(fnmatch.fnmatch(name.name.lower(), pattern)
                            for pattern in ('copying*', 'license*', 'licence*', 'copyright*', 'notice*'))):
                    continue
                target = destination / path.name / str(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())


def collect(database, output):
    rows = inventory(database.read_text())
    output.mkdir(parents=True, exist_ok=True)
    (output / 'packages.json').write_text(json.dumps(rows, indent=2) + '\n')
    (output / 'apk-installed.txt').write_bytes(database.read_bytes())
    with tempfile.TemporaryDirectory(prefix='weblab-internet-aports-') as temporary:
        for origin, commit in sorted({(row['origin'], row['commit']) for row in rows}):
            # Isolate shallow state: lazy blob fetches from git archive must not
            # race the next origin's fetch into the same shallow repository.
            repo = Path(temporary) / origin
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), 'remote', 'add', 'origin', 'https://github.com/alpinelinux/aports.git'], check=True)
            print('Collecting runtime sources:', origin, commit, flush=True)
            subprocess.run(['git', '-C', str(repo), 'fetch', '-q', '--depth=1', '--filter=blob:none', 'origin', commit], check=True)
            entries = subprocess.check_output(['git', '-C', str(repo), 'ls-tree', '-d', '--name-only',
                'FETCH_HEAD', 'main/' + origin, 'community/' + origin]).decode().splitlines()
            if len(entries) != 1:
                raise ValueError('Cannot identify exact Alpine recipe: ' + origin)
            destination = output / 'aports' / origin / commit
            destination.mkdir(parents=True)
            recipe = repo / 'recipe.tar'
            recipe.write_bytes(subprocess.check_output(['git', '-C', str(repo), 'archive', 'FETCH_HEAD:' + entries[0]]))
            with tarfile.open(recipe) as archive:
                archive.extractall(destination, filter='data')
            series = '.'.join(Path('/etc/alpine-release').read_text().strip().split('.')[:2])
            env = dict(os.environ, SRCDEST=str(destination / 'distfiles'),
                       DISTFILES_MIRROR='https://distfiles.alpinelinux.org/distfiles/v' + series)
            subprocess.run(['abuild', '-F', 'fetch', 'verify'], cwd=destination, env=env, check=True)
            if (destination / 'src').is_dir():
                shutil.rmtree(destination / 'src')
            license_texts(destination / 'distfiles', output / 'licenses' / origin / commit)
    (output / 'README.txt').write_text(
        'Sources for the exact Weblab Internet runtime APK inventory in packages.json.\n'
        'aports/<origin>/<commit> contains matching recipes, patches and verified distfiles.\n'
        'licenses/ retains upstream notices found in source archives.\n'
        'build/ contains Weblab helper source and its Dockerfile; build target release.\n'
        'Use matching Alpine/abuild tooling to rebuild runtime packages from APKBUILD.\n'
        'This is runtime source, not an offline toolchain or bit-identical build guarantee.\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    collect(args.database, args.output.resolve())
