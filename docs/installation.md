# Installation and upgrades

[Help index](README.md) · [Project overview](../README.md)

## Requirements and access

Use an x86-64 Linux host or Linux VM with rootful Docker Engine and Compose v2.
For QEMU images, enable KVM (including nested virtualization if this host is a VM).
Budget RAM for the sum of the guest allocations plus the host and Weblab; see
[device defaults](devices.md#supported-profiles).

Weblab has **no login or user authorization**. It controls the host Docker daemon,
executes supplied device images, and exposes device consoles. Keep it on a trusted
machine. The default listener is loopback; an SSH tunnel is the simplest remote
access method:

```sh
ssh -L 8080:127.0.0.1:8080 user@lab-host
```

Open `http://127.0.0.1:8080/` on the client. Do not expose this preview directly to
the public internet. Console input locks coordinate users; they are not access
control.

## Pull a prebuilt container

GHCR provides the same application as the source build for **Linux amd64**.
The standard image includes Weblab and its runtime dependencies; supply your own
network-device images and any required licenses. Host requirements and permissions
are the same as the source build.

From the source checkout, or a directory containing `compose.prebuilt.yaml` and
`compose.kvm.yaml` downloaded from this repository:

```sh
mkdir -p images lab-data
docker pull alpine:latest
docker compose -f compose.prebuilt.yaml -f compose.kvm.yaml pull
docker compose -f compose.prebuilt.yaml -f compose.kvm.yaml up -d
```

Omit the KVM override for IOL-only installations. Open **http://127.0.0.1:8080/**,
or use the SSH tunnel above. Public images require no registry login.
Use `compose.prebuilt.yaml` instead of `compose.yaml`, not as an override of it.
Keep the same Compose files and directory on subsequent `logs`, `pull`, `up` and
`down` commands. Legacy `docker-compose` accepts the same arguments.

`WL_IMAGE` selects the application image, defaulting to
`ghcr.io/weblab-network/weblab:latest`. Stable versions also have numbered tags;
pin a version or digest when you want deliberate upgrades. `edge` follows main;
`preview` is used while testing the container publication branch. Neither is a
stable release. Save configurations, export a ZIP and stop the lab before pulling
and recreating an existing deployment. Preserve its image/data mounts.

## Run the prebuilt image without Compose

For the same Linux/KVM setup, with persistent named volumes:

```sh
docker pull alpine:latest
docker pull ghcr.io/weblab-network/weblab:latest
docker run -d --name weblab --init --stop-timeout 120 \
  --hostname weblab --add-host weblab:127.0.0.1 \
  --network host --cap-add NET_ADMIN \
  --device /dev/net/tun --device /dev/kvm \
  -e WL_BIND=127.0.0.1 \
  -v weblab-images:/iou -v weblab-data:/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  ghcr.io/weblab-network/weblab:latest
```

For an optional non-resolving DNS setting, add `--dns 127.0.0.199` after
`-e WL_BIND=127.0.0.1`. With host networking this uses the host loopback;
it only fails to resolve names if no DNS service answers at that address.
It does not block internet access or change the host Docker daemon’s DNS
for image pulls. See [Docker DNS services](https://docs.docker.com/engine/network/#dns-services).

Omit `--device /dev/kvm` for IOL-only labs. Use `docker logs -f weblab` to inspect
startup and `docker stop weblab` to stop gracefully. Keep the same named volumes
when recreating the container to upgrade it. Uploaded images live in `/iou` and
saved lab state in `/data`; without these mounts, removing a container also
removes those files. Alpine PCs use the host's Docker image store, which is why
`docker pull alpine:latest` is a separate step.

The tested Linux setup needs the listed devices and NET_ADMIN, without
`--privileged`. Change `WL_BIND` to a trusted LAN address when remote access is
wanted. Do not add `-p` with `--network host`: Docker ignores published ports in
[host networking](https://docs.docker.com/engine/network/drivers/host/).
The `--add-host` entry makes the chosen hostname resolve inside the container
for NETMAP; change both values if you choose a different hostname.

## Docker Compose

Requires an **x86-64 Linux host**, rootful Docker Engine with Compose v2, and
`/dev/net/tun`. This setup uses macvlan and is intended for native Linux or a Linux
VM, not Docker Desktop or rootless Docker.

```sh
cp .env.example .env
mkdir -p images lab-data
# Optional: copy your IOL .bin files into images/, then chmod +x images/*.bin
docker pull alpine:latest
docker compose up -d --build
```

Open **http://127.0.0.1:8080/**. Use **Upload image** in the device palette
if you haven't copied images already. Preserve vendor filenames; see [device profiles](devices.md) for image selection and port names.

The app runs on **Debian Bookworm slim**. The container contains a Docker client;
Alpine PCs run as sibling containers on the host's Docker Engine. It does not run
a second Docker daemon.

Edit `.env` to customize:

| Setting | Default | Purpose |
| --- | --- | --- |
| `WL_IMAGES_DIR` | `./images` | Host directory mounted read/write at `/iou` |
| `WL_DATA_DIR` | `./lab-data` | Host directory mounted at `/data` for topology, logs and NVRAM |
| `WL_HOSTNAME` | `weblab` | Stable container hostname for NETMAP and your licensing setup |
| `WL_BIND` | `127.0.0.1` | Web listener; set your trusted LAN address for remote access |
| `WL_PORT` | `8080` | Web and console proxy port |

An optional user-supplied `/iou/iourc` is preserved exactly.
The app links it into each node's working directory and explicitly sets `IOURC`
for the IOL process. A node-specific `iourc` takes precedence.

NETMAP is generated when a node starts: `/data/NETMAP`, with a link at
`/data/nodes/NODE_ID/NETMAP`. You do not need to create `/iou/NETMAP`.
Compose explicitly maps the container hostname to `127.0.0.1` so IOL can resolve
the local endpoints in NETMAP under host networking. Without that mapping, IOL can
exit with `netio error: mkaddr: No route to host`.

If an image requires licensing, supply a license appropriate for its execution
environment. Weblab does not create or replace `iourc`. Some older IOL builds may
require additional libraries beyond the included common 32-bit runtime libraries.

```sh
docker compose logs -f lab
docker compose down              # Gracefully stops managed nodes; saved files remain
docker compose up -d --build      # Rebuild after updating the source
```

Save IOS configuration with `write memory` before stopping. Use **Export → Saved lab ZIP**
to carry IOL NVRAM and current IOSv disks alongside the topology. **Import** restores
the ZIP after checking that the same base images are installed. JSON export still
contains topology/settings and any original startup snippets. **JSON + saved configs**
reads saved startup configurations directly from IOL NVRAM or IOSv disks into reusable
first-boot snippets. Cisco devices must be stopped; no console access is needed.
Unsaved changes are excluded. **JSON + live configs** captures current running configurations; Cisco
devices must be running at an unlocked privileged console prompt. Capture temporarily
suppresses enabled console logging and verifies restoration of the original settings;
it does not save to NVRAM. See [capture details](backups.md#export-an-example-lab-with-current-configurations)
and [backup details](backups.md#export-and-restore-saved-configurations).
PC containers are recreated on startup, so their shell files and installed packages
are temporary. After a container restart, saved nodes are stopped until you start them.

## Enable KVM for QEMU devices

IOSv/IOSvL2, Virtual EXOS, vEOS-lab and Junos require working `/dev/kvm`. Add the override:

```sh
docker compose -f compose.yaml -f compose.kvm.yaml up -d --build
```

Keep both `-f` arguments on later Compose commands for this deployment. IOL-only
installations do not need the override. Legacy Compose uses `docker-compose`
with the same arguments. Allocate enough host memory before starting large labs.
vJunos-switch requires Intel VT-x and a bare-metal host for the supported
deployment; running it inside another VM can leave its forwarding plane
unavailable. See [Junos profiles](devices.md#juniper-vjunos-experimental).
FRR uses Docker and does not require KVM; pull its [supported image](devices.md#frrouting) first.

## Why these container permissions?

The PC bridge creates TAP interfaces; Alpine PCs run as sibling containers on
the host Docker daemon. Weblab and Docker must share the network namespace so
Docker can find the TAP parents. Compose uses host networking, NET_ADMIN,
`/dev/net/tun` and the Docker socket. It does not require `privileged: true`.
Host networking means Compose port mappings are not used.

## Run directly on Linux

The same scripts work without an application container. On Debian Bookworm amd64,
with Docker Engine already installed and running:

```sh
sudo apt-get update
sudo apt-get install -y python3 perl libnet-pcap-perl iproute2 \
  libc6-i386 lib32gcc-s1 lib32stdc++6 lib32z1
sudo docker pull alpine:latest
sudo ./start-lab.sh --image-dir ./images --data-dir ./lab-data
```

For QEMU profiles, also install `qemu-system-x86 qemu-utils mtools ovmf` (QEMU 7.2+) and ensure the
server user can open `/dev/kvm`. Saved compressed NVRAM retrieval also requires `gzip`.

Root is needed for PC TAP creation and access to the rootful Docker daemon. For
IOL-only topologies, the server can run as an ordinary user with writable image and
data directories; Docker and TAP access are only used when starting PCs.

The image directory defaults to the application directory, preserving existing
`./start-lab.sh` installations. Override it with `--image-dir` or `WL_IMAGES_DIR`.
State defaults to `.lab/`. Native execution also accepts `WL_DATA_DIR`,
`WL_BIND` and `WL_PORT`; explicit command-line options override these variables.
`WL_HOSTNAME` is a Compose setting; native execution uses the host's hostname.
Paths supplied to `start-lab.sh` are relative to the application directory.
An optional user-supplied `iourc` belongs in the selected image directory.
No license is generated automatically.

For an externally stored user-supplied license, you can set `IOURC` when starting
the native server. A readable node-specific or image-directory `iourc` takes
precedence. Keep license contents out of source control and public logs.

## Upgrade or move an installation

Existing installations using `IOL_*` application settings must rename them to
`WL_*` in `.env` before recreating the container (for example, `IOL_DATA_DIR`
becomes `WL_DATA_DIR`). Native `IOL_IMAGE_DIR` becomes `WL_IMAGES_DIR`. Preserve
your existing paths, hostname and listener values. The vendor variable `IOURC`
is unchanged.

1. Save device configuration and export a saved lab ZIP. Preserve the matching
   base images separately.
2. Stop the lab before recreating the application container.
3. Update source, rebuild/recreate with the same Compose files and data mount,
   then refresh the browser. There is no frontend build step.
4. Start the lab when ready; application restarts leave managed nodes stopped.

Do not manually purge `lab-data` before a ZIP import. Restore already replaces
node storage transactionally. See [backups](backups.md) for image checks,
configuration precedence and compatibility with older archives.
