#!/usr/bin/env python3
"""Optional MCP SDK integration; disposable echo fixture, no vendor boots.

Run with the Python environment containing requirements-mcp.txt.
Optionally --ollama-url http://HOST:11434/v1 --model MODEL tests model tool calls
using synthetic catalog entries. No live workspace or vendor image is accessed.
"""
import argparse
import copy
import asyncio
import json
from pathlib import Path
import shutil
import sys
from unittest.mock import patch
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import test_lab
import lab_automation
import lab_server
import storage
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def payload(result):
    if result.is_error:
        raise AssertionError(result.content)
    return result.structured_content or json.loads(result.content[0].text)


async def model_test(session, args, initialization):
    available = (await session.list_tools()).tools
    allowed = {'get_lab_state', 'get_authoring_guide', 'list_device_profiles', 'preview_topology', 'get_proposal'}
    tools = [{'type': 'function', 'function': {'name': t.name, 'description': t.description,
              'parameters': t.input_schema}} for t in available if t.name in allowed]
    messages = [{'role': 'system', 'content': initialization.instructions + '\nThis is a structural integration test with synthetic image files. Do not start devices or claim boot/protocol validation.'},
                {'role': 'user', 'content': 'Prepare and preview an original lab with two routers (one FRR and one vJunosEvolved), four switches (one vJunos-switch and three EXOS), for STP, VRRP and OSPF practice. Discover the catalog and read the guide first. Include a meaningful connected graph and companion Markdown with addressing, tasks and manual configuration requirements. Do not apply or start anything. Report the proposal ID and download links.'}]
    preview = None
    for step in range(20):
        body = json.dumps({'model': args.model, 'messages': messages, 'tools': tools,
                           'stream': False, 'temperature': 0.3, 'max_tokens': 12000}).encode()
        request = Request(args.ollama_url.rstrip('/') + '/chat/completions', data=body,
                          headers={'Content-Type': 'application/json'})
        def complete():
            with urlopen(request, timeout=300) as response:
                return json.load(response)
        reply = await asyncio.to_thread(complete)
        message = reply['choices'][0]['message']
        messages.append(message)
        calls = message.get('tool_calls') or []
        print(f'Model round {step+1}: ' + ', '.join(c['function']['name'] for c in calls), flush=True)
        if not calls:
            if preview:
                break
            messages.append({'role': 'user', 'content': 'Please finish by calling preview_topology with the complete topology and exercise Markdown. Do not only describe it.'})
            continue
        for call in calls:
            function = call['function']
            if function['name'] not in allowed:
                result_text = 'Tool is not allowed in this test.'
            else:
                try:
                    result = await session.call_tool(function['name'], json.loads(function['arguments']))
                    result_text = json.dumps(result.model_dump(mode='json'))
                    if function['name'] == 'preview_topology' and not result.is_error:
                        preview = payload(result)
                        print('Model preview accepted:', preview['proposal_id'], flush=True)
                    elif result.is_error:
                        print('Validation/tool error:', result_text[:700], flush=True)
                except (ValueError, TypeError) as error:
                    result_text = str(error)
            messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': result_text})
    assert preview, 'Model did not produce a valid preview within the step budget'
    nodes = preview['topology']['nodes']
    assert len(nodes) == 6, f'Expected six nodes, got {len(nodes)}'
    assert sum(n['type'] == 'router' for n in nodes) == 2
    assert len(preview['topology']['links']) >= 5
    assert 'manual' in preview['instructions_markdown'].lower() or 'console' in preview['instructions_markdown'].lower()
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / 'topology.json').write_text(json.dumps(preview['topology'], indent=2) + '\n')
        (args.output / 'exercise.md').write_text(preview['instructions_markdown'])
        (args.output / 'transcript.json').write_text(json.dumps(messages, indent=2))
    print('Model structural preview passed (no vendor boot or protocol test).', flush=True)


