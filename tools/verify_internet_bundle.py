#!/usr/bin/env python3
"""Verify helper binary/source checksums, image identity and build provenance."""
import argparse
import hashlib
import json
import posixpath
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import urllib.parse
import urllib.request

PREFIX = 'weblab-internet-'
FILES = {PREFIX + suffix for suffix in ('image.json', 'NOTICE.txt', 'sources.tar.gz', 'linux-amd64.tar.gz')}


def safe_source_members(members):
    for member in members.values():
        path = PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise ValueError('Unsafe source archive member')
        current, seen = member, set()
        while current.issym() or current.islnk():
            # Alpine recipes legitimately link package hook names. Preserve only
            # links resolving inside that exact retained origin/commit recipe.
            if len(path.parts) < 4 or path.parts[0] != 'aports' or current.name in seen:
                raise ValueError('Unsafe source archive link')
            seen.add(current.name)
            if len(seen) > 16 or PurePosixPath(current.linkname).is_absolute():
                raise ValueError('Unsafe source archive link')
            target = posixpath.normpath(posixpath.join(posixpath.dirname(current.name), current.linkname)
                                       if current.issym() else current.linkname)
            if not target.startswith('/'.join(path.parts[:3]) + '/') or target not in members:
                raise ValueError('Unsafe source archive link')
            current = members[target]


def verify(directory, version=None, revision=None):
    expected = {}
    for line in (directory/(PREFIX+'SHA256SUMS')).read_text().splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  ([a-zA-Z0-9_.-]+)', line)
        if not match or match[2] in expected:
            raise ValueError('Invalid or duplicate checksum entry')
        expected[match[2]] = match[1]
    if set(expected) != FILES:
        raise ValueError('Incomplete or unexpected binary/source bundle')
    for name, digest in expected.items():
        path = directory/name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or unsafe bundle file: ' + name)
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise ValueError('Bundle checksum mismatch: ' + name)
    info = json.loads((directory/(PREFIX+'image.json')).read_text())
    labels = info['labels']
    if (not re.fullmatch(r'sha256:[a-f0-9]{64}', info['Id']) or
            (info['Os'], info['Architecture']) != ('linux', 'amd64') or
            labels.get('network.weblab.internet.distribution') != '1' or
            labels.get('org.opencontainers.image.source') != 'https://github.com/weblab-network/weblab'):
        raise ValueError('Unexpected release image identity')
    if version is not None and labels.get('org.opencontainers.image.version') != version:
        raise ValueError('Image version differs from the release')
    if revision is not None and labels.get('org.opencontainers.image.revision') != revision:
        raise ValueError('Image source revision differs from the reviewed public commit')
    with tarfile.open(directory/(PREFIX+'linux-amd64.tar.gz')) as archive:
        manifests = json.load(archive.extractfile('manifest.json'))
        if len(manifests) != 1:
            raise ValueError('Expected exactly one Docker image')
        config = archive.extractfile(manifests[0]['Config']).read()
        if 'sha256:' + hashlib.sha256(config).hexdigest() != info['Id']:
            raise ValueError('Archived image does not match inventoried image identity')
        if json.loads(config)['config'].get('Labels') != labels:
            raise ValueError('Archived image labels do not match metadata')
        if info['image'] not in manifests[0].get('RepoTags', []):
            raise ValueError('Archived image tag differs from metadata')
    with tarfile.open(directory/(PREFIX+'sources.tar.gz')) as archive:
        members = {member.name: member for member in archive}
        safe_source_members(members)
        packages = json.load(archive.extractfile('packages.json'))
        if not packages or packages != info['packages']:
            raise ValueError('Source inventory differs from image metadata')
        for package in packages:
            recipe = f'aports/{package["origin"]}/{package["commit"]}/APKBUILD'
            if recipe not in members:
                raise ValueError('Missing corresponding Alpine recipe: ' + recipe)
        for name, digest in info['build_files'].items():
            if hashlib.sha256(archive.extractfile('build/' + name).read()).hexdigest() != digest:
                raise ValueError('Source build input mismatch: ' + name)
    print('Verified Internet binary/source pair:', info['Id'])
    return info


def anonymous_reference(image, image_id):
    if not image.startswith('ghcr.io/'):
        raise ValueError('Expected a GHCR release image')
    repository, tag = image.removeprefix('ghcr.io/').rsplit(':', 1)
    query = urllib.parse.urlencode({'service': 'ghcr.io', 'scope': 'repository:'+repository+':pull'})
    # No Docker credentials: this must prove the public package is anonymously readable.
    try:
        with urllib.request.urlopen('https://ghcr.io/token?'+query, timeout=30) as response:
            token = json.load(response)['token']
        request = urllib.request.Request('https://ghcr.io/v2/'+repository+'/manifests/'+tag, headers={
            'Authorization': 'Bearer '+token,
            'Accept': 'application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json'})
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(4*1024*1024+1)
        if len(raw) > 4*1024*1024 or json.loads(raw).get('config', {}).get('digest') != image_id:
            raise ValueError('Public registry manifest differs from the tested image')
    except OSError as exc:
        raise ValueError('Anonymous helper pull verification failed; check GHCR package visibility and retry the release job') from exc
    return 'ghcr.io/'+repository+'@sha256:'+hashlib.sha256(raw).hexdigest()


def registry_lock(directory, image, info):
    actual = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image]))[0]
    if actual['Id'] != info['Id']:
        raise ValueError('Pulled registry image differs from the tested binary')
    reference = anonymous_reference(image, info['Id'])
    result = {'image': reference, 'image_id': info['Id'],
              'version': info['labels']['org.opencontainers.image.version'],
              'public_revision': info['labels']['org.opencontainers.image.revision']}
    (directory/(PREFIX+'registry.json')).write_text(json.dumps(result, indent=2)+'\n')
    print('Verified published helper:', reference)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--version')
    parser.add_argument('--revision')
    parser.add_argument('--registry-image', help='After pulling, verify its identity and write the immutable registry reference')
    args = parser.parse_args()
    info = verify(args.directory, args.version, args.revision)
    if args.registry_image:
        registry_lock(args.directory, args.registry_image, info)
