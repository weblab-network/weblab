# Weblab

A browser workspace for building and running network labs with Cisco IOL,
IOSv/IOSvL2, Virtual EXOS, Arista vEOS-lab, FRRouting, LL2S switches and Alpine PCs,
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

## See Weblab.Network in action

<a href="https://weblab.network/#watch"><img src="https://weblab.network/assets/overview-poster.png" alt="Watch the 1:55 Weblab.Network overview: topology editing, floating consoles and link faults" width="640"></a>

**[▶ Watch the 1:55 overview](https://weblab.network/#watch)** ·
[Full installation and lab walkthrough](https://weblab.network/#walkthrough) ·
[Watch on YouTube](https://youtu.be/fv2eDcvB_s0)

**Agent window · new in v0.5.0**

<a href="https://weblab.network/#agent-create-exercise"><img src="https://weblab.network/assets/weblab-agent-create-exercise-thumb.jpg" alt="Build an OSPF and RSTP practice lab with FRR, LL2S and student tasks" width="280"></a>
<a href="https://weblab.network/#agent-configure-verify"><img src="https://weblab.network/assets/weblab-agent-configure-verify-thumb.jpg" alt="Approve Agent console access and inspect switch configuration results" width="280"></a>

[▶ Create a practice lab · 1:13](https://weblab.network/#agent-create-exercise) ·
[▶ Configure and verify · 1:16](https://weblab.network/#agent-configure-verify) ·
[Agent setup and permissions](docs/agent.md)

*The Agent issues the console commands; the devices produce the output. The user
navigates the workspace and grants permissions, without manually typing console
commands. Silent, captioned edits; waits shortened. The practice lab intentionally
leaves OSPF and RSTP tasks for the student.*

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

## New in v0.5.0

- [Optional Agent window](docs/agent.md): floating conversations alongside the
  topology and consoles, using Ollama or OpenAI through ChatGPT sign-in.
- Review topology proposals, approve node starts and console configuration, or
  explicitly enable session YOLO for the actions you allow. Read/preview is the default.
- Conversation history, screenshot/text attachments and optional conversation
  transcripts in lab ZIP/JSON exports. Separate tabs keep operations and proposals
  out of the conversation's reading space.
- [Prebuilt companion](docs/agent.md#enable-with-prebuilt-images), separate from
  the standard image. No Agent or model account is needed for ordinary lab use.

See [v0.5.0 release notes](docs/releases/v0.5.0.md) for setup, limits and upgrades.

### Already available in v0.4.0

- [LL2S switches](https://github.com/weblab-network/ll2s): prebuilt Open vSwitch
  containers with STP/RSTP, VLANs, LLDP and optional SNMPv2c management. Combine
  with FRR and Alpine for switching/routing labs without KVM or vendor images.
- [MCP integration](docs/automation.md): optional external-agent discovery,
  topology previews, approved starts and shared-console reads/input. Agent
  proposals can use simple node aliases and omit link IDs.
- [Storage monitoring](docs/storage.md): filesystem headroom, per-node disk/log
  usage and preflight checks for starts, uploads, exports and restores.
- LL2S saved-image checks and recovery when a container is missing after reboot.

See [v0.4.0 release notes](docs/releases/v0.4.0.md) for scope and upgrade guidance.

### Open-source switching and routing

On the Docker host used by Weblab:

```sh
sudo modprobe openvswitch
docker pull ghcr.io/weblab-network/ll2s:0.2.0
docker pull quay.io/frrouting/frr:10.7.1
docker pull alpine:latest
```

Import the [LL2S/FRR OSPF topology](examples/ll2s-frr-ospf.json) and open its
[exercise](examples/ll2s-frr-ospf.md), or try the [RSTP/VLAN exercise](examples/ll2s-rstp.md).
Use Weblab v0.4.0 or newer. No KVM override is needed for these container-only labs.
See [LL2S setup](docs/devices.md#ll2s-linux-layer-2-switch) for saving and monitoring,
and the [FRR profile](docs/devices.md#frrouting) for image checks and limitations.

## Intended use

Weblab is a trusted personal/group lab tool with **no login or per-user isolation**.
The application controls the host Docker daemon and device consoles. Keep the
listener on loopback or a trusted network; use an SSH tunnel for remote access.
Console locks coordinate input, not authorization. Do not expose this preview
directly to the public internet.

Save device configurations before stopping. QEMU disks and IOL NVRAM/VLAN files
persist; Alpine PC filesystems are disposable. Use [saved lab ZIP](docs/backups.md)
for supported saved storage. JSON snippets initialize fresh Cisco/FRR/LL2S nodes only.
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

## Contact

For questions or feedback, [open a GitHub issue](https://github.com/weblab-network/weblab/issues)
or [reveal the contact email on our website](https://weblab.network/#contact).

## Screenshots

Click a thumbnail to open the full-size image. Ctrl/Cmd-click opens it in a new tab.

<p>
  <a href="https://weblab.network/assets/showcase-topology.png"><img src="https://weblab.network/assets/showcase-topology.png" alt="Multi-vendor topology with the Junos switch inspector and tabbed console" title="Topology and device inspector — view full size" width="260"></a>
  <a href="https://weblab.network/assets/showcase-tabbed-consoles.png"><img src="https://weblab.network/assets/showcase-tabbed-consoles.png" alt="Floating topology beside tabbed consoles showing EXOS VLANs and spanning tree" title="Floating topology and tabbed consoles — view full size" width="260"></a>
  <a href="https://weblab.network/assets/showcase-arranged-consoles.png"><img src="https://weblab.network/assets/showcase-arranged-consoles.png" alt="Arranged topology and Junos, FRR and EXOS consoles showing OSPF, VRRP and VLANs" title="Arranged multi-vendor consoles — view full size" width="260"></a>
</p>

### Tablets and phones

Keep the topology and console visible while using the onscreen keyboard.
[Explore the smaller-screen workspace](https://weblab.network/#mobile).

<p>
  <a href="https://weblab.network/assets/mobile-tablet-fullscreen.png"><img src="https://weblab.network/assets/mobile-tablet-fullscreen.png" alt="Fullscreen tablet with arranged topology, tabbed consoles and onscreen keyboard" title="Fullscreen tablet — view full size" width="210"></a>
  <a href="https://weblab.network/assets/mobile-phone-fullscreen.png"><img src="https://weblab.network/assets/mobile-phone-fullscreen.png" alt="Phone with manually arranged topology and console above the onscreen keyboard" title="Phone workspace — view full size" width="150"></a>
</p>

<details>
<summary>More tablet views: browser and device inspector</summary>

<p>
  <a href="https://weblab.network/assets/mobile-tablet-browser.png"><img src="https://weblab.network/assets/mobile-tablet-browser.png" alt="Tablet browser showing the topology, FRR console and onscreen keyboard" title="Tablet browser — view full size" width="210"></a>
  <a href="https://weblab.network/assets/mobile-tablet-inspector.png"><img src="https://weblab.network/assets/mobile-tablet-inspector.png" alt="Tablet with the device inspector open beside the topology and FRR console" title="Tablet device inspector — view full size" width="210"></a>
</p>

</details>

These screenshots show the shared demo preview, where controls are restricted.

## License

Weblab’s original code is licensed under the [MIT License](LICENSE).
Copyright © 2026 [weblab.network](https://weblab.network)

Bundled third-party components retain their own licenses, including GPLv2 for
`iou2net.pl`. See [third-party notices](THIRD_PARTY.md).
