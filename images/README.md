# Local device images

Place your own IOL `.bin`, supported QCOW2 device images and required Arista Aboot
ISO here, or use **Upload image** in the web UI. Compose mounts this directory at
`/iou`; uploads are saved back here. See [device profiles](../docs/devices.md) for
exact filename prefixes, tested releases, ports and resource requirements.

Manually copied IOL `.bin` files need execute permission (`chmod +x images/*.bin`).
QCOW2 files need read permission and working KVM; per-node writable disks live in
the data directory, separate from these base images.

Weblab does not generate licenses. If an image requires `iourc`, supply it here
for the execution environment. Existing files are preserved and passed to IOL.
No vendor images or licenses are included in the source or application image.

Everything here except this README is ignored by Git and excluded from Docker
builds. Keep the base images available when restoring a saved lab ZIP.
