#!/usr/bin/env python3
"""Verify and promote the CI-tested Weblab 0.10.2 binary set without rebuilding."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
REPO = 'weblab-network/weblab'


def run(*args):
    return subprocess.check_output(args, cwd=ROOT).decode().strip()


def registry(image, reference):
    repository = image.removeprefix('ghcr.io/')
    query = urllib.parse.urlencode({'service': 'ghcr.io',
                                  'scope': 'repository:' + repository + ':pull'})
    with urllib.request.urlopen('https://ghcr.io/token?' + query, timeout=30) as response:
        token = json.load(response)['token']
    headers = {'Authorization': 'Bearer ' + token,
               'Accept': 'application/vnd.oci.image.index.v1+json, '
                         'application/vnd.docker.distribution.manifest.list.v2+json, '
                         'application/vnd.oci.image.manifest.v1+json, '
                         'application/vnd.docker.distribution.manifest.v2+json'}

    def get(path):
        request = urllib.request.Request('https://ghcr.io/v2/' + repository + '/' + path,
                                         headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()

    raw = get('manifests/' + reference)
    digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
    index = json.loads(raw)
    platforms = [entry for entry in index['manifests']
                 if entry.get('platform', {}).get('os') == 'linux'
                 and entry['platform'].get('architecture') == 'amd64']
    if len(platforms) != 1:
        raise ValueError('Expected one Linux amd64 manifest: ' + image)
    manifest = json.loads(get('manifests/' + platforms[0]['digest']))
    config = json.loads(get('blobs/' + manifest['config']['digest']))
    return digest, config['config']['Labels']


def verify(lock, *, published=False):
    if lock['release_tag'] != 'v0.10.2':
        raise ValueError('This promotion is restricted to v0.10.2')
    allowed = {'ghcr.io/' + REPO + suffix for suffix in ('', '-agent', '-exos')}
    if set(lock['images']) != allowed:
        raise ValueError('Unexpected release repositories')
    if run('git', 'rev-parse', lock['release_tag'] + '^{commit}') != lock['tag_commit']:
        raise ValueError('Release tag identity changed')
    changed = run('git', 'diff', '--name-only', lock['tag_commit'], lock['tested_commit']).splitlines()
    if changed != ['tests/test_auth_console_lock.py']:
        raise ValueError('Tested source differs beyond the reviewed test-only follow-up')
    # tests/ is excluded from the Docker context and none of the three recipes
    # copies it. All runtime/build inputs in these public commits are identical.
    result = json.loads(run('gh', 'api', 'repos/' + REPO + '/actions/runs/' + str(lock['tested_run'])))
    if (result['head_sha'] != lock['tested_commit'] or result['conclusion'] != 'success'
            or result['status'] != 'completed' or result['head_branch'] != 'main'
            or result['path'] != '.github/workflows/containers.yml'):
        raise ValueError('The locked container workflow did not pass on the reviewed source')
    latest = json.loads(run('gh', 'api', 'repos/' + REPO + '/releases/latest'))
    if tuple(map(int, latest['tag_name'].removeprefix('v').split('.'))) > (0, 10, 2):
        raise ValueError('Refusing to replace a newer release')
    for image, expected in lock['images'].items():
        digest, labels = registry(image, expected)
        if digest != expected or labels.get('org.opencontainers.image.revision') != lock['tested_commit']:
            raise ValueError('Image digest/source identity mismatch: ' + image)
        try:
            digest, _ = registry(image, '0.10.2')
        except urllib.error.HTTPError as error:
            if error.code != 404 or published:
                raise
        else:
            if digest != expected:
                raise ValueError('Refusing to overwrite a different versioned image: ' + image)
        if published and registry(image, 'latest')[0] != expected:
            raise ValueError('Latest differs from reviewed release: ' + image)
        print(image, expected, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true', help='Publish the verified version/latest aliases')
    parser.add_argument('--published', action='store_true', help='Require published version/latest aliases')
    args = parser.parse_args()
    lock = json.loads((ROOT / 'packaging/releases/v0.10.2.json').read_text())
    verify(lock, published=args.published)
    if args.publish:
        for image, digest in lock['images'].items():
            subprocess.run(['docker', 'buildx', 'imagetools', 'create', '--prefer-index=false',
                            '--tag', image + ':0.10.2', '--tag', image + ':latest',
                            image + '@' + digest], check=True)
        verify(lock, published=True)


if __name__ == '__main__':
    main()
