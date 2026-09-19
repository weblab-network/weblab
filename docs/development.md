# Development and testing

[Help index](README.md) · [Project overview](../README.md)

## Source layout

| Path | Purpose |
| --- | --- |
| `lab_server.py` | HTTP API, validation, topology persistence, process/resource lifecycle |
| `lab_backup.py` | ZIP validation, export, staged restore and recovery |
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

There is no frontend build step and no third-party Python package dependency.
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
node tests/console_clipboard.cjs
node tests/instructions.cjs
node tests/workspace_features.cjs
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
uses only the pinned public EXOS release and files generated from the committed
exercise configuration. Run from a disposable development host with root, KVM,
Docker, Alpine and the native dependencies listed above:

```sh
python3 tools/fetch_exos_demo_image.py packaging/exos/build
sudo python3 tools/build_exos_demo.py \
  packaging/exos/build/EXOS-VM_33.1.1.31.qcow2 packaging/exos/build/demo.zip
docker build -t weblab-base:local .
docker build -f packaging/exos/Dockerfile \
  --build-arg WEBLAB_IMAGE=weblab-base:local -t weblab-exos:local .
python3 tools/smoke_exos_container.py weblab-exos:local
```

Use current Docker with BuildKit for the Dockerfile-specific context allowlist.
The builder configures fresh switches, saves their disks, exports a ZIP, restores
it into another disposable lab, and checks traffic between both PCs. It stops and
removes only its own test nodes. Run it sequentially with other native suites.
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
