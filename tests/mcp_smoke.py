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


async def main(args):
    fixture = test_lab.LabTests()
    fixture.setUp()
    try:
        lab = fixture.lab
        lab.save({'name': 'Empty', 'nodes': [], 'links': []})
        lab.automation = lab_automation.Automation(lab, lab_server.LabError, lab_server.ports, lab_server.run)
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
                    assert 'get_console_output' in names
                    assert ('send_console_input' in names) == console_input
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
                        stale = await session.call_tool('send_console_input', {
                            'node_id': 'console_echo', 'input': 'STALE\r',
                            'expected_revision': observed['revision'], 'expected_cursor': observed['cursor']})
                        assert stale.is_error
                        lab.stop_all()
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
