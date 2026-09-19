"""QEMU image validation, persistent disks, and IOSv/EXOS/vEOS launch profiles."""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import shutil
import struct

ABOOT_IMAGE = "Aboot-veos-serial-8.0.2.iso"


def is_qemu(node):
    return node.get("type") != "pc" and node.get("image", "").endswith(".qcow2")


def is_exos(node):
    return is_qemu(node) and node['image'].lower().startswith(('exos-vm_', 'exos-vm-'))


def is_vios(node):
    return is_qemu(node) and not is_exos(node) and not is_veos(node)


def is_veos(node):
    return is_qemu(node) and node['image'].lower().startswith(('veos64-lab-', 'veos-lab-'))


def required_images(nodes):
    names = {n['image'] for n in nodes if n['type'] != 'pc'}
    if any(is_veos(n) for n in nodes):
        names.add(ABOOT_IMAGE)
    return names


def validate_aboot(path, error):
    """Check ISO9660 and El Torito records before attaching boot media."""
    try:
        if not path.is_file() or not 32768 < path.stat().st_size <= 64 * 1024**2:
            raise ValueError('missing or invalid size')
        with path.open('rb') as stream:
            stream.seek(16 * 2048)
            primary, boot = stream.read(2048), stream.read(2048)
        if (primary[:7] != b'\x01CD001\x01' or boot[:7] != b'\x00CD001\x01'
                or boot[7:39].rstrip(b'\x00 ') != b'EL TORITO SPECIFICATION'):
            raise ValueError('not a bootable ISO9660 image')
    except (OSError, ValueError) as exc:
        raise error(f'Arista requires a valid {ABOOT_IMAGE} in the image directory: {exc}') from exc


def boot_image(node, image_dir, error):
    if not is_veos(node):
        return None
    path = image_dir / ABOOT_IMAGE
    validate_aboot(path, error)
    return path


def require_kvm(error):
    try:
        with open("/dev/kvm", "rb+") as kvm:
            if fcntl.ioctl(kvm, 0xAE00) != 12:  # KVM_GET_API_VERSION
                raise OSError("Unsupported KVM API")
    except OSError as exc:
        raise error("QEMU devices require usable /dev/kvm. Enable virtualization on the host; "
                    "for Compose, include compose.kvm.yaml when recreating the stopped lab") from exc


def validate_image(path, run, error, backing_name=None):
    # Reject external dependencies before asking QEMU to parse the image.
    with Path(path).open("rb") as stream:
        header = stream.read(104)
    if len(header) < 72 or header[:4] != b"QFI\xfb":
        raise error("Upload a valid QCOW2 disk image (.qcow2)")
    version, backing_offset, backing_size = struct.unpack_from(">IQI", header, 4)
    if version not in (2, 3) or (version == 3 and len(header) < 104):
        raise error("Only QCOW2 version 2 or 3 images are supported")
    if backing_name is None and (backing_offset or backing_size):
        raise error("Upload a standalone QCOW2 image without a backing file")
    if backing_name is not None:
        if not backing_offset or not 0 < backing_size <= 255:
            raise error("Saved QEMU disk is missing its expected backing image")
        with Path(path).open("rb") as stream:
            stream.seek(backing_offset)
            actual_backing = stream.read(backing_size)
        if actual_backing != backing_name.encode():
            raise error("Saved QEMU disk references an unexpected backing image")
    if struct.unpack_from(">I", header, 32)[0]:
        raise error("Encrypted QCOW2 images are not supported")
    if version == 3 and struct.unpack_from(">Q", header, 72)[0] & 4:
        raise error("QCOW2 external data files are not supported")
    if not shutil.which("qemu-img"):
        raise error("Install qemu-utils to validate QEMU images, or rebuild the lab container")
    info = json.loads(run("qemu-img", "info", "--output=json", "-f", "qcow2", str(path)))
    if not 0 < info.get("virtual-size", 0) <= 64 * 1024**3:
        raise error("QEMU virtual disk size must be between 1 byte and 64 GiB")


def disk_paths(node, cwd):
    stem = ("veos-" if is_veos(node) else "exos-" if is_exos(node) else "vios-") + hashlib.sha256(node["image"].encode()).hexdigest()[:20]
    return cwd / (stem + "-base.qcow2"), cwd / (stem + ".qcow2")