async def link_fault_checks(fixture):
    lab = fixture.lab
    url = f'http://127.0.0.1:{fixture.port}'
    link_id = lab.topology['links'][0]['id']
    lab.start('r1')
    lab.automation.console_read('r1', wait_seconds=.1)
    try:
        for compact in (False, True):
            params = StdioServerParameters(command=sys.executable,
                args=[str(ROOT / 'tools/weblab_mcp.py'), '--url', url, '--allow-link-faults']
                + (['--compact-responses'] if compact else []))
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    names = {t.name for t in (await session.list_tools()).tools}
                    assert {'get_link_state', 'set_link_frame_loss', 'set_link_carrier'} <= names
                    assert not {'apply_topology', 'start_node', 'send_console_input', 'apply_internet_settings'} & names
                    state = payload(await session.call_tool('get_link_state', {'link_id':link_id}))
                    assert state['a']['node'] == 'r1' and state['b']['node'] == 'r2'
                    args = dict(link_id=link_id, expected_revision=state['revision'], expected_link_revision=state['link_revision'])
                    # Fake IOL is not a supported carrier profile; don't invent a success.
                    rejected = await session.call_tool('set_link_carrier', {**args, 'side':'a', 'up':False})
                    assert rejected.is_error
                    result = payload(await session.call_tool('set_link_frame_loss', {
                        **args, 'blocked_a_to_b':True, 'blocked_b_to_a':False}))
                    assert result['applied'] and result['state']['blocked_a_to_b']
                    assert len(json.dumps(result).encode()) < 8000
                    stale = await session.call_tool('set_link_frame_loss', {
                        **args, 'blocked_a_to_b':False, 'blocked_b_to_a':False})
                    assert stale.is_error
                    state = payload(await session.call_tool('get_link_state', {'link_id':link_id}))
                    result = payload(await session.call_tool('set_link_frame_loss', dict(link_id=link_id,
                        expected_revision=state['revision'], expected_link_revision=state['link_revision'],
                        blocked_a_to_b=False, blocked_b_to_a=False)))
                    assert result['applied'] and not result['state']['blocked_a_to_b']
        print('MCP link faults: independent opt-in, full/compact reads, block/clear and stale/profile guards passed.', flush=True)
    finally:
        lab.stop_all()


