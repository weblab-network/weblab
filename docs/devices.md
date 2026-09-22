# Images and device profiles

[Help index](README.md) · [Project overview](../README.md)

## Supported profiles

These are tested profiles, not a guarantee that every virtual-image release or
network feature works. Supply your own images and any required licenses.

| Family | Example tested image | Default RAM / vCPU | Interfaces | KVM | Initial snippets / config-text export | Saved ZIP |
| --- | --- | --- | --- | --- | --- | --- |
| Cisco IOL router/switch | `cisco_iol-17.18.02.bin`, `cisco_iol-l2-17.18.02.bin` | 1024 MB / native process | 4 ports per Ethernet slot; 1–8 slots | No | Yes / saved and live | Yes |
| Cisco IOSv / IOSvL2 | `cisco_vios-159-3.M12.qcow2`, `vios_l2-adventerprisek9-m.ssa.high_iron_20200929.qcow2` | 1024 MB / 1 | 1–16 | Yes | Yes / saved and live | Yes |
| Virtual EXOS | `EXOS-VM_33.1.1.31.qcow2` | 1024 MB / 1 | Mgmt + 1–12 | Yes | No / no | Yes |
| Arista vEOS-lab | `vEOS64-lab-4.36.1F.qcow2` + serial Aboot 8.0.2 | 6144 MB / 2 | Management1 + Ethernet1–15 | Yes | No / no | Yes |
| FRRouting container | `quay.io/frrouting/frr:10.7.1` | 512 MB container limit | eth0–eth7 | No | Yes / saved only | Saved frr.conf |
| Juniper vJunos-switch | `vJunos-switch-26.2R1.7.qcow2` | 5120 MB / 4 | fxp0 + ge-0/0/0–14 | Intel KVM, bare metal | No / no | Disk overlay |
| Juniper vJunosEvolved | `vJunosEvolved-26.2R1.7-EVO.qcow2` | 8192 MB / 4 | re0:mgmt-0 + et-0/0/0–14 | Yes; UEFI | No / no | Disk overlay |
| Alpine PC | locally installed `alpine:latest` | Host Docker container | eth0 | No | IPv4/gateway fields only | Settings only; no PC filesystem |

## Installing images

Use **Upload image** or copy files to the configured image directory (`images/`
in the default Compose setup). Upload accepts uncompressed ELF `.bin` images and
supported standalone QCOW2 files up to 1 GiB. The exact Arista companion ISO is
accepted up to 64 MiB. Uploads never overwrite an existing filename.

Uploaded IOL files become executable. Manually copied `.bin` files need execute
permission; QCOW2 files only need read permission. Keep vendor filenames,
especially the EXOS/Arista prefixes used to select their profiles. A filename
containing `l2` defaults to a switch for Cisco images. The inspector lets you
choose a compatible image for a node.

QCOW2 upload supports v2/v3 images up to 64 GiB virtual size. Base disks with
backing files, external data files or encryption are rejected. Arbitrary guest
operating systems and renamed unsupported images are not supported profiles.

## Cisco IOL

IOL is a native executable with 32-bit runtime dependencies included in Docker.
A node's `ethernet` value is the number of four-port slots. Link ports use `0/0`
through `0/3`, then `1/0`, etc.; IOS configuration usually calls them
`Ethernet0/0`, `Ethernet0/1`, and so on. Switchport commands require an L2 image.

If licensing is required, provide `iourc` beside the images or in the node's
working directory. For native execution an explicit `IOURC` environment variable
can point to a supplied file. Weblab does not generate licenses. Save configuration
with `write memory`; [saved ZIP](backups.md) includes NVRAM and the supported VLAN
and flash files. A first boot may ask to overwrite seeded NVRAM when saving.

## IOSv and IOSvL2

The same router and switch palette supports IOSv. Copy your `.qcow2` files into
`images/`, or use **Upload image**. These files need read permission, not an
executable bit. Filenames containing `l2` default to switches. Add a router or
switch, select its QCOW2 image in the inspector and click **Apply settings**.
Apart from the EXOS, Arista and Junos profiles below, QCOW2 profiles target IOSv and IOSvL2; arbitrary QCOW2 operating systems
are not supported. Both `cisco_vios-159-3.M12.qcow2` and
`vios_l2-adventerprisek9-m.ssa.high_iron_20200929.qcow2` are supported profiles.

