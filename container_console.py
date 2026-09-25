"""Keep an Alpine/FRR console independent of its running container.

Only a successful shell logout is retried. Docker errors and repeated rapid
exits fail visibly; this never starts, stops or recreates a container.
"""
from collections import deque
import argparse
import shutil
import signal
import subprocess
import sys
import time


def supervise(container, *, frr=False, run=subprocess.call, clock=time.monotonic, sleep=time.sleep):
    docker = shutil.which('docker')
    if not docker:
        print('Docker client unavailable', file=sys.stderr, flush=True)
        return 1
    exits = deque()
    command = [docker, 'exec', '-it']
    if frr:
        command += ['-e', 'VTYSH_PAGER=cat', container]
        command += ['/bin/sh', '-c',
                    'vtysh; status=$?; [ "$status" -eq 0 ] || exit "$status"; '
                    'printf "\\nLinux shell. Run vtysh to return to the routing CLI.\\n"; '
                    'exec /bin/bash --noprofile --norc']
    else:
        command += [container, '/bin/sh']
    while True:
        code = run(command)
        if code != 0:
            print(f'Container console exited with status {code}; not retrying', file=sys.stderr, flush=True)
            return code if code > 0 else 128 - code
        now = clock()
        while exits and now - exits[0] >= 10:
            exits.popleft()
        exits.append(now)
        if len(exits) >= 3:
            print('Container console exited three times within 10 seconds; recovery stopped', file=sys.stderr, flush=True)
            return 1
        print('\r\n[Console shell closed; reopening in one second.]', flush=True)
        sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frr', action='store_true', help='Open vtysh, then a Linux shell on CLI exit')
    parser.add_argument('container')
    args = parser.parse_args()
    # Docker's attached TTY handles Ctrl+C for foreground guest commands.
    # Do not kill the supervisor in the short interval between attachments.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    child = None

    def stop(signum, frame):
        if child is not None and child.poll() is None:
            child.terminate()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)

    def run(command):
        nonlocal child
        child = subprocess.Popen(command)
        return child.wait()

    raise SystemExit(supervise(args.container, frr=args.frr, run=run))


if __name__ == '__main__':
    main()