def disk_for(node, cwd, image_dir, run, error):
    base = image_dir / node["image"]
    validate_image(base, run, error)
    # A distinct disk per image avoids silently booting an old image after selection
    # changes. Relative backing names let a stopped lab move between installations.
    backing, disk = disk_paths(node, cwd)
    if os.path.lexists(backing):
        if not backing.is_symlink():
            raise error(f"Expected an image symlink at {backing}")
        if backing.resolve() != base.resolve():
            backing.unlink()
    if not os.path.lexists(backing):
        backing.symlink_to(base)
    if not disk.exists():
        temporary = disk.with_suffix(".tmp")
        try:
            run("qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", backing.name, str(temporary))
            temporary.replace(disk)
        finally:
            temporary.unlink(missing_ok=True)
    return disk


def config_disk(node, cwd, run, error):
    """IOSv's first-boot FAT disk; never attach to an existing writable disk."""
    if not shutil.which("mformat") or not shutil.which("mcopy"):
        raise error("Install mtools or rebuild the container to use IOSv startup_config")
    config = cwd / "ios_config.txt"
    checksum = cwd / "ios_config_checksum"
    config.write_text(node["startup_config"], encoding="utf-8")
    checksum.write_text(hashlib.md5(config.read_bytes()).hexdigest() + "\n", encoding="ascii")
    image = cwd / "initial-config.img"
    temporary = cwd / "initial-config.tmp"
    temporary.unlink(missing_ok=True)
    try:
        # IOSv expects a partitioned DOS disk, not a superfloppy image. Use a
        # legacy CHS geometry matching the disks formatted by IOSv itself.
        mbr = bytearray(512)
        struct.pack_into("<B3sB3sII", mbr, 446, 0x80, b"\x01\x01\x00", 0x01,
                         b"\x0f\x3f\x07", 63, 8001)
        mbr[510:512] = b"\x55\xaa"
        with temporary.open("wb") as stream:
            stream.write(mbr)
            stream.truncate(4 * 1024 * 1024)
        partition = str(temporary) + "@@32256"
        run("mformat", "-i", partition, "-T", "8001", "-h", "16", "-s", "63",
            "-c", "8", "-R", "8", "-H", "63", "::")
        run("mcopy", "-i", partition, str(config), "::ios_config.txt")
        run("mcopy", "-i", partition, str(checksum), "::ios_config_checksum")
        temporary.replace(image)
    finally:
        temporary.unlink(missing_ok=True)
    return image


def command(node, disk, socket_dir, config=None, boot=None):
    exos, veos = is_exos(node), is_veos(node)
    if veos and boot is None:
        raise ValueError(f'Arista requires {ABOOT_IMAGE}')
    if config is not None and not is_vios(node):
        raise ValueError('Initial config disks are only supported for IOSv')
    interface = "ide" if exos or veos else "virtio"
    adapter = "virtio-net-pci" if veos else "rtl8139" if exos else "e1000"
    # EXOS 33.1 classifies x86 using the CPU model-name string. An AMD host
    # name sends it into a development-board boot path. Keep host features
    # and vendor ID; override only the guest-visible description for EXOS.
    cpu = "host,model-id=Intel-compatible virtual CPU" if exos else "host"
    args = ["-name", node["id"], "-machine", "pc,accel=kvm", "-cpu", cpu,
            "-smp", "2" if veos else "1", "-m", str(node["memory"]), "-display", "none",
            "-monitor", "none", "-qmp", f"unix:{socket_dir / 'qmp'},server=on,wait=off", "-serial", "stdio", "-boot", "order=dc" if veos else "c",
            "-drive", f"file={str(disk).replace(',', ',,')},format=qcow2,if={interface},cache=writeback"]
    if veos:
        args += ["-drive", f"file={str(boot).replace(',', ',,')},format=raw,media=cdrom,if=ide,index=2,readonly=on"]
    if config is not None:
        args += ["-drive", f"file={str(config).replace(',', ',,')},format=raw,if=virtio"]
    for index in range(node["ethernet"]):
        # Locally administered, stable across restarts; unique within the lab.
        mac = "02:" + ":".join(f"{b:02x}" for b in hashlib.sha256(
            f"{socket_dir.parent}:{node['id']}:{index}".encode()).digest()[:5])
        path = str(socket_dir / str(index)).replace(",", ",,")
        args += ["-netdev", f"stream,id=n{index},server=off,addr.type=unix,addr.path={path}",
                 "-device", f"{adapter},netdev=n{index},id=nic{index},mac={mac}"]
    return args