IOSv requires [working KVM](installation.md#enable-kvm-for-qemu-devices).

Each IOSv node uses one vCPU and defaults to 1024 MB RAM and four Ethernet
interfaces; the inspector supports 1–16 interfaces. Router ports are `Gi0/0` through
`Gi0/15`; switch ports are `Gi0/0` through `Gi3/3`. IOSv can take several minutes to
boot after its status becomes Running. Use the existing console tabs to configure
it. Stop the lab and remove incompatible cables before changing an existing IOL
node to an IOSv image, because interface names differ.

IOSv–IOSv, IOSv–IOL and IOSv–PC cables use the same topology editor. A local process
(`qemu_net.py`) translates QEMU Ethernet streams to the existing IOL socket fabric,
including VLAN-tagged frames. IOSv networking adds no host bridge or network
listener. PCs retain their existing TAP/macvlan setup and must start after their
connected router/switch. No IOL `iourc` is passed to IOSv.

The uploaded base disk stays unchanged. Each node/image combination gets its own
writable QCOW2 overlay in the node data directory. Use IOS `write memory` before
stopping; the overlay survives Stop/Start and container recreation. Selecting a
different image creates a separate disk; reselecting the previous image reuses its
saved disk. Saved lab ZIP includes the currently selected disk for each node;
topology JSON does not. Back up the whole stopped data directory and the original
images together to preserve all historical disks as well. Preserve image filenames;
backing symlinks are refreshed on startup if the image directory moves.

Uploads accept standalone QCOW2 v2/v3 disks up to 1 GiB, with virtual disk size up
to 64 GiB. Images with backing files, external data files or encryption are rejected.
Copying a file manually does not bypass validation at startup.

## Virtual EXOS

The EXOS QEMU profile uses the fixed `Nehalem-v1` virtual CPU model with RDTSCP for
consistent CPU identification and features across hosts. EXOS 33.1 uses the model name to
detect x86; an AMD model name can otherwise stop boot at a developer menu and shell.

Upload the vendor QCOW2 with its original filename, for example
`EXOS-VM_33.1.1.31.qcow2`, then add a **switch** and select that image. Names starting
with `EXOS-VM_` or `EXOS-VM-` (case insensitive) select the EXOS profile. Other QCOW2
filenames retain the IOSv profile except the Arista and Junos prefixes documented below.
EXOS uses KVM, one vCPU, 1024 MB RAM by default,
an IDE disk and RTL8139 NICs. Its writable disk is separate from the uploaded image.

The interface count includes **Mgmt** followed by numbered data ports **1–12**;
the default is 13 interfaces. Mgmt is separate from the data ports and is not
automatically connected to the host network. Connect PCs or other switches to
the numbered ports. Link JSON uses `"port":"1"`, not Cisco interface names.

Start the switch and open its console. On the tested 33.1.1.31 image, log in as
`admin` with an empty password. Allow the pending-AAA boot stage to finish. On first
login, `q` accepts defaults for the remaining setup questions. Use `show version`,
`show ports information` and `show vlan` to inspect it. `save configuration` followed
by confirmation saves changes to the writable disk. Browser console sharing and
input locks work as for other devices.

This first EXOS implementation covers boot, console, topology links and persistent
disk storage. Topology JSON and stopped-lab ZIP backup/restore support EXOS. Initial
configuration snippets, live configuration capture and saved-text extraction are
not implemented for EXOS; the app rejects those operations rather than sending
Cisco commands or parsing an EXOS disk as Cisco NVRAM. A mixed lab containing EXOS
can still use Topology JSON or Saved lab ZIP.

## Arista vEOS-lab

Upload `vEOS64-lab-4.36.1F.qcow2` and `Aboot-veos-serial-8.0.2.iso`, or copy them
into the image directory. Add a **switch**, select the vEOS image and apply its
settings. Names beginning `vEOS64-lab-` or `vEOS-lab-` (case insensitive) select
the Arista profile; preserve the vendor filename. The ISO is boot media and does
not appear as a device image. Startup checks it before creating a writable disk.
This profile targets vEOS-lab, not CloudEOS/vEOS Router or CVX.

Arista uses two vCPUs, 6144 MB RAM by default, an IDE writable disk, the read-only
Aboot CD-ROM and virtio NICs. The interface count includes `Management1` followed
by `Ethernet1` through `Ethernet15`; choose 2–16 total interfaces, default 5.
Use Ethernet data ports for ordinary cables. Management1 is isolated from the
host unless explicitly connected within the topology. JSON link ports use the
full spelling, for example `"port":"Ethernet1"`.

Allow several minutes for first boot. Open its console and log in as `admin`
with no password. Run `zerotouch disable` and wait for its reboot, then log in
again and use `enable`. Useful checks are `show version`, `show interfaces status`
and `show lldp neighbors`. Configure the device normally and use `write memory`
before stopping it. Shared consoles and input locks work as for other devices.
The app's Stop ends the VM process; save first. Use the application's Stop action after saving; guest shutdown behavior varies by image.

Plain topology JSON and stopped-lab Saved lab ZIP are supported. ZIP preserves
the writable overlay and records hashes for both the base QCOW2 and required
Aboot ISO; both must be installed unchanged on restore. Device images and the
ISO are not embedded in the ZIP. Arista startup snippets and saved/live config
text extraction are not yet supported, and are rejected explicitly. Existing
Cisco handlers must not be used on an Arista node.

This profile requires `Aboot-veos-serial-8.0.2.iso`. Other Aboot releases
are not supported by this profile.

## Alpine PCs

Install the requested Alpine tag in the host Docker daemon, for example
`docker pull alpine:latest`. Weblab does not pull images automatically when
starting nodes. PCs must connect to a router/switch; direct PC-to-PC cables
are not accepted.

Set IPv4/prefix and optional gateway in the inspector. A PC may also be left
without an initial address. The gateway must belong to its configured subnet.
The PC starts after its peer. Its shell provides ordinary Linux tools such as
`ip address`, `ip route` and `ping`.

PC containers are disposable. Files, installed packages and shell-made network
changes are lost on stop/start. Topology address/gateway settings are reapplied.
PC resource fields do not enforce a Docker memory limit or add interfaces.


## FRRouting

FRR runs as a separate Docker container on the same Docker Engine used by Alpine
PCs. No KVM, QCOW2 image or custom Dockerfile is needed. Pull the supported image
on that Docker host:

```sh
docker pull quay.io/frrouting/frr:10.7.1
```

Add a **Router**, select `quay.io/frrouting/frr:10.7.1` in its image selector, and
apply. The profile is listed even before pulling; Start gives an actionable error
if the image is missing. It checks the tested upstream manifest digest
`sha256:e995beaa50fdc9edb35eadcfefa29b7f062cc06f2b812613789b68fa541554d2`;
other tags/custom images are not supported by this first profile. Weblab never
pulls images implicitly. [Upstream image information](https://frrouting.org/release/).

Choose 1–8 interfaces (default 4), named **eth0** through **eth7**. Each interface
is a separate lab port; none is reserved for management. The default memory limit
is 512 MB. Start opens the shared **vtysh** routing CLI without a login prompt.
Use `configure terminal`, `show interface brief`, `show ip route` and
`show running-config`. Console input locks and floating windows work as usual.

The image's zebra/static routing services and BGP, OSPFv2, OSPFv3, RIP, RIPng,
IS-IS and BFD daemons are enabled. Configure protocols through vtysh; enabled
services alone do not establish neighbors. FRR is an L3 routing suite, not an
STP/RSTP/MSTP switch. This profile does not provide Linux bridge/VLAN management,
a general Linux shell console, or arbitrary guest package/filesystem persistence.

Use **write memory** before Stop. The last saved `/etc/frr/frr.conf` is copied
into the node's lab storage before its container is removed. Unsaved running
changes are excluded. Existing saved configuration wins over `startup_config`
snippets on every subsequent start, including after ZIP import. If copying fails,
Stop retains the container and reports an error so the saved state can be recovered.
Do not manually remove managed containers: that bypasses configuration collection.
After an interrupted Weblab server, journal recovery collects the retained
container's saved config and removes its owned resources.

Saved lab ZIP includes FRR configuration and the required container image digest;
the destination must have the matching image pulled. **JSON + saved configs**
works while stopped (16 KiB snippet limit). **JSON + live configs** is not yet
implemented for FRR. ZIP preserves configurations up to 1 MiB per FRR node.
Daemon launch settings are generated by Weblab, not restored shell scripts.
Kernel changes made outside FRR, installed packages and other container files
are not backed up; keep interface addresses and routes in the FRR configuration.

FRR-to-FRR and mixed-node links use Weblab's existing Ethernet fabric. Directional
loss preserves carrier. **Unplug** lowers carrier on the selected port’s parent TAP so
the router sees carrier loss; peer carrier is controlled separately. Faults remain
runtime-only. Interfaces and routing tables are inside separate container network
namespaces; the profile does not enable routing on the Docker host.

Use the same local Docker socket and host-network Weblab setup as Alpine PCs;
remote Docker Engines are unsupported. The FRR container uses NET_ADMIN, NET_RAW
and SYS_ADMIN capabilities in its own namespaces, without privileged mode or host
PID/network sharing. See the [FRR OSPF exercise](../examples/frr-ospf.md) and
[its topology](../examples/frr-ospf.json) for a vendor-image-free starting point.


## Juniper vJunos

Copy `vJunos-switch-26.2R1.7.qcow2` or
`vJunosEvolved-26.2R1.7-EVO.qcow2` into the image directory. These images exceed
Weblab's 1 GiB browser-upload limit; use a filesystem copy. Preserve their vendor
filenames. They are user-supplied and are not bundled with Weblab.

Use a **Switch** for vJunos-switch and a **Router** for vJunosEvolved. The palette
shape is an editor classification, not a guarantee of every L2/L3 feature.
Both profiles use four vCPUs and virtio disks/NICs. The switch defaults to
5120 MB RAM; Evolved requires 8192 MB. Each defaults to five interfaces:
management plus four data ports. Select 2–16 total interfaces.

| Profile | Management | Data ports |
| --- | --- | --- |
| vJunos-switch | `fxp0` | `ge-0/0/0`–`ge-0/0/14` |
| vJunosEvolved | `re0:mgmt-0` | `et-0/0/0`–`et-0/0/14` |

Management is isolated from the host network unless connected explicitly in the
lab. Use data ports for ordinary topology cables. Evolved uses non-channelized
ports and requires OVMF UEFI firmware (`apt install ovmf` for native installs;
included when rebuilding the Weblab Docker image). Evolved also receives a
stable VM UUID derived from the topology node ID, retained across restarts and
ZIP restores. Keep node IDs distinct when creating independent devices.

vJunos-switch itself starts a nested control-plane VM. Juniper specifies Intel
VT-x and does not support deploying it inside another VM. Evolved has a different
architecture and does not have that same nesting requirement. See Juniper's
[switch requirements](https://www.juniper.net/documentation/us/en/software/vjunos/vjunos-switch-kvm/topics/vjunos-switch-kvm-hw-requirements.html)
and [Evolved deployment guide](https://www.juniper.net/documentation/us/en/software/vJunosEvolved/vjunos-evolved-kvm/topics/vjunos-evolved-deploy-on-kvm.html).

Initial snippets and saved/live configuration-text export are not implemented
for Junos. Configure through the console. The uploaded base image remains
unchanged; each node has its own writable disk. Stopped-lab ZIP includes that disk
and requires the same base image on restore. Directional traffic blocking uses
the existing link fabric; Junos carrier/unplug control is not enabled.


Validation of 26.2R1.7: a bare-metal deployment booted vJunos-switch in under two
minutes and passed VLAN 10/20/30 access switching, an 802.1Q trunk to FRR, and
inter-VLAN traffic between three Alpine PCs. This was a user-run integration
test. A nested-VM test reached Junos but failed to initialize the forwarding
plane; a working CLI alone is insufficient. Evolved boot/console and the UUID
correction have been tested, but full forwarding and real-guest ZIP restore
coverage remain incomplete. Repeated boots of both profiles and installation
from the published instructions have also been user-tested. These results
apply to the listed images and host requirements, not arbitrary Junos releases.

On a fresh image, log in as `root` with an empty password, then enter `cli`.
Set a root password before committing configuration. Use `show chassis fpc`
and `show interfaces terse` to check forwarding-plane readiness. Save changes
with `commit`. Before Weblab Stop, use `request system power-off` and wait for
guest shutdown. Weblab Stop terminates QEMU; it does not currently perform a
Junos-aware graceful shutdown. Juniper warns that abrupt termination can damage
the switch's disk; see its [deployment guide](https://www.juniper.net/documentation/us/en/software/vjunos/vjunos-switch-kvm/topics/deploy-and-manage-vjunos-switch-onkvm.html).

**Required shutdown sequence:** `commit` configuration changes, return to
operational mode, run `request system power-off`, and wait for guest shutdown
before using **Stop node**, **Stop lab**, or stopping/recreating the Weblab
container. Stopping without this sequence has caused an unbootable switch disk
in user testing. The workspace asks for confirmation when stopping running Junos
nodes; Cancel lets you return to their consoles. The warning does not execute
shutdown or verify that it completed. Direct API calls and container/host
shutdowns bypass the browser warning.
