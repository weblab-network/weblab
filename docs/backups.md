# Saving, exporting and restoring labs

[Help index](README.md) · [Project overview](../README.md)

## Choose an export format

| Format | What it preserves | When to use it |
| --- | --- | --- |
| Topology JSON | Nodes, links, settings and original startup snippets | Share a topology or fresh exercise |
| JSON + saved configs | Topology with saved Cisco/FRR startup text | Share a saved configuration baseline; stop Cisco/FRR devices first |
| JSON + live configs | Topology with current Cisco running text | Share unsaved configuration; requires ready, unlocked privileged consoles |
| Saved lab ZIP | Topology and supported persistent device storage | Resume a saved lab, including IOL VLAN databases, QEMU disks and FRR configuration; stop all nodes first |

A ZIP is a saved-storage backup, not a VM memory snapshot. Device images and
licenses are not included. Instructions documents and window positions are not
part of topology/ZIP exports. EXOS/Arista/Junos support topology JSON and saved ZIP,
but not configuration-text export or initial snippets.

## Where state is stored

The paths below use the default Compose host directory, `lab-data/`. Native
execution defaults to `.lab/` unless `--data-dir` is specified.

- `lab-data/topology.json`: saved map and device settings.
- `lab-data/NETMAP`: generated topology connections. A manual NETMAP in the application directory is preserved.
- `lab-data/nodes/NODE_ID/`: each IOL node's own working directory and NVRAM, plus launcher logs. Use IOS `write memory` before stopping to preserve configuration.
- `lab-data/runtime.json`: journal of owned processes and resources. A lock prevents two servers from managing the same directory.
- `lab-data/owner`: stable prefix for owned Docker and TAP resources.

The node working directory also contains links to the generated NETMAP and the
image directory's `iourc`; `IOURC` explicitly points the IOL process at its license.
NETMAP is generated at node launch, including for a node with no cables. It does
not belong in `/iou` for managed nodes. When a console process exits unexpectedly,
its last 16 KiB of output is written to the launcher log so license/NETMAP errors
are visible even when IOL exits with status 0. Normal stops do not dump this output.

IOL IDs are allocated between 100 and 1023, excluding the current manual NETMAP and existing IOU sockets. Each PC receives a pseudo IOL ID and its own TAP, macvlan network, and Alpine container. NETMAP places that pseudo ID on the right, with hostnames as required by this `iou2net.pl`. Docker uses a private provisioning /30 from `198.18.0.0/15`; the PC's provisioning address is removed before applying the configured lab address. PC lab interfaces are not automatically connected to the host management network.

Stopping an IOL node also stops its attached PCs. Stopping the app with Ctrl-C or SIGTERM stops its managed devices and removes their Docker containers, macvlan networks and TAPs. Saved IOL NVRAM and the topology remain. After an interrupted server, its journal is used to clean up its own resources on the next start; nodes then start stopped. A failed **Start lab** rolls back nodes newly started by that operation, retaining devices that were already running.

PC containers are recreated on start: shell-installed packages, files, and manual interface changes are ephemeral. Store the startup IPv4 address/gateway in the inspector; these settings transfer with either export format.

## Initial configurations in topology JSON

A Cisco router/switch node can include an optional `startup_config` string (maximum 16 KiB UTF-8):

```json
"startup_config": "hostname R1\ninterface Ethernet0/0\n ip address 10.0.12.1 255.255.255.252\n no shutdown\nend\n"
```

Use IOS configuration-file text, without console prompts, `enable`, `configure terminal`, `show` or `write memory`. Control characters are rejected. PCs use their existing JSON `ipv4` and `gateway` fields; arbitrary PC shell scripts are not accepted as `startup_config`.

Snippets initialize only fresh devices. IOL receives newly created NVRAM containing the configuration; IOSv/IOSvL2 receives a first-boot FAT configuration disk with `ios_config.txt` and its checksum. Existing IOL NVRAM or the current image’s writable IOSv disk always takes precedence, including after ZIP import. Snippets are retained in topology JSON for sharing but are not reapplied when you edit them, restart a device or import over existing node storage. An interrupted first boot may already have created device state; it is conservatively treated as existing state. Let the initial boot finish and inspect the console for IOS configuration errors.

