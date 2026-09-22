# Prebuilt images and EXOS demo

[Help index](README.md) · [Project overview](../README.md)

## Choose an edition

| Image | Contents |
| --- | --- |
| `ghcr.io/weblab-network/weblab` | Weblab and runtime dependencies; supply device images |
| `ghcr.io/weblab-network/weblab-exos` | Weblab plus pinned Virtual EXOS 33.1.1.31 and an unconfigured five-node topology |

Both are **Linux amd64** images. Use an x86-64 Linux host/VM with rootful Docker
Engine and Compose. The EXOS edition requires working `/dev/kvm`, including
nested virtualization if applicable, and `/dev/net/tun`. Allow at least 4 GiB
available RAM for the three 1 GiB EXOS guests and overhead; a host with 8 GiB or
more is recommended. Allow several GB of disk space for images and saved disks.

These are application containers that launch virtual devices; pulling one does
not remove the KVM requirement. Docker Desktop, rootless Docker and ARM hosts
are not supported by this deployment. See [installation](installation.md) for
permissions, trusted-network access and the standard edition.

The `edge` edition has also been independently field-tested on a fresh minimal
Debian 13 installation with KVM enabled. The documented deployment uses explicit
device access, NET_ADMIN and persistent storage; a successful privileged test does
not make `--privileged` a requirement.

## Start the EXOS demo

Use a new directory, separate from any existing Weblab checkout or `.env`:

```sh
mkdir weblab-exos-demo
cd weblab-exos-demo
curl -fLO https://raw.githubusercontent.com/weblab-network/weblab/main/compose.exos-demo.yaml
curl -fLo exercise.md https://raw.githubusercontent.com/weblab-network/weblab/main/examples/exos-demo.md
docker pull alpine:latest
docker compose -f compose.exos-demo.yaml pull
docker compose -f compose.exos-demo.yaml up -d
```

Open **http://127.0.0.1:8080/**, click **Start lab** and allow a few minutes for
the switches to boot. Open **Instructions → Open file** and select `exercise.md`
to keep the exercise beside the consoles. For a remote host, use the SSH tunnel
in the installation guide. EXOS console login is `admin` with an empty password.

The topology contains three EXOS switches and two PCs, already cabled together.
EXOS starts with factory defaults; no saved switch configuration is included.
On first login, answer `q` to accept the remaining setup defaults. Use
`enable lldp ports all` and `show lldp neighbors` on each switch to check its
switch-to-switch connections. PCs do not advertise LLDP by default. See the
[exercise](../examples/exos-demo.md) for optional VLAN/routing configuration tasks.
The Alpine image runs as sibling PC containers on the host, so it is pulled
separately. No network devices start until you click **Start lab**.

By default this Compose file uses `./exos-images` and `./exos-lab-data` and binds
to loopback port 8080. `WL_IMAGES_DIR`, `WL_DATA_DIR`, `WL_BIND`, `WL_PORT` and
`WL_HOSTNAME` override these defaults. `WL_DEMO_IMAGE` selects its image tag.
Do not run two workspaces on the same port or data directory. A `.env` copied
from another installation can override these paths; the new-directory approach
above avoids accidentally selecting existing storage.

## Persistence and updates

The binary is stored separately from writable mounts inside the container. On
startup it is copied into the image directory only if absent, and its SHA-256 is
checked. A same-name file with different contents causes an error; it is never
overwritten. The topology JSON is validated and installed **only when the data directory is
empty**. No writable guest disks are bundled; they are created when devices first
start. Existing workspaces, including previously configured demo labs, are left intact.

Restarts, image updates and **Stop lab** do not reset the exercise. Save EXOS
changes with `save configuration` before stopping. Export a saved ZIP before an
upgrade. To start with factory-default switches again, select a different empty `WL_DATA_DIR`; keep the old
directory as your backup. Never delete storage belonging to a running lab.

An interrupted initialization fails closed with an explanatory error. Preserve
that directory and retry with a new empty data directory. Do not remove the
initialization marker to force a retry over partially restored files.

Stable releases have numbered tags and `latest`; `edge` follows main and
`preview` is for the container feature branch. Pin a numbered tag or digest for
controlled upgrades. Use the same Compose filename on every command:

```sh
docker compose -f compose.exos-demo.yaml logs -f lab
docker compose -f compose.exos-demo.yaml down
docker compose -f compose.exos-demo.yaml pull
docker compose -f compose.exos-demo.yaml up -d
```

## Distribution and licenses

The optional EXOS edition uses the unmodified QCOW2 linked by the
[official Virtual EXOS repository](https://github.com/extremenetworks/Virtual_EXOS).
Its filename, upstream URL and SHA-256 are pinned in
[image metadata](../packaging/exos/image.json). At runtime, each guest gets a writable overlay over the same read-only base image.

Extreme's published redistribution notice is retained in
[Virtual-EXOS.txt](../licenses/Virtual-EXOS.txt), in the container's
`/app/licenses/`, and alongside the binary at `/opt/weblab/exos-demo/LICENSE.txt`.
Existing notices inside the guest image are retained. The optional image is not
wholly MIT-licensed: Weblab, EXOS, iou2net and runtime dependencies retain their
respective terms. See [third-party notices](../THIRD_PARTY.md).

Cisco and Arista binaries or license files are not included. No vendor images,
derived disks or saved ZIPs are committed to Git. Virtual EXOS is a virtual lab
image, not a guarantee of hardware data-plane features or vendor support.

## How images are published

The [container workflow](../.github/workflows/containers.yml) uses the repository's
automatic `GITHUB_TOKEN` with `packages: write`; no personal access token is
required. It tests the backend, builds Linux amd64, smoke-tests the packaged
server, then publishes to GHCR. The EXOS job downloads the pinned binary, verifies
its checksum, validates the topology JSON, and tests packaged initialization and
persistence. It does not boot EXOS VMs or require KVM on the build runner.
Real guest boot, LLDP and forwarding checks are separate, opt-in native tests;
passing the packaging pipeline alone does not establish protocol behavior.

After the first upload, the repository owner must make each GHCR package public
in its package settings so anonymous pulls work. Package visibility is separate
from repository visibility. Stable tags are published by pushing `vVERSION`;
branch builds do not overwrite `latest`.

For a numbered release, commit its notes at `docs/releases/vVERSION.md` and push
an annotated `vVERSION` tag on the reviewed public commit (for example `v0.2.0`).
A normal SemVer tag publishes both editions as `VERSION` and `latest` after their
respective checks pass. The jobs publish sequentially, so the two `latest` tags
are not updated atomically. After both succeed, the workflow creates a GitHub
Release using the committed notes. Existing release pages are left unchanged
on reruns. Do not move a published tag; use a new version for corrections.
