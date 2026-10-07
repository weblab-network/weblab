# Storage monitoring

[Help index](README.md) · [Saving and restoring labs](backups.md)

Open **Storage** in the main toolbar or the floating topology toolbar. Its label
turns amber for low space and red for critical space. The report shows:

- Available space and capacity for the lab-data, image and temporary-file
  filesystems as seen by Weblab. Rows can refer to the same filesystem;
  their free space must not be added together.
- Allocated host space for each node directory, with VM disks and logs broken
  out. This includes historical disks for that node, not just its selected image.
- **Retained** directories belonging to IDs outside the current topology.
  They remain untouched; removing a device from the map does not erase saved data.

The report uses file metadata without opening guest disks or taking QEMU locks.
It excludes symbolic links, including links to base images. Allocated space is
based on filesystem blocks rather than a sparse file's apparent length. It is an
estimate on filesystems with compression or shared/reflink blocks. Hardlinks are
counted once within a node directory. Mounted subdirectories are skipped, and
unreadable, changing or excessively large trees are reported as partial.

Node measurements are cached for ten seconds. An open report refreshes every
15 seconds and has a Refresh button. Filesystem warnings update with normal
workspace polling. The report does not measure guest filesystem free space,
Docker's independently mounted storage, every file in lab-data, or the total
physical host disk. Export staging, images and other workloads also consume
space even when not included in the node totals.

## Warnings and operation checks

- **Low:** less than 2 GiB or 10% available.
- **Critical:** less than 256 MiB available.
- **Unknown:** a filesystem could not be measured.

Node starts require at least 256 MiB available in lab-data and 64 MiB in the
temporary filesystem. This is minimum headroom, not a guarantee that a VM has
enough room for its future writes.

Image uploads check their destination filesystem for the upload size plus
64 MiB. ZIP uploads check lab-data similarly. Restore then checks for the full
reconstructed device files plus 64 MiB, while both the uploaded ZIP and the old
lab still exist. Existing storage is not deleted to make room for a restore.

ZIP export uses a conservative uncompressed-file estimate, plus space for a
temporary compact vEOS encoding when selected, overhead and 64 MiB headroom.
This can exceed the eventual compressed ZIP size. Use a larger data volume if
the preflight refuses an export; disabling compact vEOS avoids that encoding's
extra temporary-space requirement. Saved-config extraction retains its existing
virtual-disk-size plus 64 MiB check.

Checks are not disk reservations: other processes and running guests can consume
space afterward. Warnings do not automatically delete files or stop devices.
Stop remains available when space is low, although saving device state itself
still requires writable storage. Back up important labs before manual cleanup.

## Record growth over time

An enabled [MCP adapter](automation.md#storage-reports) exposes the same report
through `get_storage_report()`, without enabling topology writes or console input.

The read-only `GET /api/storage` endpoint returns a UTC measurement timestamp,
filesystem capacity and per-node allocated byte counts. For a local deployment:

```sh
curl --fail --silent --show-error http://127.0.0.1:8080/api/storage >> storage-history.jsonl
printf '\n' >> storage-history.jsonl
```

The same metadata-only measurement can run independently of the server. From a
source checkout, supply the actual directories:

```sh
python3 storage.py --data-dir /path/to/lab-data --image-dir /path/to/images >> storage-history.jsonl
```

Or, for a container built with this feature, replacing `weblab` with its name:

```sh
docker exec weblab python3 /app/storage.py --data-dir /data --image-dir /iou >> storage-history.jsonl
```

Each sample is one JSON line. Take another after several hours, keeping the same
node IDs and image selection. Compare `free_bytes` for the data filesystem and
each node's `disk_bytes`/`log_bytes`. A recreated node or restored lab is a new
baseline. Guest `show system storage` readings are separate evidence.

## Junos disks

Trace cleanup inside Junos can free guest filesystem space without shrinking
the host QCOW2 allocation. Guest discard support, virtual disk settings and host
storage all affect reclamation. Weblab does not currently enable discard for
these disks or perform automatic offline disk compaction. Never run repair,
conversion or compaction against a live guest disk.

Keep the [Junos shutdown sequence](devices.md#juniper-vjunos): commit, issue
`request system power-off`, wait for guest shutdown, then stop the node/lab or
container. Storage monitoring does not change Stop semantics or verify guest
shutdown. Automatic graceful shutdown and complete real-guest ZIP restoration
coverage remain future work.
