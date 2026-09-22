# Supported scope and limitations

[Help index](README.md) · [Project overview](../README.md)

## Deployment scope

Weblab is a self-hosted personal/trusted-group lab workspace. It has no accounts,
authentication, roles or per-user lab isolation. All stations accessing a data
directory control the same topology; input locks are cooperative controls that
another station can take over after confirmation. Public internet exposure and
untrusted multi-tenant hosting are outside this preview's scope.

The supplied deployment targets x86-64 Linux with rootful Docker. QEMU devices
require KVM. Host networking, TAP access and the Docker socket are part of the
PC implementation. Docker Desktop and rootless Docker are not supported setups.
See [installation](installation.md).

## Devices and data

- Supported images are the [documented profiles](devices.md), not arbitrary QCOW2
  operating systems. Features vary by image/release; a booting device does not
  prove that every switching/routing protocol is implemented.
- Extreme Networks Virtual EXOS and Arista vEOS support consoles, links and persistent
  disks, but not initial configuration snippets or configuration-text extraction.
- Junos profiles are experimental. vJunos-switch forwarding was verified on bare
  metal; nested-VM forwarding failed. Junos snippets, config-text export and
  carrier control are unavailable; real-guest ZIP restore verification remains
  incomplete. Shut down Junos through its CLI before Weblab Stop.
- FRR supports routing, initial/saved configuration and ZIPs; live config capture,
  general Linux filesystem persistence and STP/MSTP switching are not implemented.
- Alpine PCs have one interface and ephemeral filesystems. They do not provide
  persistent server/container volumes through this UI.
- Saved ZIP preserves supported storage, not VM memory or unsaved running config.
- Startup snippets affect fresh Cisco/FRR storage only. Existing storage wins.
- Topology import accepts JSON, not YAML. Limits are 64 nodes, 256 links, 1 MB
  topology JSON and 16 KiB per startup snippet; ZIP limits are documented in
  [backups](backups.md).

## Workspace and exercises

Window positions and terminal scrollback belong to the current browser page;
reloading does not synchronize another station's window layout. Shared consoles
are one CLI session. Instructions are local to the browser tab and are not
embedded in topology/ZIP exports. Relative files in Instructions must be opened
manually, and HTML and images are not rendered as active content.

Phone keyboard/fullscreen behavior depends on the browser and available viewport.

## Link faults

Directional frame loss is available across supported device families. Carrier
Unplug/Reconnect is limited to IOSv/IOSvL2 and the two exact tested IOL 17.18.02
profiles, plus the tested FRR container profile. The UI reports requested state, not an instant guest acknowledgment.
Faults are runtime-only and are not included in backups. Stop the lab before
changing cable endpoints.
