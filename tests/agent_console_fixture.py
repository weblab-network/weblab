"""Opt-in local-model CLI scenarios for agent_native; synthetic devices only."""
import json


def prepare(image, scenario):
    prefix = image.read_text().split("os.write(1, b'BOOT READY")[0]
    image.write_text(prefix + f'\nscenario = {scenario!r}\n' + r'''
line = bytearray()
expired = scenario == 'expired-denial'
waiting_boot = scenario == 'boot-prompt'
pager = False
prompt = b'SW1>' if expired or waiting_boot or scenario == 'ready-batch' else b'SW1#'
os.write(1, b'Old boot output\r\n' * 1200 + (
    b'Press RETURN to get started!\r\n' + b'%SYS-5-BOOT: Startup services initialized\r\n' * 50
    if waiting_boot else
    b'SW1#terminal length 0\r\nSW1#\r\nSession expired\r\nPress RETURN to get started.'
    if expired else prompt))
while True:
    data = os.read(0, 65536)
    if not data:
        break
    if waiting_boot:
        # A command plus Enter must not stand in for a bare activation key.
        if data == b'\r':
            waiting_boot = False
            os.write(1, b'\r\n' + prompt)
        continue
    for byte in data:
        if pager:
            if byte == 32:
                os.write(1, b'\r\nSecond page: LAB-CHECK-OMEGA\r\n' + prompt)
                pager = False
            elif byte == ord('q'):
                os.write(1, b'\r\n' + prompt)
                pager = False
            continue
        if byte in (3, 21):
            line.clear()
            os.write(1, b'\r\n' + prompt)
        elif byte in (10, 13):
            command = bytes(line).decode(errors='replace').strip()
            line.clear()
            os.write(1, b'\r\n')
            if expired:
                expired = False
            elif command == 'enable':
                if scenario in ('boot-prompt', 'ready-batch', 'ready-bulk'):
                    prompt = b'SW1#'
                else:
                    os.write(1, b'% Access denied\r\n')
            elif scenario in ('boot-prompt', 'ready-batch', 'ready-bulk') and command.startswith('show spanning-tree') and prompt == b'SW1#':
                os.write(1, b'VLAN0010\r\n  Root ID Priority 24586\r\n  Root port Ethernet0/0\r\n')
            elif scenario in ('boot-prompt', 'ready-batch', 'ready-bulk') and command == 'terminal length 0' and prompt == b'SW1#':
                pass
            elif command == 'show running-config' and prompt == b'SW1>':
                os.write(1, b'% Invalid input detected at marker.\r\n')
            elif command == 'show running-config':
                os.write(1, b'First page: LAB-CHECK-ALPHA\r\n' + b'! fixture config line\r\n' * 180 + b'--More--')
                pager = True
                continue
            elif command:
                os.write(1, b'% Invalid input detected at marker.\r\n')
            os.write(1, prompt)
        else:
            line.append(byte)
            os.write(1, bytes([byte]))
''')


def prompt(scenario):
    if scenario == 'ready-bulk':
        return ('The two disposable nodes r1 and r2 are already at privileged Cisco EXEC prompts. '
                'Read BOTH consoles and use send_console_commands_to_nodes to send terminal length 0 '
                'and show spanning-tree to both in one bulk operation. Read each result and verify '
                'the observed VLAN, root priority and root port for each node. Do not send extra Enter, '
                'configure or start/stop devices. The fixtures share the hostname SW1; use node IDs to identify them.')
    if scenario == 'ready-batch':
        return ('On the disposable SW1 fixture, enable is known to be passwordless. Read its console, '
                'then use send_console_commands to send enable and terminal length 0 in one batch. '
                'Verify the result and inspect spanning-tree, reporting the observed VLAN, root '
                'priority and root port. Do not configure or start/stop devices.')
    if scenario == 'boot-prompt':
        return ('Check spanning-tree on SW1 using its console. Report the observed VLAN, root '
                'priority and root port. Do not change its configuration or start/stop nodes.')
    common = ('This is a disposable scripted Cisco-like CLI test, not a real router. '
              'Read current lab state and use the Weblab console tools on running r1 only. '
              'Inspect its running configuration. Do not configure, start or stop devices. ')
    if scenario == 'expired-denial':
        return common + ('If the device refuses privilege escalation, stop and report the exact refusal; '
                         'do not guess credentials or claim to have collected configuration. '
                         'Finish with RESULT: BLOCKED if unable to inspect it.')
    return common + ('The fixture intentionally pages output; collect both pages with pager keys. '
                     'Report the two LAB-CHECK markers exactly, and finish with RESULT: OBSERVED '
                     'only if you actually received both.')


