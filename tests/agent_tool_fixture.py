"""In-container fake model for agent_native --tool-gateway-only; never uses auth.

Exercises packaged Codex Code Mode plus the real Weblab MCP adapter/gateway.
Only the disposable test container mounts this file. Not a production module.
"""
import json
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, '/app')
import agent_companion


class Model(BaseHTTPRequestHandler):
    calls = 0

    def log_message(self, *_):
        pass

    def do_POST(self):
        try:
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert self.path == '/v1/responses'
            assert not self.headers.get('Authorization'), 'Fixture must not receive credentials'
            if not Model.calls:
                content=[part for item in data['input'] if item.get('role')=='user' for part in item.get('content',[]) if isinstance(part,dict)]
                assert any('ATTACHMENT_TRANSPORT_PROBE' in part.get('text','') for part in content), 'Text attachment did not reach provider'
                assert any(part.get('type')=='input_image' and part.get('image_url','').startswith('data:image/') for part in content), 'Image attachment did not reach provider'
                # New Codex models advertise tools through additional_tools input items.
                advertised = json.dumps([i for i in data['input'] if i.get('type') == 'additional_tools'])
                assert '"name": "exec"' in advertised, 'Code Mode gateway was not offered'
                item = {'type':'custom_tool_call', 'id':'call_fixture', 'call_id':'call_fixture',
                        'name':'exec', 'namespace':'functions', 'input': (
                            'text({names: ALL_TOOLS.map(t=>t.name), shell:typeof tools.exec_command, '
                            'patch:typeof tools.apply_patch, process:typeof process, fetch:typeof fetch, '
                            'require:typeof require}); '
                            'text(await tools.mcp__weblab__get_lab_state({}));'
                            r'text({commandValidation:await tools.mcp__weblab__send_console_command({node_id:"missing", command:"show clock\\r", expected_revision:"unused", expected_cursor:"unused"})});')}
            else:
                assert Model.calls == 1
                outputs = [i for i in data['input'] if i.get('type') == 'custom_tool_call_output']
                assert outputs, 'No Code Mode output returned'
                text = '\n'.join(p.get('text','') for i in outputs for p in i['output'] if isinstance(p,dict))
                objects = []
                for line in text.splitlines():
                    try: objects.append(json.loads(line))
                    except ValueError: pass
                exposure = next(o for o in objects if isinstance(o,dict) and 'names' in o)
                assert exposure['names'] and all(n.startswith('mcp__weblab__') for n in exposure['names'])
                assert 'mcp__weblab__send_console_command' in exposure['names']
                assert all(exposure[k] == 'undefined' for k in ('shell','patch','process','fetch','require'))
                result = next(o for o in objects if isinstance(o,dict) and 'content' in o)
                assert not result.get('isError'), result
                payload = json.loads(next(c['text'] for c in result['content'] if c.get('type') == 'text'))
                assert payload['topology']['nodes'] == []
                rejected = next(o['commandValidation'] for o in objects if isinstance(o,dict) and 'commandValidation' in o)
                assert rejected.get('isError'), rejected
                assert 'omit Enter escapes' in json.dumps(rejected), rejected
                item = {'id':'msg_fixture','type':'message','role':'assistant','status':'completed',
                        'content':[{'type':'output_text','text':'Fixture verified: Code Mode read the empty Weblab topology; only Weblab tools are callable.','annotations':[]}]}
            Model.calls += 1
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream')
            self.end_headers()
            for event in [
                {'type':'response.created','response':{'id':'resp_fixture','status':'in_progress','output':[]}},
                {'type':'response.output_item.done','output_index':0,'item':item},
                {'type':'response.completed','response':{'id':'resp_fixture','status':'completed','output':[item],
                  'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}}]:
                self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode())
            self.wfile.flush()
            self.close_connection = True
        except Exception as exc:
            print('Fixture failure: '+str(exc), flush=True)
            self.send_error(500)


def main():
    catalog = json.loads(subprocess.check_output(['codex','debug','models','--bundled']))
    model = next(m for m in catalog['models'] if m['slug'] == 'gpt-5.6-sol')
    assert model['tool_mode'] == 'code_mode_only'
    Path('/tmp/tool-model.json').write_text(json.dumps({'models':[model]}))
    server = ThreadingHTTPServer(('127.0.0.1',0),Model)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    original = agent_companion.RPC

    class FixtureRPC(original):
        def __init__(self, config, env=None):
            config = {**config, 'model_provider':'fixture', 'model_catalog_json':'/tmp/tool-model.json',
                      'model_providers.fixture.name':'offline fixture',
                      'model_providers.fixture.base_url':f'http://127.0.0.1:{server.server_port}/v1',
                      'model_providers.fixture.wire_api':'responses',
                      'model_providers.fixture.requires_openai_auth':False,
                      'model_providers.fixture.supports_websockets':False,
                      'model_providers.fixture.request_max_retries':0,
                      'features.enable_request_compression':False}
            super().__init__(config,env=env)

        def call(self, method, params, **kwargs):
            if method == 'account/read':
                return {'account':{'type':'chatgpt'}}  # Fake account, disposable home, no tokens.
            if method in ('thread/start','thread/resume'):
                params = {**params,'modelProvider':'fixture'}
            return super().call(method,params,**kwargs)

    agent_companion.RPC = FixtureRPC
    agent_companion.main()


if __name__ == '__main__':
    main()
