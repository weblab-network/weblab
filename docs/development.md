# Development and testing

[Help index](README.md) · [Project overview](../README.md)

## Source layout

| Path | Purpose |
| --- | --- |
| `lab_server.py` | HTTP API, validation, topology persistence, process/resource lifecycle |
| `container_console.py` | Alpine/FRR/LL2S console recovery and vtysh-to-shell access without container restart |
| `disk_delta.py` | Lossless vEOS backup block references to checksum-verified base images |
| `ll2s_device.py` | Independent LL2S/OVS switch container, passthru ports and saved startup configuration |
| `frr.py`, `tap_net.py` | Pinned FRR container lifecycle, saved configuration and multiport TAP fabric adapter |
| `lab_backup.py` | ZIP validation, export, staged restore and recovery |
| `storage.py`, `web/storage.js` | Metadata-only usage reports, capacity preflights and storage dialog |
| `vios.py`, `qemu_net.py`, `qmp.py` | QEMU profiles, Ethernet transport and carrier control |
| `link_fabric.py`, `iol_l1.py` | Directional frame loss and supported IOL carrier signaling |
| `initial_config.py`, `saved_config.py` | Fresh-device seeds and saved Cisco config extraction |
| `console_capture.py`, `cisco_config.py` | Shared console capture workflow and Cisco commands/parsing |
| `wrapper-ws.pl` | PTY, shared console WebSockets, input locks and transcripts |
| `iou2net.pl` | IOU-to-TAP bridge used for Alpine PCs |
| `web/` | Plain JS/CSS workspace and locally vendored browser assets |
| `tools/validate_topology.py` | Read-only topology JSON validator |
| `examples/` | Original starter and OSPF exercises |
| `tests/` | Backend, browser and opt-in image integration checks |

There is no frontend build step and the core server has no third-party Python package dependency.
The optional external MCP adapter has a separate SDK environment.
Docker copies source into the image; rebuild/recreate to deploy changes. Never
use an active lab's data directory as a test fixture. Do not read active guest
storage or bypass QEMU disk locks. Run node-launching suites sequentially.

## Backend tests

From the repository root on Linux, with Python 3.11+ and Perl installed:

```sh
python3 -m unittest discover -s tests -v
```

The default suite uses disposable fake device processes, real PTYs/WebSockets,
local sockets and temporary storage. It does not require vendor images or Docker.
Some QEMU/disk checks run only when `qemu-system-x86`, `qemu-img`, `qemu-io`, mtools
and gzip are available; inspect skipped tests rather than treating a partial run
as full integration validation.

## Browser tests

Install Node.js 18+ and the pinned development dependency, then Chromium:

```sh
npm ci --prefix tests
cd tests
npx playwright install chromium
cd ..
node tests/console_open_mode.cjs
node tests/topology_windows.cjs
node tests/topology_multiselect.cjs
node tests/ll2s_ui.cjs
node tests/junos_ui.cjs
node tests/iol_l1_ui.cjs
node tests/console_clipboard.cjs
node tests/instructions.cjs
node tests/workspace_features.cjs
node tests/storage_ui.cjs
```