Use distinct node IDs/a fresh lab for a clean exercise. Importing JSON with old node IDs is not a reset. Plain **Topology JSON** exports the stored initial snippets, not a capture of later device changes. For first boot, Weblab creates IOL NVRAM from the snippet or attaches an IOSv FAT seed disk containing `ios_config.txt` and its checksum. IOSv seeding requires `mtools`, included in the Docker image.

## Export an example lab with current configurations

Choose the source in **Export**. Both configuration options put text into each Cisco device's `startup_config` field, preserve PC address/gateway settings, and leave the active topology's snippets unchanged. The resulting JSON initializes fresh devices; restored/existing device state still wins.

| Option | Configuration source | Device requirements |
| --- | --- | --- |
| **JSON + saved configs** | Last saved startup configuration in IOL NVRAM or the current IOSv disk | Cisco devices stopped; no console access needed |
| **JSON + live configs** | Current running configuration, including unsaved changes | Cisco devices running at unlocked privileged EXEC prompts |
| **Topology JSON** | Original snippets already stored in the topology | Any state |

Saved configs are the primary option for sharing a saved baseline. The application reads storage without issuing any console commands, changing logging, or saving configuration. Unsaved changes are excluded. Save changes before stopping if you want them included. Missing, corrupt, ambiguous or unsupported storage fails the whole export; the error points to **JSON + live configs** as an explicit alternative. Neither mode automatically substitutes a different configuration source or falls back to the original seed file/snippet.

`saved_config.py` validates the NVRAM format, record bounds and one's-complement checksum. It supports the installed IOL 64 KiB and IOSv/IOSvL2 512 KiB layouts, including IOS LZW-compressed configuration records via bounded `gzip -dc` decompression. IOSv extraction uses QEMU to read the current overlay/backing chain into a private sparse raw file, then mtools to read `nvram` from a FAT partition. It never mounts a filesystem or writes the original disk. The supported virtual disk limit is 8 GiB; allow free temporary space equal to the virtual disk size plus 64 MiB (normal sparse usage is smaller). Temporary files are removed on success and errors. `qemu-utils`, `mtools` and `gzip` are included in Docker. Unknown formats require live capture or a ZIP backup.

Saved JSON includes startup configuration text only, not private NVRAM records, VLAN databases or arbitrary flash files. Use **Saved lab ZIP** to preserve device storage. Banner control-C delimiters are converted to printable delimiters while preserving the message. Like live export, saved JSON is limited to 16 KiB per configuration and 1 MB overall.

For **JSON + live configs**, the following console safeguards apply:

All Cisco devices must be running, fully booted, at the privileged EXEC (`#`) prompt outside configuration mode, and have their input locks released. Capture acquires each console’s input lock and clears any unfinished input line. It reads the existing console logging settings and, if enabled, temporarily applies `no logging console` and verifies suppression before reading `show running-config`. Already-disabled console logging is left alone. Queued log messages cause a bounded retry of the entire affected query/capture; persistent log-like output, including such text in banners, is rejected rather than stripped from the configuration.

After capture, the original logging commands (including severity/filter/format options, or the implicit default) are restored and verified. The exported configuration receives the original settings too; it does not inherit the temporary `no logging console`. Export never issues `write memory` or `copy running-config startup-config`, and it does not change buffered/remote logging settings. The temporary configuration commands can update IOS configuration-change timestamps and generate log records.

On capture errors, cleanup attempts to restore logging while it still owns the console. If the console disconnects or another station takes over, export does not take input back forcibly. If restoration cannot be verified, no file is exported and the error includes manual recovery commands for that device. Review those instructions before saving device configuration. Other stations can observe the capture output. Stopped/not-ready devices, held locks, and incomplete/oversized output also fail the whole export. No device credentials are supplied automatically. Captured configurations are plain text in the exported JSON.

