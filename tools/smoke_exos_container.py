#!/usr/bin/env python3
"""Verify demo initialization/recreation using disposable Docker volumes, no guests."""
import json
import subprocess
import sys
import time
import urllib.request
import uuid

from smoke_container import docker


def main():
    image = sys.argv[1]
    name = 'weblab-exos-smoke-' + uuid.uuid4().hex[:12]
    volumes = [name + '-images', name + '-data']
    mounts = ['-v', volumes[0] + ':/iou', '-v', volumes[1] + ':/data']
    try:
        for volume in volumes:
            docker('volume', 'create', volume)
        for iteration in range(2):
            docker('run', '-d', '--name', name, '-p', '127.0.0.1::8080', *mounts,
                   image, '--bind', '0.0.0.0', '--port', '8080', '--image-dir', '/iou', '--data-dir', '/data')
            port = docker('port', name, '8080/tcp').rsplit(':', 1)[1]
            for attempt in range(180):
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/state', timeout=2) as response:
                        state = json.load(response)
                    break
                except (OSError, ValueError):
                    if attempt == 179:
                        raise
                    time.sleep(1)
            assert len(state['topology']['nodes']) == 5, state
            assert all(s['state'] == 'stopped' for s in state['status'].values()), state
            assert state['topology']['name'] == ('EXOS: VLANs and routing' if iteration == 0 else 'User saved changes')
            docker('stop', '--time', '20', name)
            assert docker('inspect', '-f', '{{.State.ExitCode}}', name) == '0'
            print(docker('logs', name))
            docker('rm', name)
            if iteration == 0:
                docker('run', '--rm', *mounts, '--entrypoint', 'python3', image, '-c',
                       "import json,pathlib; p=pathlib.Path('/data/topology.json'); "
                       "t=json.loads(p.read_text()); t['name']='User saved changes'; p.write_text(json.dumps(t))")
        print('PASS: packaged demo seeds five stopped nodes and preserves edits across recreation')
    finally:
        subprocess.run(['docker', 'logs', name], check=False, stderr=subprocess.DEVNULL)
        subprocess.run(['docker', 'rm', '-f', name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for volume in volumes:
            subprocess.run(['docker', 'volume', 'rm', volume], check=False, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
