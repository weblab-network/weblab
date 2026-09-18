"""Small bounded QMP client for explicit link-state changes on local QEMU NICs."""
import json
import socket
import time


class QMPError(Exception):
    pass


def set_link(path, index, up):
    deadline = time.monotonic() + 3
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(3)
            sock.connect(str(path))
            with sock.makefile('rb') as reader:
                def receive():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise QMPError('QEMU link control timed out')
                    sock.settimeout(remaining)
                    line = reader.readline(65537)
                    if not line or len(line) > 65536:
                        raise QMPError('Invalid QMP response')
                    return json.loads(line)

                if 'QMP' not in receive():
                    raise QMPError('Missing QMP greeting')
                for request in ({'execute': 'qmp_capabilities', 'id': 1},
                                {'execute': 'set_link', 'arguments': {'name': f'nic{index}', 'up': up}, 'id': 2}):
                    sock.sendall(json.dumps(request).encode() + b'\n')
                    while True:
                        message = receive()
                        if 'event' in message:
                            continue
                        if message.get('id') != request['id']:
                            raise QMPError('Unexpected QMP response')
                        if 'error' in message:
                            raise QMPError(message['error'].get('desc', 'QEMU rejected link change'))
                        if 'return' not in message:
                            raise QMPError('Missing QMP result')
                        break
    except (OSError, ValueError) as exc:
        raise QMPError(f'QEMU link control failed: {exc}') from exc