async def main(args):
    fixture = test_lab.LabTests()
    fixture.setUp()
    try:
        lab = fixture.lab
        lab.automation = lab_automation.Automation(lab, lab_server.LabError, lab_server.ports, lab_server.run)
        await link_fault_checks(fixture)
        lab.save({'name': 'Empty', 'nodes': [], 'links': []})
        (fixture.root / 'docs').mkdir()
        (fixture.root / 'docs/practice-labs.md').write_text((ROOT / 'docs/practice-labs.md').read_text())
        url = f'http://127.0.0.1:{fixture.port}'
        for writable, console_input in ((False, False), (True, False), (False, True), (True, True)):
            params = StdioServerParameters(command=sys.executable,
                args=[str(ROOT / 'tools/weblab_mcp.py'), '--url', url]
                + (['--allow-write'] if writable else [])
                + (['--allow-console-input'] if console_input else []))
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    initialization = await session.initialize()
                    names = {t.name for t in (await session.list_tools()).tools}
                    assert ('apply_topology' in names) == writable
                    assert ('start_node' in names) == writable
                    assert not {'apply_internet_settings', 'stop_internet_node', 'start_internet_node'} & names
                    assert {'get_internet_settings', 'preview_internet_settings'} <= names
                    assert 'get_console_output' in names
                    assert 'get_link_state' in names
                    assert not {'set_link_frame_loss', 'set_link_carrier'} & names
                    assert ('send_console_input' in names) == console_input
                    assert ('send_console_command' in names) == console_input
                    assert ('send_console_key' in names) == console_input
                    assert ('send_console_commands' in names) == console_input
                    assert ('send_console_commands_to_nodes' in names) == console_input
                    by_name = {t.name: t for t in (await session.list_tools()).tools}
                    assert by_name['get_lab_state'].annotations.read_only_hint
                    assert by_name['get_console_output'].annotations.read_only_hint
                    storage_tool = by_name['get_storage_report']
                    assert storage_tool.annotations.read_only_hint
                    assert not storage_tool.annotations.destructive_hint
                    assert not storage_tool.annotations.open_world_hint
                    if console_input:
                        assert not by_name['send_console_input'].annotations.read_only_hint
                        assert by_name['send_console_input'].annotations.destructive_hint
                        assert by_name['send_console_command'].annotations.destructive_hint
                        assert not by_name['send_console_command'].annotations.read_only_hint
                        assert by_name['send_console_key'].annotations.destructive_hint
                        assert not by_name['send_console_key'].annotations.read_only_hint
                        assert by_name['send_console_commands_to_nodes'].annotations.destructive_hint
                        assert not by_name['send_console_commands_to_nodes'].annotations.read_only_hint
                        assert by_name['send_console_commands'].annotations.destructive_hint
                        assert not by_name['send_console_commands'].annotations.read_only_hint
                    if writable:
                        assert by_name['apply_topology'].annotations.destructive_hint
                    assert 'stop_node' not in names
                    state = payload(await session.call_tool('get_lab_state'))
                    assert not state['topology']['nodes']
                    # Real STDIO -> HTTP storage read, available with every exposure
                    # combination. Never start a device or inspect disk contents.
                    probe = lab.node_dir('storage_probe')
                    disk = probe / 'sparse.qcow2'
                    with disk.open('wb') as stream:
                        stream.write(b'x' * 4096)
                        stream.truncate(512 * storage.MIB)
                    (probe / 'console.log').write_text('retained log')
                    before = copy.deepcopy(lab.topology)
                    try:
                        lab.storage.expires = 0
                        report = payload(await session.call_tool('get_storage_report'))
                        assert report['measured_at'] and report['complete']
                        assert len(report['filesystems']) == 3
                        row = next(n for n in report['nodes'] if n['id'] == 'storage_probe')
                        assert row['retained'] and row['complete']
                        assert row['disk_bytes'] == disk.stat().st_blocks * 512
                        assert row['apparent_bytes'] > row['allocated_bytes']
                        assert row['log_bytes'] > 0
                        assert report['node_allocated_bytes'] == sum(n['allocated_bytes'] for n in report['nodes'])
                        cached = payload(await session.call_tool('get_storage_report'))
                        assert cached == report
                        with patch.object(storage, 'MAX_ENTRIES', 1):
                            lab.storage.expires = 0
                            partial = payload(await session.call_tool('get_storage_report'))
                            assert partial['complete'] is False
                        with patch('storage.shutil.disk_usage', side_effect=PermissionError()):
                            lab.storage.expires = 0
                            unknown = payload(await session.call_tool('get_storage_report'))
                            assert all(f['level'] == 'unknown' and f['free_bytes'] is None
                                       for f in unknown['filesystems'])
                        service, lab.automation = lab.automation, None
                        try:
                            with patch.object(lab.storage, 'report', side_effect=AssertionError('Opt-in must be checked first')):
                                disabled = await session.call_tool('get_storage_report')
                            assert disabled.is_error
                            assert 'disabled' in disabled.content[0].text.lower()
                        finally:
                            lab.automation = service
                        assert lab.topology == before and not lab.runtime
                        assert disk.stat().st_size == 512 * storage.MIB
                    finally:
                        shutil.rmtree(probe)
                        lab.storage.expires = 0
                    print('MCP storage: sparse/retained accounting, cache, partial/unknown results and opt-in passed.', flush=True)
                    # The guide's actual JSON example must survive real SDK/HTTP
                    # preview, download, apply and readback. No gateway is started.
                    guide = payload(await session.call_tool('get_authoring_guide'))['markdown']
                    internet = next(value for block in guide.split('```json\n')[1:]
                                    if isinstance(value := json.loads(block.split('```')[0]), dict)
                                    and 'port_forwards' in value)
                    cloud_source = {'name': 'MCP gateway', 'nodes': [
                        {'id': 'internet', 'name': 'Internet', 'type': 'cloud', 'internet': internet}], 'links': []}
                    draft = payload(await session.call_tool('preview_topology', {
                        'topology': cloud_source, 'instructions': '# Gateway settings\nStructural check only.'}))
                    assert not lab.topology['nodes'] and not lab.runtime
                    assert draft['topology']['nodes'][0]['internet'] == internet
                    with urlopen(draft['downloads']['topology']) as response:
                        assert json.load(response) == draft['topology']
                    if writable:
                        applied = payload(await session.call_tool('apply_topology', {'proposal_id': draft['proposal_id']}))
                        observed = payload(await session.call_tool('get_lab_state'))
                        assert observed['revision'] == applied['revision']
                        assert observed['topology']['nodes'][0]['internet'] == internet
                        assert not lab.runtime
                        lab.save({'name': 'Empty', 'nodes': [], 'links': []})
                    payload(await session.call_tool('discard_proposal', {'proposal_id': draft['proposal_id']}))
                    print('MCP gateway guide, firewall/port-forward preview/download and guarded apply/readback passed (no ports published).', flush=True)
                    invalid = await session.call_tool('get_node_logs', {'node_id': '../state'})
                    assert invalid.is_error
                    assert 'Invalid node ID' in invalid.content[0].text
                    if console_input:
                        lab.save({'name': 'Console test', 'nodes': [fixture.node('console_echo')], 'links': []})
                        lab.start('console_echo')
                        observed = payload(await session.call_tool('get_console_output', {
                            'node_id': 'console_echo', 'wait_seconds': 0.1}))
                        assert 'BOOT READY' in observed['output']
                        result = payload(await session.call_tool('send_console_input', {
                            'node_id': 'console_echo', 'input': 'MCP-CONSOLE\r',
                            'expected_revision': observed['revision'], 'expected_cursor': observed['cursor'],
                            'wait_seconds': 0.2}))
                        assert 'RX:MCP-CONSOLE' in result['output']
                        assert result['input_status'] == 'sent'
                        # Default no-cursor reads select the tail, even when a
                        # local model asks for a tiny output budget.
                        tail = payload(await session.call_tool('get_console_output', {
                            'node_id': 'console_echo', 'wait_seconds': 0.1, 'max_bytes': 16}))
                        assert tail['read_mode'] == 'latest'
                        assert tail['omitted_bytes'] > 0
                        assert 'MCP-CONSOLE' in tail['output']
                        history = payload(await session.call_tool('get_console_output', {
                            'node_id': 'console_echo', 'wait_seconds': 0.1,
                            'max_bytes': 4, 'latest': False}))
                        assert history['output'] == 'BOOT'
                        assert history['capture_end'] == 'limit'
                        remainder = payload(await session.call_tool('get_console_output', {
                            'node_id': 'console_echo', 'wait_seconds': 0.1, 'cursor': history['cursor']}))
                        assert remainder['read_mode'] == 'stream'
                        assert ' READY' in remainder['output']
                        command_args = {'node_id': 'console_echo', 'expected_revision': remainder['revision'],
                                        'expected_cursor': remainder['cursor'], 'wait_seconds': .2}
                        # Bad syntax must not type anything, including doubled
                        # JavaScript escapes from the reported failure.
                        for command in ('', ' ', 'show clock\\r', 'one\\ntwo', 'show clock\r',
                                        'one\ntwo', 'show\tclock', '\x03', '\x7f', '\x85',
                                        'one\u2028two', 'x'*4096, 'é'*2048):
                            bad = await session.call_tool('send_console_command', {**command_args, 'command': command})
                            assert bad.is_error, repr(command[:30])
                        unchanged = payload(await session.call_tool('get_console_output', {
                            'node_id': 'console_echo', 'cursor': remainder['cursor'], 'wait_seconds': .1}))
                        assert unchanged['cursor'] == remainder['cursor'] and unchanged['output'] == ''
                        sent = payload(await session.call_tool('send_console_command', {
                            **command_args, 'command': 'show clock'}))
                        assert sent['output'] == 'RX:show clock', sent
                        assert sent['input_status'] == 'sent'
                        stale_command = await session.call_tool('send_console_command', {
                            **command_args, 'command': 'STALE'})
                        assert stale_command.is_error
                        stale = await session.call_tool('send_console_input', {
                            'node_id': 'console_echo', 'input': 'STALE\r',
                            'expected_revision': observed['revision'], 'expected_cursor': observed['cursor']})
                        assert stale.is_error
                        stale_key = await session.call_tool('send_console_key', {**command_args, 'key': 'enter'})
                        assert stale_key.is_error
                        # Named keys must deliver exact bytes over the same guarded
                        # send route, without trusting model escape serialization.
                        for key, value in (('enter', '\r'), ('space', ' '), ('tab', '\t'),
                                           ('ctrl_c', '\x03'), ('ctrl_u', '\x15')):
                            observed = payload(await session.call_tool('get_console_output', {
                                'node_id': 'console_echo', 'wait_seconds': .1}))
                            key_args = {'node_id': 'console_echo', 'expected_revision': observed['revision'],
                                        'expected_cursor': observed['cursor'], 'wait_seconds': .1}
                            with patch.object(lab.automation, 'console_send', wraps=lab.automation.console_send) as send:
                                for bad_key in ('', 'ENTER', 'show clock', '\\r', 'enter\n', 'ctrl_d'):
                                    bad = await session.call_tool('send_console_key', {**key_args, 'key': bad_key})
                                    assert bad.is_error
                                for invalid in ({'wait_seconds':0}, {'wait_seconds':31},
                                                {'max_bytes':0}, {'max_bytes':262145}):
                                    bad = await session.call_tool('send_console_key', {
                                        **key_args, 'key':'enter', **invalid})
                                    assert bad.is_error
                                send.assert_not_called()
                                result = payload(await session.call_tool('send_console_key', {**key_args, 'key': key}))
                                assert result['input_status'] == 'sent'
                                send.assert_called_once()
                                assert send.call_args.kwargs['input'] == value, send.call_args
                        observed = payload(await session.call_tool('get_console_output', {
                            'node_id': 'console_echo', 'wait_seconds': .1}))
                        batch_args = {'node_id': 'console_echo', 'expected_revision': observed['revision'],
                                      'expected_cursor': observed['cursor'], 'wait_seconds': .1}
                        with patch.object(lab.automation, 'console_send', wraps=lab.automation.console_send) as send:
                            for commands in ([], ['show clock']*33, ['show clock', ''], ['show clock', 'one\ntwo'],
                                             ['show clock', 'one\\ntwo'], ['show clock', '\x03'],
                                             ['x'*2048]*2, ['é'*1024]*2, ['show clock', 2]):
                                bad = await session.call_tool('send_console_commands', {**batch_args, 'commands': commands})
                                assert bad.is_error, repr(commands)[:100]
                            send.assert_not_called()  # Whole-list validation; no prefix was sent.
                            result = payload(await session.call_tool('send_console_commands', {
                                **batch_args, 'commands': ['enable', 'terminal length 0']}))
                            send.assert_called_once()
                            assert send.call_args.kwargs['input'] == 'enable\rterminal length 0\r'
                            assert result['input_status'] == 'sent' and result['command_completion'] == 'unverified'
                        stale_batch = await session.call_tool('send_console_commands', {
                            **batch_args, 'commands': ['STALE']})
                        assert stale_batch.is_error
                        # Multi-node helper schema and strict preflight are independent
                        # of the unconstrained single-console paste. Give the echo
                        # devices explicit top-level prompts for this check.
                        lab.stop_all()
                        image = fixture.root / 'fake.bin'
                        original_script = image.read_text()
                        image.write_text(original_script.replace("b'BOOT READY\\r\\n'", "b'R# '").replace(
                            "os.write(1, b'RX:' + data)", "os.write(1, b'RX:' + data.replace(b'\\r', b'\\r\\n') + b'\\r\\nR# ')") )
                        lab.save({'name':'Bulk', 'nodes':[fixture.node('bulk_a'), fixture.node('bulk_b')], 'links':[]})
                        targets = []
                        for node in ('bulk_a', 'bulk_b'):
                            lab.start(node)
                            read = payload(await session.call_tool('get_console_output', {'node_id':node, 'wait_seconds':.1}))
                            targets.append({'node_id':node, 'expected_cursor':read['cursor']})
                        bulk_args = {'targets':targets, 'expected_revision':read['revision'],
                                     'commands':['show clock','show version'], 'wait_seconds':.1}
                        with patch.object(lab.automation, 'console_send_bulk', wraps=lab.automation.console_send_bulk) as bulk:
                            for change in ({'commands':['show clock','bad\\r']}, {'targets':[]},
                                           {'targets':[{'node_id':'bulk_a'}]}, {'wait_seconds':6}):
                                rejected = await session.call_tool('send_console_commands_to_nodes', {**bulk_args, **change})
                                assert rejected.is_error
                            bulk.assert_not_called()
                            result = payload(await session.call_tool('send_console_commands_to_nodes', bulk_args))
                            bulk.assert_called_once()
                            assert bulk.call_args.kwargs['input'] == 'show clock\rshow version\r'
                            assert result['input_status'] == 'sent', result
                            assert [r['node_id'] for r in result['results']] == ['bulk_a','bulk_b']
                            assert all('show clock' in r['output'] and 'show version' in r['output'] for r in result['results'])
                        rejected = await session.call_tool('send_console_commands_to_nodes', bulk_args)
                        assert rejected.is_error
                        lab.stop_all()
                        image.write_text(original_script)
                        lab.save({'name': 'Empty', 'nodes': [], 'links': []})
                    if not writable:
                        continue
                    topology = {'name': 'MCP echo', 'nodes': [fixture.node('mcp_echo')], 'links': []}
                    invalid_topology = copy.deepcopy(topology)
                    invalid_topology['nodes'][0]['image'] = 'missing.bin'
                    rejected = await session.call_tool('preview_topology', {'topology': invalid_topology, 'instructions': '# Invalid image'})
                    assert rejected.is_error
                    assert 'Select an IOL' in rejected.content[0].text
                    draft = payload(await session.call_tool('preview_topology', {'topology': topology, 'instructions': '# Echo test\nObserve BOOT READY.'}))
                    assert not lab.topology['nodes']
                    assigned_id = draft['node_id_map']['mcp_echo']
                    assert assigned_id != 'mcp_echo'
                    state = payload(await session.call_tool('apply_topology', {'proposal_id': draft['proposal_id']}))
                    result = payload(await session.call_tool('start_node', {'node_id': assigned_id, 'expected_revision': state['revision']}))
                    assert result['started']
                    assert assigned_id in lab.runtime
                    logs = payload(await session.call_tool('get_node_logs', {'node_id': assigned_id}))
                    assert logs
                    print('MCP schemas, console capability gates, preview, apply, start and logs passed.', flush=True)
                    lab.stop_all()
                    lab.save({'name': 'Model test', 'nodes': [], 'links': []})
                    if args.ollama_url:
                        for name in ('EXOS-VM_33.1.1.31.qcow2', 'vJunosEvolved-26.2R1.7-EVO.qcow2', 'vJunos-switch-26.2R1.7.qcow2'):
                            (lab.image_dir / name).touch()
                        await model_test(session, args, initialization)
                        assert not lab.topology['nodes'], 'Model preview must not mutate the lab'
        # Gateway writes are independently opt-in, without topology/console writes.
        for internet_write in (False, True):
            lab.save({'name': 'Gateway tools', 'nodes': [
                {'id': 'gateway', 'name': 'Internet', 'type': 'cloud'}, fixture.node('router')], 'links': []})
            params = StdioServerParameters(command=sys.executable, args=[str(ROOT/'tools/weblab_mcp.py'),
                '--url', url, '--compact-responses'] + (['--allow-internet-write'] if internet_write else []))
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    names = {t.name for t in (await session.list_tools()).tools}
                    assert ('apply_internet_settings' in names) == internet_write
                    assert ('stop_internet_node' in names) == internet_write
                    assert ('start_internet_node' in names) == internet_write
                    assert 'apply_topology' not in names and 'start_node' not in names and 'send_console_input' not in names
                    page = payload(await session.call_tool('get_internet_settings', {'node_id': 'gateway'}))
                    draft = payload(await session.call_tool('preview_internet_settings', {
                        'node_id': 'gateway', 'internet': internet, 'expected_revision': page['revision']}))
                    candidate = payload(await session.call_tool('get_internet_settings', {
                        'node_id': 'gateway', 'proposal_id': draft['proposal_id']}))
                    assert candidate['internet'] == internet
                    if not internet_write:
                        continue
                    result = payload(await session.call_tool('apply_internet_settings', {'proposal_id': draft['proposal_id']}))
                    assert result['applied'] and not lab.runtime
                    # Lifecycle adapter plumbing uses a recording launcher. Native
                    # gateway suites separately test real Docker/iptables behavior.
                    def start_gateway(node_id):
                        assert node_id == 'gateway'
                        lab.runtime[node_id] = {'cloud': True, 'iol_id': lab.node(node_id)['iol_id']}
                    with patch.object(lab, 'start', side_effect=start_gateway):
                        result = payload(await session.call_tool('start_internet_node', {
                            'node_id': 'gateway', 'expected_revision': result['revision']}))
                    assert result['started'] and result['state']['compact']
                    page = payload(await session.call_tool('get_internet_settings', {'node_id': 'gateway'}))
                    stopped = payload(await session.call_tool('stop_internet_node', {
                        'node_id': 'gateway', 'expected_revision': page['revision'],
                        'expected_runtime_revision': page['runtime_revision']}))
                    assert stopped['stopped'] and not lab.runtime
                    rejected = await session.call_tool('start_internet_node', {
                        'node_id': 'router', 'expected_revision': stopped['revision']})
                    assert rejected.is_error and 'Only Internet' in rejected.content[0].text
        # Large saved snippets must not bury later node identities in the
        # local model's tool-output budget. Exercise real SDK serialization.
        nodes = [{**fixture.node('large_' + str(i)), 'name': 'SW' + str(i + 1),
                  'startup_config': '! saved baseline\n' * 700} for i in range(12)]
        nodes.append({'id': 'gateway', 'name': 'Internet', 'type': 'cloud', 'internet': internet})
        links = [{'id': 'edge_' + str(i), 'a': {'node': nodes[i]['id'], 'port': '0/0'},
                  'b': {'node': nodes[(i+1)%12]['id'], 'port': '0/1'}} for i in range(12)]
        lab.save({'name': 'Large state', 'nodes': nodes, 'links': links})
        for compact in (False, True):
            params = StdioServerParameters(command=sys.executable, args=[str(ROOT/'tools/weblab_mcp.py'),
                '--url', url, '--allow-console-input', '--allow-write'] + (['--compact-responses'] if compact else []))
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    state = payload(await session.call_tool('get_lab_state', {}))
                    if not compact:
                        ordinary = lab.automation.state()
                        # Filesystem free space can change between these calls.
                        assert set(state) == set(ordinary)
                        assert {k:v for k,v in state.items() if k != 'storage'} == {
                            k:v for k,v in ordinary.items() if k != 'storage'}, 'Ordinary/OpenAI state must remain unchanged'
                        assert all(n.get('startup_config') for n in state['topology']['nodes'] if n['type'] != 'cloud')
                        assert state['topology']['nodes'][-1]['internet'] == internet
                        continue
                    found_nodes, found_links = [], []
                    revision = state['revision']
                    for _ in range(30):
                        assert len(json.dumps(state, ensure_ascii=True, indent=2)) <= 8000
                        assert state['revision'] == revision
                        assert all('startup_config' not in n and n['has_startup_config'] == (n['type'] != 'cloud')
                                   for n in state['topology']['nodes'])
                        for n in state['topology']['nodes']:
                            if n['type'] == 'cloud':
                                assert n['internet_settings_omitted'] and n['internet']['port_forwards'] == 1
                        found_nodes.extend(n['id'] for n in state['topology']['nodes'])
                        found_links.extend(n['id'] for n in state['topology']['links'])
                        if state['next_node_offset'] is None and state['next_link_offset'] is None:
                            break
                        state = payload(await session.call_tool('get_lab_state', {
                            'node_offset': state['next_node_offset'] if state['next_node_offset'] is not None else state['total_nodes'],
                            'link_offset': state['next_link_offset'] if state['next_link_offset'] is not None else state['total_links']}))
                    assert found_nodes == [n['id'] for n in nodes]
                    assert found_links == [n['id'] for n in links]
                    invalid = await session.call_tool('get_lab_state', {'node_offset': -1})
                    assert invalid.is_error
                    started = payload(await session.call_tool('start_node', {
                        'node_id': nodes[0]['id'], 'expected_revision': revision}))
                    assert started['started'] and started['state']['compact']
                    assert len(json.dumps(started, ensure_ascii=True, indent=2)) < 9000
                    initial = payload(await session.call_tool('get_console_output', {'node_id': nodes[0]['id'], 'wait_seconds': .1}))
                    probe = 'BEGIN-' + 'x' * 4085 + '-END'
                    reply = payload(await session.call_tool('send_console_command', {
                        'node_id': nodes[0]['id'], 'expected_revision': initial['revision'],
                        'expected_cursor': initial['cursor'], 'command': probe, 'max_bytes': 65536, 'wait_seconds': .2}))
                    output = reply['output']
                    assert reply['capture_max_bytes'] == 1024
                    assert reply['capture_end'] == 'limit'
                    assert reply['evidence']['tail_hint'] == 'incomplete'
                    assert reply['command_completion'] == 'unverified'
                    assert len(json.dumps(reply, ensure_ascii=True, indent=2)) < 8000
                    while reply['capture_end'] == 'limit':
                        reply = payload(await session.call_tool('get_console_output', {
                            'node_id': nodes[0]['id'], 'cursor': reply['cursor'], 'max_bytes': 65536, 'wait_seconds': .1}))
                        assert reply['omitted_bytes'] == 0
                        assert reply['evidence']['scope'] == 'this_capture_only'
                        assert len(json.dumps(reply, ensure_ascii=True, indent=2)) < 8000
                        output += reply['output']
                    # Echo fixture prefixes every PTY read, which may split the
                    # maximum-size input into multiple transport packets.
                    assert output.startswith('RX:') and output.replace('RX:', '') == probe, (
                        'Console continuation lost or duplicated output', len(output), output[:30], output[-30:])
                    escaped = payload(await session.call_tool('send_console_input', {
                        'node_id': nodes[0]['id'], 'expected_revision': reply['revision'],
                        'expected_cursor': reply['cursor'], 'input': '\x01' * 1024,
                        'max_bytes': 1024, 'wait_seconds': .2}))
                    assert len(json.dumps(escaped, ensure_ascii=True, indent=2)) <= 8000
                    escaped_output = escaped['output']
                    while escaped['capture_end'] == 'limit':
                        escaped = payload(await session.call_tool('get_console_output', {
                            'node_id': nodes[0]['id'], 'cursor': escaped['cursor'], 'wait_seconds': .1}))
                        escaped_output += escaped['output']
                    assert escaped_output.replace('RX:', '') == '\x01' * 1024
                    lab.stop_all()
                    proposal = payload(await session.call_tool('preview_topology', {
                        'topology': {'name': 'Compact apply', 'nodes': [fixture.node('replacement')], 'links': []},
                        'instructions': '# Disposable test', 'replace_existing': True}))
                    applied = payload(await session.call_tool('apply_topology', {'proposal_id': proposal['proposal_id']}))
                    assert applied['compact'] and applied['complete']
                    oversized = copy.deepcopy(internet)
                    oversized['firewall']['rules'] *= 64
                    oversized['port_forwards'] = [
                        {**internet['port_forwards'][0], 'external_port': 22022 + i} for i in range(32)]
                    lab.save({'name': 'Large gateway settings', 'nodes': [
                        {'id': 'gateway', 'name': 'Internet', 'type': 'cloud', 'internet': oversized}], 'links': []})
                    large_state = payload(await session.call_tool('get_lab_state'))
                    assert large_state['complete'] and len(json.dumps(large_state, ensure_ascii=True, indent=2)) <= 8000
                    offsets, found_rules, found_forwards, current = {}, [], [], None
                    for _ in range(20):
                        page = payload(await session.call_tool('get_internet_settings', {
                            'node_id': 'gateway', 'expected_revision': current, 'page_size': 8, **offsets}))
                        assert len(json.dumps(page, ensure_ascii=True, indent=2)) <= 8000
                        current = page['revision']
                        found_rules += page['internet']['firewall']['rules']
                        found_forwards += page['internet']['port_forwards']
                        if all(v is None for v in page['next_offsets'].values()):
                            break
                        offsets = {arg: page['next_offsets'][key] if page['next_offsets'][key] is not None else page['totals'][key]
                                   for arg, key in [('route_offset', 'routes'), ('rule_offset', 'rules'), ('forward_offset', 'port_forwards')]}
                    assert found_rules == oversized['firewall']['rules'] and found_forwards == oversized['port_forwards']
                    # Eight real PTYs plus heavily escaped output still fit the
                    # compact result budget, preserving every per-node cursor.
                    image = fixture.root / 'fake.bin'
                    original_script = image.read_text()
                    image.write_text(original_script.replace("b'BOOT READY\\r\\n'", "b'R# '").replace(
                        "os.write(1, b'RX:' + data)", "os.write(1, b'\\x01' * 128 + b'\\r\\nR# ')") )
                    lab.save({'name':'Compact bulk', 'nodes':[fixture.node('bulk_' + str(i)) for i in range(8)], 'links':[]})
                    targets = []
                    for node in lab.topology['nodes']:
                        lab.start(node['id'])
                        read = payload(await session.call_tool('get_console_output', {'node_id':node['id'], 'wait_seconds':.1}))
                        targets.append({'node_id':node['id'], 'expected_cursor':read['cursor']})
                    result = payload(await session.call_tool('send_console_commands_to_nodes', {
                        'targets':targets, 'expected_revision':read['revision'], 'commands':['show version'], 'wait_seconds':.1}))
                    assert result['input_status'] == 'sent', result
                    assert len(result['results']) == 8
                    assert result['capture_max_bytes_per_node'] == 64
                    assert len(json.dumps(result, ensure_ascii=True, indent=2)) <= 8000
                    for capture in result['results']:
                        assert capture['capture_end'] == 'limit' and capture['bytes_read'] == 64
                        remainder = payload(await session.call_tool('get_console_output', {
                            'node_id':capture['node_id'], 'cursor':capture['cursor'], 'wait_seconds':.1}))
                        assert capture['output'] + remainder['output'] == '\x01' * 128 + '\nR#'
                    lab.stop_all()
                    image.write_text(original_script)
        print('Compact state pagination preserves every node/link; console chunks roundtrip without loss; ordinary state unchanged.', flush=True)
    finally:
        fixture.tearDown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ollama-url')
    parser.add_argument('--model')
    parser.add_argument('--output', type=Path, help='Optional local test artifacts; do not publish personal transcripts')
    args = parser.parse_args()
    if args.ollama_url and not args.model:
        parser.error('--model is required with --ollama-url')
    asyncio.run(main(args))