Linux browsers also need system libraries; Playwright's `install --with-deps
chromium` can install them with the appropriate privileges. These are test-only
dependencies; users do not need Node or Playwright to run Weblab. An existing
Playwright installation can be selected with `PLAYWRIGHT_MODULE=/absolute/path/to/playwright`.

Run the `.cjs` regression suites sequentially from the repository root. Files
ending `_ui.cjs` create synthetic disk/image fixtures; install QEMU utilities for
those checks. `console_ui.cjs` and `toolbar_ui.cjs` are helpers, not test runners.
Most browser suites launch their own disposable server and echo devices.

`browser_smoke.cjs` is an exception: it requires an explicitly supplied `LAB_URL`
and modifies/stops/starts that lab. Use only a disposable server. Native image
and Android suites below are also opt-in; do not run every `.cjs` indiscriminately.

## Android emulator check

Start an ADB server and an emulator with Chrome. The optional test restarts Chrome
on connected emulator devices; it uses disposable echo nodes and exercises real
touch controls plus document/terminal selection hooks, not every native OS menu.

```sh
ADB_HOST=127.0.0.1 ADB_PORT=5037 node tests/console_clipboard_android.cjs
```

For a remote ADB server, forward its port to loopback and set `ADB_PORT` to the
forwarded port. Both emulators can share one ADB connection. Avoid exposing ADB
to an untrusted network.

## Native image checks

These need user-supplied vendor images and any required licenses. Never bundle
user-supplied assets in tests, CI or a release. Read each script's header before running.
The optional EXOS distribution is a separate, explicitly pinned exception; see
[container images](containers.md). Native tests create disposable nodes and must run sequentially with sufficient RAM and
free Docker provisioning subnets/application IDs.

| Script | Additional requirements / coverage |
| --- | --- |
| `tests/ll2s_native.py` | Root, Docker/TAP, local LL2S 0.2.0 and Alpine, host OVS module; RSTP triangle, VLAN isolation, carrier, shared CLI and saved ZIP restore (reserves IDs 900–905) |
| `tests/iol_l1_native.py` | Root, private mount namespace, exact IOL router/L2 profiles and local iourc; launcher/API carrier and ping |
| `tests/iol_l1_switching_native.py` | Same, two switches; repeated stop/export/start and STP (not archive restoration) |
| `tests/iol_vlan_backup_native.py` | Root, IOL L2 image and supplied license; delete source storage, restore ZIP under different IDs, verify VLANs/STP/ping |
| `tests/iol_l1_experiment.py` | Low-level isolated L1 signal diagnostic; not the managed application |
| `tests/link_faults_native.cjs` | `VIOS_IMAGE`, `IOL_SWITCH_IMAGE`, `IOL_LICENSE`, KVM, Docker/TAP; mixed forwarding and faults |
| `tests/exos_native.cjs` | `EXOS_IMAGE`, KVM, Docker/TAP, Alpine; EXOS boot/console/forwarding |
| `tests/exos_cpu_native.py IMAGE` | Root, KVM, QEMU and EXOS 33.1; reproduce AMD CPU-name boot failure, then verify the compatible profile |
| `tests/veos_native.cjs` | `VEOS_IMAGE` and Aboot beside it, KVM, Docker/TAP, Alpine; vEOS boot/console/forwarding |

For example, with your own license and IOL L2 image installed:

```sh
IOURC=/path/to/iourc python3 tests/iol_vlan_backup_native.py
```

The older L1 diagnostics expect `images/iourc` and isolate their `/etc/hosts` view
as described in their headers. Do not interpret a fixture's environment-specific
boot error as a failed protocol or restore test. Report what actually ran.

## Examples and browser assets

```sh
python3 tools/validate_topology.py examples/starter.json
python3 tools/validate_topology.py examples/ospf-practice.json
```

Add `--image-dir images` to check installed filenames too. Validation does not
start devices or establish IOS command/protocol correctness.

Browser assets are shipped in `web/vendor/` with their licenses. `fetch_assets.py`
restores the pinned versions; it needs network access. Preserve those notices.

## Standalone consoles

The main app uses `wrapper-ws.pl`. The standalone `wrapper.pl` TCP console and
`iol-console.html` WebSocket client remain available for manual use. The standalone
HTML page uses CDN assets; the main workspace uses locally vendored assets.

For a separate manual lab, create an appropriate NETMAP (see
[the minimal example](../examples/NETMAP)) and use free application IDs/ports:

```sh
./wrapper.pl -m ./images/router.bin -p 2010 -- -e 2 -m 1024 -n 64 10
./wrapper-ws.pl --bind 127.0.0.1 --path /console -m ./images/switch.bin -p 8020 -- -e 2 -m 1024 -n 64 20
```

Replace the filenames with your installed images. These commands are independent
of managed nodes and use the working directory's files. Do not reuse managed IDs,
ports or storage. The main server serves the standalone client at `/iol-console.html`.

## Contribution checks

Describe the behavior changed and its validation. Use small, reproducible labs
and preserve console sessions, input locks, saved-state precedence and transaction
rollback when changing related code. Keep runtime files, logs, credentials,
images, licenses and personal exercises out of commits. Vendor handlers must be
explicit; do not send Cisco capture commands to other vendors.

Before sharing logs or screenshots, remove private configuration. Licensing and
component provenance are tracked in [third-party notices](../THIRD_PARTY.md);
do not silently add a license to a component whose distribution terms are unresolved.

## Building the optional EXOS edition

The standard `Dockerfile` still excludes vendor images. The separate demo build
uses the pinned public EXOS release and the committed topology JSON. Packaging
requires Docker and Python; it does not require KVM or boot guest devices:

```sh
python3 tools/fetch_exos_demo_image.py packaging/exos/build
python3 tools/validate_topology.py examples/exos-demo.json --image-dir packaging/exos/build
docker build -t weblab-base:local .
docker build -f packaging/exos/Dockerfile \
  --build-arg WEBLAB_IMAGE=weblab-base:local -t weblab-exos:local .
