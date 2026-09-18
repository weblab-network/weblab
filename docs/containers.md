# Prebuilt images and EXOS demo

[Help index](README.md) · [Project overview](../README.md)

## Choose an edition

| Image | Contents |
| --- | --- |
| `ghcr.io/weblab-network/weblab` | Weblab and runtime dependencies; supply device images |
| `ghcr.io/weblab-network/weblab-exos` | Weblab plus pinned Virtual EXOS 33.1.1.31 and a saved five-node demo |

Both are **Linux amd64** images. Use an x86-64 Linux host/VM with rootful Docker
Engine and Compose. The EXOS edition requires working `/dev/kvm`, including
nested virtualization if applicable, and `/dev/net/tun`. Allow at least 4 GiB
available RAM for the three 1 GiB EXOS guests and overhead; a host with 8 GiB or
more is recommended. Allow several GB of disk space for images and saved disks.

These are application containers that launch virtual devices; pulling one does
not remove the KVM requirement. Docker Desktop, rootless Docker and ARM hosts
are not supported by this deployment. See [installation](installation.md) for
permissions, trusted-network access and the standard edition.

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

The demo has two VLANs and two PCs, with an EXOS device routing between them.
It starts with a saved, working configuration. See the
[exercise](../examples/exos-demo.md) for addresses, ports and troubleshooting tasks.
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
overwritten. The baseline ZIP is restored with Weblab's normal validated importer
**only when the data directory is empty**. Existing workspaces are left intact.

Restarts, image updates and **Stop lab** do not reset the exercise. Save EXOS
changes with `save configuration` before stopping. Export a saved ZIP before an
upgrade. To start fresh, select a different empty `WL_DATA_DIR`; keep the old
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
[image metadata](../packaging/exos/image.json). The saved demo disks are writable
overlays; each guest shares the same read-only base image.

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
its checksum and generates the seed ZIP by configuring disposable KVM guests.
It restores that ZIP into a separate lab and checks inter-VLAN traffic before
packaging it. It never uses a maintainer's running lab or private image directory.

After the first upload, the repository owner must make each GHCR package public
in its package settings so anonymous pulls work. Package visibility is separate
from repository visibility. Stable tags are published by pushing `vVERSION`;
branch builds do not overwrite `latest`.