The configuration limit is 16 KiB per device and the complete JSON import limit is 1 MB. Use a saved lab ZIP for larger configurations or complete disk/NVRAM state. Exported running configuration may contain commands specific to the image/version, so use matching device images when sharing it.

Capture removes terminal paging controls and converts IOS's displayed `^C` banner delimiters to single printable delimiters, preserving the banner message for configuration-file import. Use a printable delimiter when writing banner snippets by hand as well.

For agent-assisted original practice labs, read [practice lab guide](practice-labs.md). An example baseline and exercise are in [ospf-practice.json](../examples/ospf-practice.json) and [ospf-practice.md](../examples/ospf-practice.md). Validate generated JSON without starting nodes or writing lab data:

```sh
python3 tools/validate_topology.py examples/ospf-practice.json --image-dir images
```

The validator checks topology shape, ports, addresses, snippet limits and (with `--image-dir`) installed image names. IOS syntax and feature support require separate device testing. YAML is not an import format.

## Export and restore saved configurations

1. Save configurations inside each device (for example, IOS `write memory`), then **Stop lab**. Export does not issue console commands or save unsaved running configurations.
2. Choose **Export → Saved lab ZIP**. The browser downloads a compressed archive containing `topology.json`, a versioned `manifest.json`, and saved device files. ZIP avoids base64 overhead; uploads and downloads stream through temporary files instead of loading disks into server/browser memory.
3. On the destination, install the same device images (and Arista Aboot where required) with the same filenames. Base images and licenses are excluded; SHA-256 checksums verify the installed image contents before restoring.
4. Stop the destination lab, choose **Import**, select the ZIP, then **Restore lab**. This replaces the active topology and its saved device state. Export the old lab first if you want to keep it. Restored devices remain stopped.

The ZIP includes IOL `nvram_<application-ID>`, `vlan.dat-<application-ID>` (for example, `vlan.dat-00100`), legacy `vlan.dat`, `startup-config`, `private-config`, and regular files under `CRDU/`, `flash/`, `flash0/`, `nvram/` and `pnp-info/`. It includes the current image's IOSv, EXOS, Arista or Junos QCOW2 overlay for each node; overlays belonging to previously selected images are excluded. Runtime files, diagnostic dumps, symlinks and temporary Alpine files are excluded. Logs are excluded unless selected as described below. Newly created devices may have no saved files yet. Keep a stopped copy of the entire data directory if you also need historical disks or other files outside this list.

An archive missing a VLAN database cannot restore its VLAN definitions. Keep the
database alongside NVRAM when preserving a configured switch.

Select **Include available console and launcher logs in ZIP** before saving to
add `logs/README.txt` and `logs/nodes/<node-ID>/` to the archive. This includes
retained `console-output.log.1`, `console-output.log`, `console.log` and
`bridge.log` when present, for every node in the topology, including PCs. The
first two are terminal output; the others are launcher diagnostics. Extract
`logs/` from the ZIP to read them. The manifest includes checksums, export time,
original sizes and offsets for truncated files. Imports validate the log entries
but do not install them into device storage or merge them into local history.
ZIPs with logs require an importer supporting this optional section; export
without logs for older versions.

Terminal output is recorded on the server from device start, even with no browser
console open. Recording appends across restarts of
the same node ID, retaining two 4 MiB segments (up to 8 MiB); the `.1` segment is
older. UTC markers identify session starts/ends; terminal output otherwise stays
raw, including ANSI escapes and the guest's timestamps. Commands appear when
the device echoes them. Non-echoed passwords and browser lock-control messages
are not logged separately. Recording errors appear in launcher logs and do not
stop the console. Older output cannot be recovered retroactively. The ZIP also
includes at most the last 8 MiB of each launcher log. These are application
console/launcher logs, not a collection of every guest or host system log.
Logs can contain configuration details or printed secrets: review before sharing.