python3 tools/smoke_exos_container.py weblab-exos:local
```

Use current Docker with BuildKit for the Dockerfile-specific context allowlist.
The packaged smoke test checks five stopped nodes, fresh storage, and preservation
of edits across container recreation. It does not test guest boot or protocols.

For a separate, opt-in saved-state integration test on a capable KVM host,
`tools/build_exos_demo.py IMAGE OUTPUT.zip` configures fresh switches, saves their
disks, restores the ZIP into a second disposable lab and checks traffic between
both PCs. It needs root, Docker, Alpine and the native dependencies above. Run it
sequentially with other native suites. Its ZIP is a test output, not part of the
container distribution.
`packaging/exos/build/` is ignored by Git; never force-add its binary artifacts.
The normal EXOS profile still rejects generic `startup_config` snippets.

## Updating the GitHub wiki

Edit the help in `docs/` so downloaded source retains an offline copy. Render
those pages into a separate wiki checkout, review the diff, then commit and push:

```sh
git clone git@github.com:weblab-network/weblab.wiki.git ../weblab.wiki
python3 tools/export_wiki.py ../weblab.wiki
git -C ../weblab.wiki diff
```

The exporter converts help links to wiki URLs and keeps example/source links
pointing to the main repository. It also generates the sidebar and footer. It
does not commit or push. After adding a help chapter, add it to the exporter's
page map. GitHub requires an initial wiki page to exist before cloning its Git
repository.


## FRR integration tests

The default backend suite tests FRR validation, archive integrity and partial
startup rollback without Docker or root. `node tests/frr_ui.cjs` tests the image
selector, ports and export options without starting routers.

Opt-in native test (root, local Docker, TAP permissions; no KVM):

```sh
docker pull quay.io/frrouting/frr:10.7.1
docker pull alpine:latest
python3 tests/frr_native.py
```

Run it sequentially with other native tests. It uses disposable FRR/Alpine nodes
to check OSPFv2/OSPFv3/BGP, IPv4/IPv6 routing, PC forwarding, console input locks,
traffic/carrier faults, write-memory persistence, ZIP restore and crash recovery. It never replaces the active lab.

For a separate FRR–IOSv OSPF interoperability test with a user-supplied image and
KVM, run (sequentially):

```sh
python3 tests/frr_mixed_native.py --image-dir images --iosv-image cisco_vios-159-3.M12.qcow2
```

This boots a fresh disposable IOSv router, waits for OSPF, checks a learned
loopback route and pings it from FRR. It does not alter existing lab storage.


For VRRP, interactive FRR shell access and untested-image policy, run separately:

```sh
python3 tests/frr_vrrp_native.py
```

This isolated test checks VRRPv3 election, failover and virtual-IP reachability
across VLAN 10; vtysh/shell transitions and logout recovery; and a temporary
committed image with default denial, explicit opt-in, warning and ZIP identity.
It creates and removes its own tag without replacing the installed FRR tag.
Linux interfaces are configured by the test, not automatically restored by Weblab.

`test_qemu_console.py` checks the serial settings of every QEMU profile, then
uses a disposable real QEMU process (TCG, paused CPUs) and its virtual UART to
verify Ctrl+C, Ctrl+Z and Ctrl+backslash through the PTY/WebSocket wrapper across
raw/v1/v2 protocols. It also checks that host termination still stops QEMU.
No vendor image, guest boot, KVM or active lab is used; the UART test is skipped
when QEMU is unavailable.

## LL2S discovery and monitoring checks

With `ghcr.io/weblab-network/ll2s:0.2.0` pulled on the Docker host,
run this separately from other node-launching suites:

```sh
sudo modprobe openvswitch
python3 tests/ll2s_native.py --monitoring
```

This adds exact LLDP neighbor/port checks and real SNMPv2c polling across the
management VLAN to the existing RSTP/VLAN/console test. It also verifies discovery,
management addressing and the community after a stopped ZIP restore, including
the saved hostname instead of an unsaved change. All nodes use disposable data.
Without `--monitoring`, the original switching/persistence test remains available
for older LL2S builds. Neither test changes an active lab.

For missing-container startup recovery, run separately:

```sh
python3 tests/ll2s_native.py --recovery-only
```

This uses a disposable switch and emulates lost containers/TAPs with surviving
Docker networks, for both current and older journals. It verifies unchanged host
saved files and a successful restart reusing the same subnets. It does not reboot
the host. Unit regressions also cover foreign resources, attached endpoints,
Docker failures, configuration copy failures and repeated partial cleanup.

## Optional MCP adapter checks

Enable/install the adapter as described in [AI assistants and MCP](automation.md).
The ordinary backend suite covers opt-in, validation, stale proposals, retained
storage, HTTP downloads and echo-device starts. `test_console_automation.py`
checks console reads/input, cursor replay and truncation, lock ownership, stale
history/restarts, bounded captures, disconnections and HTTP opt-in using real
disposable PTYs. For actual STDIO MCP transport:

```sh
.venv-mcp/bin/python tests/mcp_smoke.py
```

This uses a disposable server and PTY echo fixture, with no real guest images or
live lab changes. It checks all combinations of topology-write and console-input
switches, tool annotations and console roundtrips. The SDK must be installed in
that Python environment. It also checks storage report availability with every
capability combination, sparse/retained allocation, cache reuse, partial and
unknown results, and refusal when server automation is disabled. Optionally
add `--ollama-url http://HOST:11434/v1 --model MODEL` for model-driven discovery
and preview using synthetic catalog files. It does not boot vendor devices or
verify protocols. `--output /tmp/mcp-test` retains the generated exercise and
conversation locally; review it before sharing.


