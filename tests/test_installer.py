"""Execute the POSIX installer against a recording Docker/host fixture, never the lab."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAKE = r'''
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
a = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as f:
    f.write(json.dumps([name, *a]) + '\n')
def emit(key, default=''):
    print(os.environ.get(key, default))
if name == 'id': emit('UID_VALUE', '0')
elif name == 'uname': print('Linux' if a == ['-s'] else os.environ.get('ARCH', 'x86_64'))
elif name == 'ss': emit('LISTENERS')
elif name == 'curl':
    if os.environ.get('CURL_FAIL'): sys.exit(22)
    pathlib.Path(a[a.index('-o')+1]).write_text('example')
elif name == 'docker':
    if a[:2] == ['context', 'inspect']: emit('ENDPOINT', 'unix:///var/run/docker.sock')
    elif a[:1] == ['info']:
        emit('SECURITY', '[]') if 'SecurityOptions' in a[-1] else emit('PLATFORM', 'linux/x86_64')
    elif a[:2] == ['container', 'ls']:
        if os.environ.get('QUERY_FAIL'): sys.exit(1)
        emit('CONTAINERS')
    elif a[:2] == ['volume', 'ls']: emit('VOLUMES')
    elif a[:2] == ['image', 'inspect']: sys.exit(0 if os.environ.get('ALPINE_PRESENT') else 1)
    elif a[:1] == ['pull'] and os.environ.get('PULL_FAIL'): sys.exit(1)
    elif a[:1] == ['run'] and os.environ.get('RUN_FAIL') and '--network' in a and a[a.index('--network')+1]=='host': sys.exit(1)
    elif a[:1] == ['exec'] and os.environ.get('HEALTH_FAIL'): sys.exit(1)
'''


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        helper = self.bin / 'fixture'
        helper.write_text('#!' + sys.executable + '\n' + FAKE)
        helper.chmod(0o755)
        for name in ('docker', 'id', 'uname', 'ss', 'curl', 'modprobe'):
            (self.bin / name).symlink_to(helper)
        # Simulate kernel/device availability without requiring root or mknod in CI.
        source = (ROOT / 'weblab-install.sh').read_text()
        source = source.replace('[ -c /dev/net/tun ]', '[ "${TEST_TUN:-1}" = 1 ]')
        source = source.replace('[ -c /dev/kvm ]', '[ "${TEST_KVM:-1}" = 1 ]')
        source = source.replace('[ ! -d /sys/module/openvswitch ]', '[ "${TEST_OVS:-1}" = 0 ]')
        self.script = self.root / 'install.sh'
        self.script.write_text(source)
        self.log = self.root / 'calls'
        self.env = {**os.environ, 'PATH': str(self.bin)+':'+os.environ['PATH'], 'CALLS': str(self.log)}
        self.env.pop('DOCKER_HOST', None)
        self.env.pop('DOCKER_CONTEXT', None)

    def run_script(self, *args, **env):
        self.log.write_text('')
        result = subprocess.run(['sh', str(self.script), *args], cwd=self.root,
                                env={**self.env, **env}, text=True, capture_output=True)
        self.calls = [json.loads(x) for x in self.log.read_text().splitlines()]
        return result

    def assert_no_mutations(self):
        self.assertFalse([c for c in self.calls if c[:2] in (['docker', 'pull'], ['docker', 'run'])])

    def test_default_and_agent_isolation(self):
        for options in ([], ['--with-agent', '--with-kvm']):
            with self.subTest(options=options):
                result = self.run_script(*options, '--name', 'test-lab', '--port', '18080')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(['docker', 'pull', 'ghcr.io/weblab-network/ll2s:0.2.0-ovs-rstp1'], self.calls)
                runs = [c for c in self.calls if c[:2] == ['docker', 'run']]
                core = runs[-1]
                self.assertIn('WL_PORT=18080', core)
                self.assertIn('WL_BIND=127.0.0.1', core)
                self.assertIn('test-lab-data:/data', core)
                self.assertNotIn('--privileged', core)
                self.assertNotIn('--dns', core)
                self.assertEqual(len(runs), 2 if options else 1)
                if options:
                    agent = runs[0]
                    self.assertEqual(agent[agent.index('--network')+1], 'none')
                    for arg in ('10001:10001', '--read-only', 'ALL', 'no-new-privileges:true'):
                        self.assertIn(arg, agent)
                    self.assertFalse(any('docker.sock' in a or ':/data' in a or ':/iou' in a for a in agent))
                    self.assertIn('/dev/kvm', core)
                    self.assertEqual(core[-1].split(':')[-1], agent[-1].split(':')[-1])
                else:
                    self.assertFalse(any('WL_AGENT' in a for a in core))
                    self.assertNotIn('/dev/kvm', core)

    def test_preflight_failures_do_not_pull_or_start(self):
        for env in ({'CONTAINERS': 'weblab'}, {'CONTAINERS': 'weblab-agent'},
                    {'VOLUMES': 'weblab-data'}, {'VOLUMES': 'weblab-agent-conversations'},
                    {'DOCKER_HOST': 'ssh://remote'}, {'DOCKER_CONTEXT': 'remote'},
                    {'ENDPOINT': 'ssh://remote'}, {'SECURITY': '["name=rootless"]'},
                    {'PLATFORM': 'linux/aarch64'}, {'ARCH': 'aarch64'},
                    {'UID_VALUE': '1000'}, {'TEST_TUN': '0'}, {'LISTENERS': 'LISTEN'},
                    {'QUERY_FAIL': '1'}):
            with self.subTest(env=env):
                self.assertNotEqual(self.run_script(**env).returncode, 0)
                self.assert_no_mutations()
        self.assertNotEqual(self.run_script('--with-kvm', TEST_KVM='0').returncode, 0)
        self.assert_no_mutations()

    def test_invalid_arguments_and_dry_run(self):
        for args in (['--name', '../oops'], ['--port', '08080'], ['--port', '65536'],
                     ['--port', '80'], ['--port', '1;false'], ['--name'], ['--unknown']):
            self.assertNotEqual(self.run_script(*args).returncode, 0)
            self.assertEqual(self.calls, [])
        for args in (['--dry-run'], ['--help']):
            self.assertEqual(self.run_script(*args).returncode, 0)
            self.assertEqual(self.calls, [])

    def test_examples_download_and_existing_destination(self):
        result = self.run_script('--examples-dir', 'exercise with spaces')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root/'exercise with spaces/ll2s-frr-vrrp-test.json').read_text(), 'example')
        downloads = [c for c in self.calls if c[0]=='curl']
        self.assertEqual(len(downloads), 2)
        self.assertTrue(all('--fail' in c and '--proto-redir' in c for c in downloads))
        self.assertNotEqual(self.run_script('--examples-dir', 'exercise with spaces').returncode, 0)
        self.assert_no_mutations()
        self.assertNotEqual(self.run_script('--examples-dir', 'other', CURL_FAIL='1').returncode, 0)
        self.assert_no_mutations()
        self.assertFalse((self.root/'other').exists())

    def test_internet_helper_is_optional_and_matches_core_version(self):
        result = self.run_script('--with-internet')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(['docker', 'pull', 'ghcr.io/weblab-network/weblab-internet:0.11.0'], self.calls)
        core = next(call for call in self.calls if call[:2] == ['docker', 'run'] and '--network' in call and call[call.index('--network')+1] == 'host')
        self.assertIn('WL_INTERNET_IMAGE=ghcr.io/weblab-network/weblab-internet:0.11.0', core)
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call[:2] == ['docker', 'pull'] and 'weblab-internet' in call[-1] for call in self.calls))

    def test_failures_preserve_state_and_never_report_ready(self):
        for env in ({'PULL_FAIL': '1'}, {'RUN_FAIL': '1'}, {'HEALTH_FAIL': '1'}):
            with self.subTest(env=env):
                result = self.run_script('--with-agent', **env)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('Weblab is ready:', result.stdout)
                self.assertFalse(any('rm' in c or 'prune' in c for c in self.calls))
                if 'PULL_FAIL' not in env:
                    self.assertIn('preserved', result.stderr)

    def test_auth_keys_bind_and_preload(self):
        result = self.run_script('--with-auth', '--with-agent', '--start-example')
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.root/'weblab-auth.env'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        values = dict(line.split('=', 1) for line in path.read_text().splitlines())
        keys = [values['WL_AUTH_'+a+'_PASSWORD'] for a in ('ADMIN', 'USER', 'GUEST')]
        self.assertEqual(len(set(keys)), 3)
        self.assertTrue(all(len(k) >= 32 for k in keys))
        self.assertTrue(all(k not in result.stdout+result.stderr for k in keys))
        runs = [c for c in self.calls if c[:2] == ['docker', 'run']]
        self.assertIn('WL_BIND=0.0.0.0', runs[-1])
        self.assertIn('--env-file', runs[-1])
        self.assertNotIn('--env-file', runs[0])
        self.assertTrue(any(c[:2] == ['docker', 'cp'] for c in self.calls))
        self.assertTrue(any('/api/lab/start' in a for c in self.calls for a in c))
        self.assertNotEqual(self.run_script('--with-auth').returncode, 0)
        self.assert_no_mutations()
        self.assertNotEqual(self.run_script('--auth-file', 'keys').returncode, 0)
        self.assertEqual(self.calls, [])

    def test_preload_download_failure_precedes_creation(self):
        self.assertNotEqual(self.run_script('--start-example', CURL_FAIL='1').returncode, 0)
        self.assert_no_mutations()
        result = self.run_script('--start-example', '--dry-run')
        self.assertEqual(result.returncode, 0)
        self.assertIn('3 routers', result.stdout)
        self.assertEqual(self.calls, [])

    def test_preload_reports_server_error_without_retry(self):
        import io
        import urllib.error
        from unittest.mock import Mock
        # Execute the actual embedded client helper without making network calls.
        source = (ROOT / 'weblab-install.sh').read_text()
        helper = source.split("import http.cookiejar,", 1)[1].split(
            'if os.environ.get("WL_AUTH")', 1)[0]
        scope = {}
        with unittest.mock.patch.object(sys, 'argv', ['installer', '18080']):
            exec('import http.cookiejar,' + helper, scope)
        for path, body, expected in (
            ('/api/lab/start', b'{"error":"FRR daemons did not become ready"}',
             'FRR daemons did not become ready'),
            ('/api/lab/start', b'<html>unavailable</html>', 'HTTP 400'),
            ('/api/auth/login', b'{"error":"sensitive login detail"}', 'HTTP 400'),
        ):
            with self.subTest(path=path, body=body):
                opener = Mock()
                opener.open.side_effect = urllib.error.HTTPError(
                    'http://localhost' + path, 400, 'Bad Request', {}, io.BytesIO(body))
                scope['opener'] = opener
                with self.assertRaises(SystemExit) as caught:
                    scope['request'](path, {}, 'POST')
                message = str(caught.exception)
                self.assertIn('POST ' + path, message)
                self.assertIn(expected, message)
                self.assertIn('preserved', message)
                self.assertNotIn('sensitive login detail', message)
                opener.open.assert_called_once()
                self.assertEqual(opener.open.call_args.kwargs["timeout"],
                                 1800 if path == "/api/lab/start" else 600)

    def test_existing_alpine_tools_tag_is_preserved(self):
        self.assertEqual(self.run_script(ALPINE_PRESENT='1').returncode, 0)
        self.assertNotIn(['docker', 'pull', 'alpine:latest'], self.calls)

    def test_dns_override_is_core_only(self):
        for address in ('127.0.0.199', '192.0.2.53'):
            with self.subTest(address=address):
                result = self.run_script('--with-agent', '--dns', address)
                self.assertEqual(result.returncode, 0, result.stderr)
                runs = [c for c in self.calls if c[:2] == ['docker', 'run']]
                self.assertNotIn('--dns', runs[0])
                core = runs[-1]
                self.assertEqual(core[core.index('--dns')+1], address)
                self.assertIn('Core DNS override: '+address, result.stdout)
        result = self.run_script('--dns', '127.0.0.199', '--dry-run')
        self.assertEqual(result.returncode, 0)
        self.assertIn('Core DNS override: 127.0.0.199', result.stdout)
        self.assertEqual(self.calls, [])

    def test_invalid_dns_fails_before_host_operations(self):
        for address in ('resolver.local', '256.0.0.1', '127.0.0', '127.0.0.1.2',
                        '127..0.1', '.127.0.0.1', '127.0.0.1.', '127.00.0.1',
                        '127.0.0.1;false', '--privileged', '127.0.0.1 8.8.8.8'):
            with self.subTest(address=address):
                self.assertNotEqual(self.run_script('--dns', address).returncode, 0)
                self.assertEqual(self.calls, [])
        self.assertNotEqual(self.run_script('--dns').returncode, 0)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