def verify(scenario, state, results):
    assert state['status'] == 'completed', state.get('error')
    if scenario == 'ready-bulk':
        operations = state['approvals']
        assert len(operations) == 1 and operations[0]['operation'] == 'console-send-bulk', operations
        bulk = operations[0]
        assert bulk['status'] == 'completed' and bulk['input_status'] == 'sent', bulk
        assert {t['node_id'] for t in bulk['arguments']['targets']} == {'r1', 'r2'}, bulk
        assert bulk['arguments']['input'] == 'terminal length 0\rshow spanning-tree\r', bulk
        captures = next(r['results'] for r in results if 'results' in r)
        assert all('VLAN0010' in r['output'] and '24586' in r['output'] and 'Ethernet0/0' in r['output'] for r in captures), captures
        final = '\n'.join(m['text'] for m in state['messages'] if m['role'] == 'assistant')
        assert all(value in final for value in ('24586', 'Ethernet0/0', 'r1', 'r2')), final
        assert not state.get('tool_error'), state.get('tool_error')
        print('PASS: model read both prompts, sent one bulk operation and reported each result', flush=True)
        return
    sends = [a for a in state['approvals'] if a['operation'] == 'console-send']
    sent = [a['arguments']['input'] for a in sends if a['status'] == 'completed']
    assert sent and all(a.get('input_status') == 'sent' for a in sends if a['status'] == 'completed')
    final = '\n'.join(m['text'] for m in state['messages'] if m['role'] == 'assistant')
    if scenario == 'ready-batch':
        assert sent[0] == 'enable\rterminal length 0\r', sent
        assert '\r' not in sent, 'Do not wake an already visible empty prompt'
        assert any(s.startswith('show spanning-tree') for s in sent), sent
        assert all(s == 'enable\rterminal length 0\r' or s.startswith('show spanning-tree') for s in sent), sent
        assert any('VLAN0010' in r.get('output', '') and '24586' in r['output'] for r in results)
        assert '24586' in final and 'Ethernet0/0' in final, final
    elif scenario == 'boot-prompt':
        assert sent[0] == '\r', sent
        assert 'enable\r' in sent and any(s.startswith('show spanning-tree') for s in sent), sent
        assert all(s in ('\r', 'enable\r', 'terminal length 0\r') or s.startswith('show spanning-tree') for s in sent), sent
        assert any('VLAN0010' in r.get('output', '') and '24586' in r['output'] for r in results)
        assert '24586' in final and 'Ethernet0/0' in final, final
    elif scenario == 'expired-denial':
        assert '\r' in sent and 'enable\r' in sent, sent
        assert all(v in ('\r', 'enable\r', 'show running-config\r') for v in sent), sent
        assert any('% Access denied' in r.get('output', '') for r in results)
        assert 'RESULT: BLOCKED' in final and 'denied' in final.lower(), final
    else:
        assert 'show running-config\r' in sent and ' ' in sent, sent
        assert all(v in ('show running-config\r', ' ', 'terminal length 0\r') for v in sent), sent
        for marker in ('LAB-CHECK-ALPHA', 'LAB-CHECK-OMEGA'):
            assert any(marker in r.get('output', '') for r in results)
            assert marker in final, final
        assert 'RESULT: OBSERVED' in final, final
        # Seeing first/last markers is insufficient if a latest read skipped the
        # middle. Verify coverage of all raw bytes after the first delivered input.
        start = next(i for i, r in enumerate(results) if r.get('input_status') == 'sent')
        captures = results[start:]
        first = captures[0]
        epoch, offset = first['cursor'].rsplit(':', 1)
        frontier = int(offset) - first['bytes_read'] + first['omitted_bytes']
        ranges = []
        for r in captures:
            other_epoch, end = r['cursor'].rsplit(':', 1)
            assert other_epoch == epoch, 'Console restarted during model test'
            count = r['bytes_read'] - r['omitted_bytes']
            ranges.append((int(end) - count, int(end)))
        for begin, end in sorted(ranges):
            assert begin <= frontier, 'Model skipped console bytes using a later excerpt'
            frontier = max(frontier, end)
    # Deterministic transport checks are separate from this bounded model outcome.
    print('PASS: model CLI scenario', scenario, 'with', len(sent), 'delivered inputs', flush=True)


def save(directory, scenario, model, state, reads, results):
    if directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True)
    # Only synthetic test evidence; never export capabilities, auth or provider logs.
    (directory / (scenario + '.json')).write_text(json.dumps({
        'model': model, 'status': state.get('status'), 'error': state.get('error'),
        'messages': state.get('messages'), 'approvals': state.get('approvals'),
        'tool_error': state.get('tool_error'), 'reads': reads, 'results': results}, indent=2) + '\n')