## Optional Agent window checks

`agent_bridge.py` provides browser sessions and a restricted Unix-socket gateway;
`agent_companion.py` manages pinned Codex processes in the optional companion;
`agent_auth.py` manages per-session Codex device login;
`agent_transport.py` is their dependency-free transport. `web/agent.js` uses
existing floating-window geometry. The main image does not install Codex or MCP.
The companion permits only its guarded apply/start/input tools through Codex's
per-tool approval policy, so approval is enforced by Weblab's browser cards or
the owner's explicit session YOLO setting. Do not replace this with a blanket
Codex approval policy or remove gateway enforcement.
Generic Codex shell/elicitation requests remain rejected. Pinned configuration
fields can be checked against the [Codex configuration reference](https://developers.openai.com/codex/config-reference)
and the installed CLI's generated schemas.

Default tests cover denied gateway routes, session separation, provider allowlists,
unchanged labs, duplicate request IDs, interruption and stored conversation state.
Authentication tests cover device-code completion/cancellation/logout, secret
redaction, per-session credential directories, disabled generic tools and exact
CONNECT destinations. The browser fixture covers provider selection and sign-in
controls without authenticating an account. History checks cover migration,
restart/resume, ownership, allowlist validation, approval revocation, rename/delete,
transcript downloads and optional remembered browser access. They use disposable
state and a simulated Codex RPC; they do not require an account or a running lab:

Attachment checks cover UTF-8/image headers and limits, session/conversation
ownership, duplicate sends, disk failures, history/restart retention and deletion.
Browser checks cover file selection, safe previews, removal, download and invalid
files. The packaged `--tool-gateway-only` fixture also asserts that actual image
data and text reach its simulated Responses endpoint through Codex. This verifies
transport, not a cloud model's visual understanding.

Optional lab transcript exports are covered by `tests/test_backup.py` (allowlisted
fields, bounds, checksums, rejected paths and restore without installing history),
the Agent ownership tests, and browser JSON/ZIP export checks. JSON import drops
reference history metadata. These checks use disposable fixtures only.

`tests/agent_packaging.py` checks the actual images with fresh named volumes:
runtime imports and Codex license/NOTICE, unprivileged network-none companion,
read/preview defaults, persisted session/draft access after recreating both
containers, and reset/close. It does not authenticate a real account or run
inference. `tools/smoke_container.py` also verifies that a plain core image reports
the Agent disabled. The release workflow runs these before publishing the Agent
image and requires that job before creating the GitHub release.

```sh
python3 -m unittest discover -s tests -p test_agent.py -v
node tests/agent_ui.cjs
```

For actual Codex/Ollama calls, build the optional image and run this **opt-in** test
with already installed models. It uses an empty disposable Weblab, no vendor
images or running lab, and cleans only its own test container/volume:

```sh
docker build -f packaging/agent/Dockerfile -t weblab-agent:dev .
python3 tests/agent_native.py --ollama-url http://YOUR_HOST:11434/v1 \
  --model gpt-oss:20b --model qwen3.5:9b-temp-0.3
```

To exercise the real pinned Codex device-login protocol and HTTPS gateway without
logging into an account or making an inference request, use:

```sh
python3 tests/agent_native.py --openai-login-only
```

It requests a device code and immediately cancels it, without printing the code.
It needs outbound access to the fixed OpenAI hosts and disposable Docker storage.
Signed-in generation, account limits and entitlement errors need a separate
operator test; never reuse host Codex credentials in automated tests.

For model-visible tool routing, use the real pinned Codex runtime with a local
simulated Code Mode model and the real Weblab MCP adapter/gateway:

```sh
python3 tests/agent_native.py --tool-gateway-only
```

This starts a disposable empty lab and a network-isolated companion. The fixture
uses bundled `gpt-5.6-sol` catalog metadata, checks that Code Mode is offered,
executes a read of the empty topology, and verifies that only Weblab tools are
callable (no shell, patch, filesystem or network globals). It uses no OpenAI
credentials or cloud inference. `tests/agent_tool_fixture.py` is mounted only by
this test and must never become a production entrypoint. Keep its checks when
upgrading Codex, including the builtin namespace exclusions.

For packaging without inference or external network access, also build the core
image and test the two services with fresh named volumes:

```sh
docker build -t weblab-agent-core-test:local .
python3 tests/agent_packaging.py
```

Add `--approvals` to also exercise browser-approved starts and console input
against two disposable echo devices, including layout edits during approvals,
with the real model/companion (no vendor boot).
Use `--approvals-only --model gpt-oss:20b` to focus on that path and provider failure.
Add `--protocol-preview` to check a nine-node OSPF/STP draft for redundant switch
paths, configuration snippets and PC addressing. This checks design structure,
not configuration syntax or protocol convergence.
The backend suite also checks denial/expiry/cancellation, session isolation, stale
revisions/cursors and human locks.

The Ollama test checks real previews, unchanged topology, duplicate-submit handling, Stop,
container recreation and continued conversation. It does not establish exercise
correctness or guest boot/forwarding. See [setup and limitations](agent.md).
