#!/usr/bin/env python3
"""Start an empty, unprivileged disposable container and check its HTTP API."""
import json
import subprocess
import sys
import time
import urllib.request
import uuid


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def main():
    image = sys.argv[1]
    name = 'weblab-smoke-' + uuid.uuid4().hex[:12]
    try:
        docker('run', '-d', '--name', name, '-p', '127.0.0.1::8080', image,
               '--bind', '0.0.0.0', '--port', '8080', '--image-dir', '/iou',
               '--data-dir', '/data')
        port = docker('port', name, '8080/tcp').rsplit(':', 1)[1]
        for attempt in range(30):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/state', timeout=2) as response:
                    state = json.load(response)
                assert state['topology']['nodes'] == [], state
                print('PASS: packaged server starts and serves an empty lab')
                break
            except (OSError, ValueError):
                if attempt == 29:
                    raise
                time.sleep(1)
        docker('stop', '--time', '20', name)
        assert docker('inspect', '-f', '{{.State.ExitCode}}', name) == '0'
        print('PASS: packaged server shuts down cleanly')
    finally:
        subprocess.run(['docker', 'logs', name], check=False)
        subprocess.run(['docker', 'rm', '-f', name], check=False, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
