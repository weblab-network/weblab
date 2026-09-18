# Weblab

A browser workspace for building and running network labs with Cisco IOL,
IOSv/IOSvL2, Virtual EXOS, Arista vEOS-lab and Alpine PCs.

Keep the topology, multiple device consoles and lab instructions together in one
page. Float, resize and arrange windows for desktop or tablet use; share consoles
across workstations with input locks and confirmed takeover. Practice failure
scenarios with directional link loss and supported carrier controls, and share
labs as topology JSON or saved-state ZIP archives.

**[Read the wiki](https://github.com/weblab-network/weblab/wiki)** · [Device profiles](https://github.com/weblab-network/weblab/wiki/Device-profiles) ·
[Starter exercise](examples/starter.md) · [OSPF practice](examples/ospf-practice.md)

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
Weblab does not include vendor images or generate licenses.

For IOSv, EXOS or vEOS, enable KVM and include the override:

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

## Intended use

Weblab is a trusted personal/group lab tool with **no login or per-user isolation**.
The application controls the host Docker daemon and device consoles. Keep the
listener on loopback or a trusted network; use an SSH tunnel for remote access.
Console locks coordinate input, not authorization. Do not expose this preview
directly to the public internet.

Save device configurations before stopping. QEMU disks and IOL NVRAM/VLAN files
persist; Alpine PC filesystems are disposable. Use [saved lab ZIP](docs/backups.md)
for supported saved storage. JSON snippets initialize fresh Cisco nodes only.
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
