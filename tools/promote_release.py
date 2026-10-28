#!/usr/bin/env python3
"""Guard and promote tested GHCR digests; never rebuild or replace a version."""
import argparse
import hashlib
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

REPOSITORY = 'weblab-network/weblab'
ACCEPT = ', '.join(('application/vnd.oci.image.index.v1+json',
                    'application/vnd.docker.distribution.manifest.list.v2+json',
                    'application/vnd.oci.image.manifest.v1+json',
                    'application/vnd.docker.distribution.manifest.v2+json'))


def version(value):
    if not re.fullmatch(r'v?\d+\.\d+\.\d+', value):
        raise ValueError('Expected a stable release version: ' + value)
    return tuple(map(int, value.removeprefix('v').split('.')))


def registry(image, reference, missing_ok=False):
    repository = image.removeprefix('ghcr.io/')
    query = urllib.parse.urlencode({'service': 'ghcr.io', 'scope': 'repository:'+repository+':pull'})
    with urllib.request.urlopen('https://ghcr.io/token?'+query, timeout=30) as response:
        token = json.load(response)['token']

    def get(path):
        request = urllib.request.Request('https://ghcr.io/v2/'+repository+'/'+path,
            headers={'Authorization': 'Bearer '+token, 'Accept': ACCEPT})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    try:
        raw = get('manifests/'+reference)
    except urllib.error.HTTPError as error:
        if missing_ok and error.code == 404:
            return None
        raise  # Authentication, network and server failures are never "absent".
    digest = 'sha256:'+hashlib.sha256(raw).hexdigest()
    manifest = json.loads(raw)
    if 'manifests' in manifest:
        platforms = [entry for entry in manifest['manifests']
                     if entry.get('platform', {}).get('os') == 'linux'
                     and entry['platform'].get('architecture') == 'amd64']
        if len(platforms) != 1:
            raise ValueError('Expected one Linux amd64 image: '+image)
        manifest = json.loads(get('manifests/'+platforms[0]['digest']))
    config = json.loads(get('blobs/'+manifest['config']['digest']))
    if (config.get('os'), config.get('architecture')) != ('linux', 'amd64'):
        raise ValueError('Unexpected platform: '+image)
    return {'digest': digest, 'labels': config['config'].get('Labels', {})}


def preflight(images, tag, revision):
    wanted = version(tag)
    expected_names = {'ghcr.io/'+REPOSITORY+suffix for suffix in ('', '-agent', '-exos', '-internet')}
    if set(images) != expected_names or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Incomplete release image set or invalid source revision')
    resolved = {}
    for image, reference in images.items():
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', reference):
            raise ValueError('Promotion requires immutable tested digests')
        candidate = registry(image, reference)
        labels = candidate['labels']
        if (candidate['digest'] != reference or
                labels.get('org.opencontainers.image.revision') != revision or
                version(labels.get('org.opencontainers.image.version', '')) != wanted):
            raise ValueError('Tested image version/source mismatch: '+image)
        existing = registry(image, tag.removeprefix('v'), missing_ok=True)
        if existing and existing['digest'] != reference:
            raise ValueError('Refusing to overwrite existing version: '+image)
        latest = registry(image, 'latest', missing_ok=True)
        if latest:
            current = version(latest['labels'].get('org.opencontainers.image.version', ''))
            if current > wanted or (current == wanted and latest['digest'] != reference):
                raise ValueError('Refusing to replace newer or conflicting latest: '+image)
        resolved[image] = reference
    return resolved


def promote(images, tag, revision, publish=False):
    # Complete every guard before the first write. The workflow serializes all
    # release promotions, including different version tags, without cancellation.
    resolved = preflight(images, tag, revision)
    if publish:
        for alias in (tag.removeprefix('v'), 'latest'):
            for image, digest in resolved.items():
                subprocess.run(['docker', 'buildx', 'imagetools', 'create', '--prefer-index=false',
                                '--tag', image+':'+alias, image+'@'+digest], check=True)
                if registry(image, alias)['digest'] != digest:
                    raise ValueError('Published digest mismatch: '+image+':'+alias)
    return resolved


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--core', required=True)
    parser.add_argument('--agent', required=True)
    parser.add_argument('--exos', required=True)
    parser.add_argument('--internet', required=True)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    images = {'ghcr.io/'+REPOSITORY+suffix: digest for suffix, digest in
              (('', args.core), ('-agent', args.agent), ('-exos', args.exos), ('-internet', args.internet))}
    print(json.dumps(promote(images, args.tag, args.revision, args.publish), indent=2))
