"""Bounded QMP requests for local NIC control and guest power-button events."""
import json
import socket
import time


class QMPError(Exception):
    pass


def set_link(path, index, up):
    _execute(path, {'execute': 'set_link', 'arguments': {'name': f'nic{index}', 'up': up}}, 'link control')


def powerdown(path):
    """Request guest shutdown; QMP acknowledgement does not mean it finished."""
    _execute(path, {'execute': 'system_powerdown'}, 'guest shutdown')


def _execute(path, command, label):
    deadline = time.monotonic() + 3
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(3)
            sock.connect(str(path))
            with sock.makefile('rb') as reader:
                def receive():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise QMPError(f'QEMU {label} timed out')
                    sock.settimeout(remaining)
                    line = reader.readline(65537)
                    if not line or len(line) > 65536:
                        raise QMPError('Invalid QMP response')
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise QMPError('Invalid QMP response')
                    return message

                if 'QMP' not in receive():
                    raise QMPError('Missing QMP greeting')
                for request in ({'execute': 'qmp_capabilities', 'id': 1},
                                {**command, 'id': 2}):
                    sock.sendall(json.dumps(request).encode() + b'\n')
                    while True:
                        message = receive()
                        if 'event' in message:
                            continue
                        if message.get('id') != request['id']:
                            raise QMPError('Unexpected QMP response')
                        if 'error' in message:
                            detail = message['error']
                            if not isinstance(detail, dict) or not isinstance(detail.get('desc'), str):
                                raise QMPError(f'QEMU rejected {label}')
                            raise QMPError(detail['desc'])
                        if 'return' not in message:
                            raise QMPError('Missing QMP result')
                        break
    except (OSError, ValueError) as exc:
        raise QMPError(f'QEMU {label} failed: {exc}') from exc