Backups are limited to 8 GiB compressed/device data and 4096 entries. A pending download expires after 15 minutes; up to two may be prepared at once. Import checks paths, file types, lengths, checksums and IOSv backing references before installation. It stages the restore and keeps the previous state until installation commits; a recovery journal rolls back an interrupted installation on server startup. IOL NVRAM and ID-suffixed VLAN database filenames are remapped if an application ID changes on import.

ZIP installation replaces the whole `nodes/` tree, including removal of old
nodes and files absent from the archive; it does not merge device storage.
The rest of `lab-data` is not purged: the manager's lock, ownership and recovery
metadata must remain intact. No manual folder cleanup is needed before import.

**Topology JSON** remains available while devices run and contains no saved device files, but retains any original `startup_config` snippets. With a JSON import, existing local files are reused when node/application IDs match; JSON import does not migrate NVRAM when an application ID changes.

These paths are relative to `--data-dir`; Compose uses `/data`, backed by `./lab-data`
on the host. For another independent workspace:
`./start-lab.sh --port 8081 --data-dir /path/to/another-lab`.
Each manager checks ID conflicts when starting; if another lab has claimed a saved
ID, recreate that stopped node to allocate a free one.

## Compact vEOS disk backups

For labs containing Arista vEOS, **Compact vEOS disks in ZIP** is enabled by
default in the export dialog. It stores references to matching blocks in the
installed base image instead of repeating them in the ZIP. This can substantially
reduce backups when vEOS has copied its boot image into the writable disk.
The saved disk and guest files are never modified. Restore reconstructs the exact
original QCOW2 bytes and checks both the encoded data and reconstructed disk
SHA-256 checksums before installing anything. All usual base-image/Aboot checks
and transactional restore protections still apply.

Compact archives use backup format version 2 with `.qcow2.wl-delta` entries.
They require a Weblab installation supporting this format; those entries are
not directly bootable disks. Turn the option off to produce a conventional
version-1 ZIP for older installations, unless the lab contains FRR nodes
(which require version 2). Existing version-1 archives remain
importable. Topology JSON and configuration-text exports are unaffected.

Compaction is used only for vEOS disks at least 1 MiB in size, with a base image
file at most 1 GiB, and when block references save at least 10% of the uncompressed
file size. Otherwise the ordinary disk entry is used automatically. Actual ZIP
savings depend on disk contents. The block index uses additional memory (tens of
MiB for typical images), and export needs temporary disk space for the encoded
file alongside the ZIP. The 8 GiB device-data limit applies to the reconstructed
files as well as the stored payload. This does not save unsaved running configs:
save inside the device and stop the lab first.


## FRR configuration

FRR supports initial `startup_config` snippets using FRR configuration-file syntax.
They seed only fresh nodes. Keep interface addresses, routes and routing protocols
in FRR; arbitrary Linux commands are not startup snippets. Save with `write memory`
and then Stop: Weblab collects the saved `frr.conf` before removing the container.
Unsaved changes are intentionally excluded. If collection fails, the container is
retained and Stop reports the error; retry after resolving it.

Stopped **JSON + saved configs** includes this file as a fresh-device snippet,
subject to the 16 KiB limit. FRR live console capture is not implemented. Saved
lab ZIP preserves the configuration up to 1 MiB and records the pinned container
image identity. Tested images retain their pinned registry digest; explicitly
allowed untested images record their immutable local Docker image ID. FRR ZIPs
use backup format version 2 and require an FRR-capable Weblab importer with the
matching Docker image installed. Custom-image ZIPs also require
`WL_ALLOW_UNTESTED_FRR=1`; it never bypasses archive image matching. Older Weblab
versions reject those custom-image ZIPs. Use `docker save` / `docker load` to
transport a local image separately. No container image,
daemon shell settings or arbitrary guest filesystem data is included. Existing
FRR configuration overrides snippets, including after ZIP restore.


Junos ZIP storage is covered by synthetic-disk round-trip tests; a complete
real-guest restore/forwarding test remains outstanding. Use `commit` and shut
down Junos with `request system power-off` and wait for shutdown before
Stop/export; keep the original base image. The Stop confirmation is a reminder,
not an automatic guest shutdown.
