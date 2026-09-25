# Weblab

A browser workspace for building and running network labs with Cisco IOL,
IOSv/IOSvL2, Virtual EXOS, Arista vEOS-lab, FRRouting and Alpine PCs,
plus Juniper vJunos profiles.

Keep the topology, multiple device consoles and lab instructions together in one
page. Float, resize and arrange windows for desktop or tablet use; share consoles
across workstations with input locks and confirmed takeover. Practice failure
scenarios with directional link loss and supported carrier controls, and share
labs as topology JSON or saved-state ZIP archives.

**[Read the wiki](https://github.com/weblab-network/weblab/wiki)** · [Device profiles](https://github.com/weblab-network/weblab/wiki/Device-profiles) ·
[Starter exercise](examples/starter.md) · [OSPF practice](examples/ospf-practice.md)

Try the **[live demo preview](https://demo.weblab.network/)**: a shared workspace
with restricted controls for exploring the interface and device consoles.

## Quick start

Use an **x86-64 Linux host/VM**, rootful Docker Engine with Compose, and
`/dev/net/tun`. From the source checkout:

```sh
git clone https://github.com/weblab-network/weblab.git
cd weblab
cp .env.example .env
mkdir -p images lab-data
docker pull alpine:latest
docker compose up -d --build
```

Open **http://127.0.0.1:8080/**. Upload your device images, then create a starter
topology or import an example. Supply your own images and any required licenses;
the standard Weblab image does not include vendor images or generate licenses.

For IOSv, EXOS, vEOS or Junos, enable KVM and include the override:

```sh
docker compose -f compose.yaml -f compose.kvm.yaml up -d --build
```

If your Debian installation uses legacy Compose, install `docker-compose`:

```sh
apt update && apt install -y docker-compose
```

Then:

```sh
docker-compose -f compose.yaml -f compose.kvm.yaml up -d --build
```

Keep both `-f` arguments on subsequent commands for that deployment. See
[installation](docs/installation.md) for prerequisites, native Linux execution,
remote access, resource requirements and upgrades.

Prefer to skip the build? See [prebuilt containers](docs/installation.md#pull-a-prebuilt-container)
for the GHCR pull-and-start commands. The optional
[EXOS demo edition](docs/containers.md) includes Virtual EXOS and an unconfigured
three-switch/two-PC topology with a companion exercise.

## Recent additions

- Optional IOL cable unplug control, off by default to reduce CPU usage.
- FRR VRRP, console shell access and opt-in support for custom FRR images.
- Console interrupt and logout reliability fixes; see the [v0.3.0 release notes](docs/releases/v0.3.0.md).

- FRR container routers with saved configurations and an [OSPF exercise](examples/frr-ospf.md).
- vJunos-switch and vJunosEvolved profiles; see [requirements and validation](docs/devices.md#juniper-vjunos).
- Multi-select and move groups of topology nodes, including on touch screens.
- Compact vEOS ZIP backups with exact disk reconstruction on restore.

## Intended use

Weblab is a trusted personal/group lab tool with **no login or per-user isolation**.
The application controls the host Docker daemon and device consoles. Keep the
listener on loopback or a trusted network; use an SSH tunnel for remote access.
Console locks coordinate input, not authorization. Do not expose this preview
directly to the public internet.

Save device configurations before stopping. QEMU disks and IOL NVRAM/VLAN files
persist; Alpine PC filesystems are disposable. Use [saved lab ZIP](docs/backups.md)
for supported saved storage. JSON snippets initialize fresh Cisco/FRR nodes only.
See [limitations](docs/limitations.md) before relying on a particular workflow.

## Documentation and development

The [wiki](https://github.com/weblab-network/weblab/wiki) and
[offline help](docs/README.md) cover installation, device profiles, topology,
consoles, instructions, link faults, backups, troubleshooting and the topology
JSON contract. Agents and authors creating practice labs should read
[AGENTS.md](AGENTS.md) and the [practice-lab guide](docs/practice-labs.md).

The backend uses Python's standard library and Perl. The UI is plain JavaScript
and CSS with vendored terminal/Markdown assets; there is no frontend build step.
[Development and testing](docs/development.md) explains dependencies and disposable
regression suites.

## License

Weblab’s original code is licensed under the [MIT License](LICENSE).
Copyright © 2026 [weblab.network](https://weblab.network)

Bundled third-party components retain their own licenses, including GPLv2 for
`iou2net.pl`. See [third-party notices](THIRD_PARTY.md).


For an open-source routing lab without vendor images or KVM, see the
[FRR profile](docs/devices.md#frrouting) and [OSPF exercise](examples/frr-ospf.md).
FRR uses a separately pulled upstream Docker image, with digest checking by
default and an explicit [opt-in for modified images](docs/devices.md#allow-a-modified-frr-image).
