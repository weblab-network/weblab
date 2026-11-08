# Native ARM64 container labs

[Installation](installation.md) · [Device profiles](devices.md)

The optional ARM64 source-build profile runs **Weblab, custom FRR routers,
LL2S switches and Alpine PCs natively** on a Linux ARM64 host. FRR includes the
same LLDP, read-only SNMPv2c, routed VLAN and IPv4 VRRP extensions as the amd64
variant. Diagram-only network clouds and groups are also available.

This profile does not run Cisco IOL or the x86 QEMU vendor guests. The Internet
node, Agent companion and external SSH helpers are outside its initial validated
scope; do not add their Compose overrides. The existing prebuilt installer,
release tarballs and GHCR release images remain **amd64**. This profile builds
locally; it does not emulate x86 or turn a vendor image into an ARM image.

## Prepare a Linux host

Use native Linux ARM64 with rootful Docker Engine, Compose, Git, Python 3 and
tar. The Docker daemon and Weblab must share the Linux host network namespace;
Docker Desktop is not supported. `/dev/net/tun` and the host `openvswitch` module
are required. KVM is not needed.

Build on the target host. Allow several GiB of free build space and time for
FRR and Open vSwitch compilation. `--jobs 1` limits FRR compiler concurrency.
The scripts inspect the Docker daemon's architecture and select native bases.

```sh
git clone https://github.com/weblab-network/weblab.git
git clone --branch frr-10.7.1 --depth 1 https://github.com/FRRouting/frr.git frr-source
git clone https://github.com/weblab-network/ll2s.git ll2s-source
cd weblab

python3 tools/build_frr_lldp.py --source ../frr-source --jobs 1
python3 tools/build_ll2s.py --source ../ll2s-source
docker pull --platform linux/arm64 alpine:latest
sudo modprobe openvswitch
```

The builders use pinned commits, not uncommitted changes or the current branch.
FRR selects a pinned ARM64 upstream image digest. LL2S builds its published
Alpine base recipe, then applies the published, checksummed OVS RSTP correction.
Source attribution and package inventories remain in the images. Use
`--context-dir /new/directory` to retain the exact build inputs.

Resulting local image names:

| Device | Native image |
| --- | --- |
| FRR with Weblab extensions | `weblab-frr:10.7.1-wl1-arm64` |
| LL2S with OVS RSTP correction | `ll2s:0.2.0-ovs-rstp1-arm64` |
| PC | `alpine:latest` |

Persist loading `openvswitch` using your host's module-loading configuration if
needed after reboot. Missing kernel support must be fixed on the host.

## Start Weblab

For a new installation:

```sh
cp .env.example .env
docker compose -f compose.yaml -f compose.arm64.yaml up -d --build
```

Open **http://127.0.0.1:8080/** on the host, or use an SSH tunnel from your desktop:

```sh
ssh -L 8080:127.0.0.1:8080 user@your-arm-host
```

The override selects `linux/arm64`, enables the container-only profile and opts
into locally built FRR via `WL_ALLOW_UNTESTED_FRR=1`. The existing compatibility
warning remains visible; local builds are not the pinned amd64 release image.
The server also detects ARM64 automatically. Vendor uploads are hidden, Internet
is disabled, and only native FRR/LL2S images appear in the device selectors.
Unsupported imported nodes fail before Start lab launches any devices.

Keep the existing `.env` when upgrading. Save configurations, stop your lab,
then rebuild/recreate using the same Compose command. Do not add
`compose.kvm.yaml`, `compose.prebuilt.yaml`, `compose.agent.yaml` or
`compose.ssh.yaml` to this initial profile.

## Examples and saved state

Use the [ARM64 OSPF/RSTP practice lab](../examples/arm64-ospf-rstp.md)
and its [JSON](../examples/arm64-ospf-rstp.json), or create new nodes in the UI.
For other FRR/LL2S JSON examples, select the native image for each router/switch
before starting. PC images must also be native ARM64.

Changing an image selector does **not** migrate saved device state. Existing
saved state takes precedence over startup snippets. For an amd64-to-ARM move,
export saved FRR/LL2S configuration text and use it as the baseline on fresh
nodes. ZIP restore continues to require exact image identity; an amd64 image
and its ARM rebuild are different images, even when built from the same source.
See [backup semantics](backups.md).

This source profile is for a self-hosted installation. It does not include the
public sandbox's queue, visitor restrictions or reset policy.
