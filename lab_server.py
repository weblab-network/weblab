#!/usr/bin/env python3
"""Weblab topology manager. See docs/README.md."""
import argparse
import copy
import fcntl
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import tempfile
import time
import uuid
import vios
import frr
import lab_backup
import console_capture
import saved_config
import initial_config
import link_fabric
import qmp
import iol_l1
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
MAX_IMAGE_BYTES = 1024 * 1024 * 1024


class LabError(Exception):
    pass


def run(*args, timeout=30):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LabError(str(exc)) from exc
    if result.returncode:
        raise LabError((result.stderr or result.stdout or "Command failed").strip()[-3000:])
    return result.stdout.strip()


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def process_stamp(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def ports(node):
    if node["type"] == "pc":
        return ["eth0"]
    if frr.is_frr(node):
        return [f"eth{i}" for i in range(node["ethernet"])]
    if vios.is_exos(node):
        return ["Mgmt"] + [str(i) for i in range(1, node["ethernet"])]
    if vios.is_veos(node):
        return ["Management1"] + [f"Ethernet{i}" for i in range(1, node["ethernet"])]
    if vios.is_junos(node):
        return (["re0:mgmt-0"] + [f"et-0/0/{i}" for i in range(node["ethernet"]-1)]
                if vios.is_junos_evolved(node) else
                ["fxp0"] + [f"ge-0/0/{i}" for i in range(node["ethernet"]-1)])
    if vios.is_qemu(node):
        return [f"Gi{index // 4}/{index % 4}" if node["type"] == "switch" else f"Gi0/{index}"
                for index in range(node["ethernet"])]
    return [f"{slot}/{port}" for slot in range(node["ethernet"]) for port in range(4)]


def netmap_port(node, port):
    if node["type"] == "pc":
        return "0/0"
    if vios.is_qemu(node) or frr.is_frr(node):
        index = ports(node).index(port)
        return f"{index // 4}/{index % 4}"
    return port


class Lab:
    def __init__(self, directory, image_dir=None):
        self.image_dir = (image_dir if image_dir is not None else ROOT).resolve()
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.upload_lock = threading.Lock()
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.file_lock = (self.directory / "server.lock").open("w")
        try:
            fcntl.flock(self.file_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.file_lock.close()
            raise LabError("A server already manages this data directory") from exc
        lab_backup.recover(self.directory)
        self.exports = {}
        self.lock = threading.RLock()
        self.fabric = None
        self.l1 = None
        self.runtime = {}
        self.errors = {}
        self.children = {}
        self.owner_path = self.directory / "owner"
        if not self.owner_path.exists():
            self.owner_path.write_text(uuid.uuid4().hex[:8])
        self.owner = self.owner_path.read_text().strip()
        self.topology_path = self.directory / "topology.json"
        self.runtime_path = self.directory / "runtime.json"
        self.netio = Path(f"/tmp/netio{os.getuid()}")
        self.netl1 = Path(f"/tmp/netl1{os.getuid()}")
        self.topology = (json.loads(self.topology_path.read_text()) if self.topology_path.exists()
                         else {"version": 1, "name": "Untitled lab", "nodes": [], "links": []})
        # Recover only resources journaled by this manager, never manual labs.
        if self.runtime_path.exists():
            self.runtime = json.loads(self.runtime_path.read_text())
            for node_id in list(self.runtime):
                self.stop(node_id, dependents=False)
        link_fabric.recover(self.directory, self.netio)
        iol_l1.recover(self.directory, self.netl1)
        self.persist()

    def catalog(self):
        return [{"name": p.name, "type": "switch" if "l2" in p.name.lower() or vios.is_exos({"image":p.name}) or vios.is_veos({"image":p.name}) or vios.is_junos_switch({"image":p.name}) else "router"}
                for p in sorted(self.image_dir.iterdir()) if p.is_file() and
                ((p.suffix == ".bin" and os.access(p, os.X_OK)) or
                 (p.suffix == ".qcow2" and os.access(p, os.R_OK)))] + [{"name": frr.IMAGE, "type": "router"}]

    def upload_image(self, name, stream, length):
        iso = name == vios.ABOOT_IMAGE
        if not iso and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_. -]{0,194}\.(bin|qcow2)", name):
            raise LabError(f"Use a .bin or .qcow2 filename containing letters, numbers, spaces, dots, underscores or hyphens; Arista boot media must be named {vios.ABOOT_IMAGE}")
        qcow = name.endswith(".qcow2")
        if not 64 <= length <= MAX_IMAGE_BYTES:
            raise LabError("Image size must be between 64 bytes and 1 GiB")
        target = self.image_dir / name
        if os.path.lexists(target):
            raise LabError("An image with this name already exists; choose a different filename")
        if not self.upload_lock.acquire(blocking=False):
            raise LabError("Another image upload is in progress")
        try:
            # Stream into the destination filesystem, then publish without overwriting.
            # Neither partial nor failed uploads are visible in the image catalog.
            with tempfile.NamedTemporaryFile(dir=self.image_dir, prefix=".upload-", suffix=".tmp") as temporary:
                remaining, header = length, b""
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise LabError("Incomplete image upload")
                    if not iso and len(header) < 4:
                        header += chunk[:4 - len(header)]
                        if len(header) == 4 and header != (b"QFI\xfb" if qcow else b"\x7fELF"):
                            raise LabError("Upload a QCOW2 disk (.qcow2) or an uncompressed IOL ELF binary (.bin) matching the filename")
                    temporary.write(chunk)
                    remaining -= len(chunk)
                temporary.flush()
                if qcow:
                    vios.validate_image(Path(temporary.name), run, LabError)
                if iso:
                    vios.validate_aboot(Path(temporary.name), LabError)
                os.fchmod(temporary.fileno(), 0o644 if qcow or iso else 0o755)
                os.fsync(temporary.fileno())
                try:
                    os.link(temporary.name, target)
                except FileExistsError as exc:
                    raise LabError("An image with this name already exists") from exc
        finally:
            self.upload_lock.release()
        return self.snapshot()

    def persist(self):
        atomic_json(self.topology_path, self.topology)
        atomic_json(self.runtime_path, self.runtime)

    def journal(self):
        atomic_json(self.runtime_path, self.runtime)

    def node(self, node_id):
        for node in self.topology["nodes"]:
            if node["id"] == node_id:
                return node
        raise LabError("Node does not exist")

    def snapshot(self):
        with self.lock:
            return {"topology": copy.deepcopy(self.topology),
                    "status": {n["id"]: {"state": "running" if n["id"] in self.runtime else
                                         "error" if self.errors.get(n["id"]) else "stopped",
                                         "error": self.errors.get(n["id"], "")}
                               for n in self.topology["nodes"]},
                    "images": self.catalog(),
                    "link_state": self.link_states(),
                    "link_error": self.fabric.error if self.fabric else ""}

    def occupied_ids(self):
        occupied = set()
        if self.netl1.exists():
            occupied.update(int(p.name[2:]) for p in self.netl1.iterdir()
                            if p.name.startswith('L1') and p.name[2:].isdigit())
        if self.netio.exists():
            occupied.update(int(p.name) for p in self.netio.iterdir() if p.name.isdigit())
        if (ROOT / "NETMAP").exists():
            occupied.update(map(int, re.findall(r"\b(\d+):", (ROOT / "NETMAP").read_text())))
        return occupied

    def validate(self, data):
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list) or not isinstance(data.get("links"), list):
            raise LabError("Expected a topology with nodes and links")
        if len(data["nodes"]) > 64 or len(data["links"]) > 256:
            raise LabError("Limit: 64 nodes and 256 links per lab")
        name = str(data.get("name", "Untitled lab")).strip()
        if not 1 <= len(name) <= 80:
            raise LabError("Lab name must contain 1–80 characters")
        result = {"version": 1, "name": name, "nodes": [], "links": []}
        existing = {n["id"]: n for n in self.topology["nodes"]}
        occupied = self.occupied_ids()
        occupied.update(existing[raw["id"]]["iol_id"] for raw in data["nodes"]
                        if isinstance(raw, dict) and isinstance(raw.get("id"), str) and raw["id"] in existing)
        ids = set()
        for raw in data["nodes"]:
            if not isinstance(raw, dict):
                raise LabError("Invalid node")
            node_id = str(raw.get("id", ""))
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", node_id) or node_id in ids:
                raise LabError("Node IDs must be unique letters, digits, underscores or hyphens")
            ids.add(node_id)
            kind = raw.get("type")
            if kind not in ("switch", "router", "pc"):
                raise LabError("Unknown device type")
            label = str(raw.get("name", "")).strip()
            if not 1 <= len(label) <= 40:
                raise LabError("Node names must contain 1–40 characters")
            node = {"id": node_id, "type": kind, "name": label}
            image = str(raw.get("image", "alpine:latest" if kind == "pc" else ""))
            qcow = kind != "pc" and image.endswith(".qcow2")
            exos = vios.is_exos({"type":kind,"image":image})
            veos = vios.is_veos({"type":kind,"image":image})
            junos = vios.is_junos({"type":kind,"image":image})
            evolved = vios.is_junos_evolved({"type":kind,"image":image})
            if junos and kind != ("router" if evolved else "switch"):
                raise LabError("vJunosEvolved requires a router; vJunos-switch requires a switch")
            is_frr = frr.is_frr({"image": image})
            if is_frr and (kind != "router" or image != frr.IMAGE):
                raise LabError(f"FRR requires a router node and image {frr.IMAGE}")
            if (exos or veos) and kind != "switch":
                raise LabError(f"{'Arista vEOS' if veos else 'EXOS'} images require a switch node")
            for key, default, low, high in [("x", 350, 70, 2330), ("y", 250, 60, 1540),
                                           ("memory", 8192 if evolved else 5120 if junos else 512 if is_frr else 6144 if veos else 1024, 8192 if evolved else 5120 if junos else 256, 8192),
                                           ("ethernet", 5 if junos else 4 if is_frr else 5 if veos else 13 if exos else 4 if qcow else 2, 2 if exos or veos or junos else 1, 13 if exos else 16 if qcow else 8)]:
                value = raw.get(key, default)
                if type(value) not in (int, float) or not low <= value <= high or int(value) != value:
                    raise LabError(f"{key} must be an integer between {low} and {high}")
                node[key] = int(value)
            if kind == "pc":
                if not re.fullmatch(r"alpine(?::[a-zA-Z0-9_.-]+)?", image):
                    raise LabError("PC image must be alpine or alpine:TAG")
            elif not is_frr and image not in {i["name"] for i in self.catalog()}:
                raise LabError("Select an IOL .bin, supported .qcow2 image, or the supported FRR container")
            node["image"] = image
            snippet = raw.get("startup_config", "")
            if not isinstance(snippet, str) or len(snippet.encode("utf-8")) > 16_384:
                raise LabError("startup_config must be a string of at most 16 KiB")
            if any(ord(char) < 32 and char not in "\r\n\t" for char in snippet) or "\x7f" in snippet:
                raise LabError("startup_config must contain configuration text, not control characters")
            if snippet.strip():
                if junos:
                    raise LabError("Junos startup snippets are not supported yet; configure through its console")
                if exos or veos:
                    raise LabError(f"{'Arista vEOS' if veos else 'EXOS'} startup snippets are not supported yet; configure through its console")
                if kind == "pc":
                    raise LabError("PCs use ipv4 and gateway startup settings; startup_config is for IOL/IOSv devices")
                snippet = snippet.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
                if len(snippet.encode("utf-8")) > 16_384:
                    raise LabError("startup_config must fit within 16 KiB including its final newline")
                node["startup_config"] = snippet
            node["ipv4"] = str(raw.get("ipv4", "")).strip()
            node["gateway"] = str(raw.get("gateway", "")).strip()
            try:
                address = ipaddress.IPv4Interface(node["ipv4"]) if node["ipv4"] else None
                if address and "/" not in node["ipv4"]:
                    raise ValueError("Include an IPv4 prefix, e.g. 10.0.10.10/24")
                if node["gateway"]:
                    gateway = ipaddress.IPv4Address(node["gateway"])
                    if not address or gateway not in address.network:
                        raise ValueError("Gateway must belong to the PC subnet")
            except ValueError as exc:
                raise LabError(str(exc)) from exc
            if node_id in existing:
                node["iol_id"] = existing[node_id]["iol_id"]
            else:
                requested = raw.get("iol_id")
                available = (requested if type(requested) is int and 100 <= requested <= 1023 and requested not in occupied
                             else next((i for i in range(100, 1024) if i not in occupied), None))
                if available is None:
                    raise LabError("No free IOL application IDs")
                node["iol_id"] = available
                occupied.add(available)
            result["nodes"].append(node)
        by_id = {n["id"]: n for n in result["nodes"]}
        used, link_ids = set(), set()
        for raw in data["links"]:
            if not isinstance(raw, dict):
                raise LabError("Invalid link")
            link_id = str(raw.get("id", ""))
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", link_id) or link_id in link_ids:
                raise LabError("Link IDs must be unique letters, digits, underscores or hyphens")
            link_ids.add(link_id)
            link = {"id": link_id}
            for side in ("a", "b"):
                endpoint = raw.get(side, {})
                if not isinstance(endpoint, dict):
                    raise LabError("Invalid link endpoint")
                node_id, port = endpoint.get("node"), endpoint.get("port")
                if not isinstance(node_id, str) or not isinstance(port, str) or node_id not in by_id or port not in ports(by_id[node_id]):
                    raise LabError("Link references an unknown node or interface")
                if (node_id, port) in used:
                    raise LabError("Each interface can have only one cable")
                used.add((node_id, port))
                link[side] = {"node": node_id, "port": port}
            a, b = by_id[link["a"]["node"]], by_id[link["b"]["node"]]
            if a["id"] == b["id"] or a["type"] == b["type"] == "pc":
                raise LabError("Connect different nodes; a PC must connect to a router or switch")
            result["links"].append(link)
        return result

    @staticmethod
    def structure(topology):
        return {"nodes": sorted([{k: v for k, v in n.items() if k not in ("x", "y", "name")}
                                  for n in topology["nodes"]], key=lambda n: n["id"]),
                "links": sorted(topology["links"], key=lambda link: link["id"])}

    def save(self, data):
        with self.lock:
            validated = self.validate(data)
            if self.runtime:
                # Only address settings of PCs that are stopped may change while
                # another device runs. All other structural restrictions remain.
                comparison = copy.deepcopy(validated)
                old_nodes = {node["id"]: node for node in self.topology["nodes"]}
                for node in comparison["nodes"]:
                    old = old_nodes.get(node["id"])
                    if old and old["type"] == node["type"] == "pc" and node["id"] not in self.runtime:
                        for field in ("ipv4", "gateway"):
                            node[field] = old[field]
                if self.structure(comparison) != self.structure(self.topology):
                    raise LabError("Stop all nodes before changing devices, settings or cables; stopped PCs may change IPv4/gateway")
            self.topology = validated
            self.errors = {key: value for key, value in self.errors.items() if key in {n["id"] for n in validated["nodes"]}}
            self.persist()
            return self.snapshot()

    def link_states(self):
        faults = self.fabric.states() if self.fabric else {}
        l1_states = self.l1.states() if self.l1 else {}
        return {link['id']: {**faults.get(link['id'], {'blocked_a_to_b': False, 'blocked_b_to_a': False,
                                                     'carrier_a': 'up', 'carrier_b': 'up'}),
                            'available': bool(self.fabric and not self.fabric.error and
                                              any(link[side]['node'] in self.runtime for side in ('a', 'b'))),
                            **{'carrier_capable_' + side: bool(frr.is_frr(self.node(link[side]['node'])) or vios.is_vios(self.node(link[side]['node'])) or
                                                               iol_l1.supported(self.node(link[side]['node'])))
                               for side in ('a', 'b')},
                            **{'carrier_supported_' + side: bool((frr.is_frr(self.node(link[side]['node'])) or vios.is_vios(self.node(link[side]['node'])) or
                                                               self.runtime.get(link[side]['node'], {}).get('iol_l1_identity')) and
                                                               link[side]['node'] in self.runtime)
                               for side in ('a', 'b')},
                            **{field + '_' + side: value for side in ('a', 'b')
                               for field, value in {
                                   'carrier_iol': (link['id'], side) in l1_states,
                                   'carrier_pending': l1_states.get((link['id'], side), {}).get('pending', 0),
                                   'carrier_error': l1_states.get((link['id'], side), {}).get('error', '')}.items()}}
                for link in self.topology['links']}

    def set_link_carrier(self, link_id, data):
        with self.lock:
            if (not isinstance(data, dict) or set(data) != {'side', 'up'} or
                    data['side'] not in ('a', 'b') or type(data['up']) is not bool):
                raise LabError('Specify side a/b and boolean up')
            state = self.link_states().get(link_id)
            if state is None:
                raise LabError('Link does not exist')
            side = data['side']
            if not state['available'] or not state['carrier_supported_' + side]:
                raise LabError('Cable link-down requires a running supported IOSv, IOL or FRR interface')
            endpoint = next(link for link in self.topology['links'] if link['id'] == link_id)[side]
            node = self.node(endpoint['node'])
            runtime = self.runtime[node['id']]
            if runtime.get('iol_l1_identity'):
                try:
                    self.l1.set_carrier(link_id, side, data['up'])
                except iol_l1.FabricError as exc:
                    raise LabError(str(exc)) from exc
                return self.snapshot()
            self.fabric.set_carrier(link_id, side, 'unknown')
            try:
                if frr.is_frr(node):
                    tap = runtime['frr_ports'][ports(node).index(endpoint['port'])]['tap']
                    run('ip', 'link', 'set', 'dev', tap, 'carrier', 'on' if data['up'] else 'off')
                else:
                    qmp.set_link(Path(runtime['qemu_sockets']) / 'qmp', ports(node).index(endpoint['port']), data['up'])
            except (qmp.QMPError, LabError) as exc:
                raise LabError(f'{exc}. Link state is uncertain; traffic stays blocked. Use Reconnect on this endpoint to recover.') from exc
            self.fabric.set_carrier(link_id, side, 'up' if data['up'] else 'down')
            return self.snapshot()

    def set_link_traffic(self, link_id, data):
        with self.lock:
            if not isinstance(data, dict) or set(data) != {'blocked_a_to_b', 'blocked_b_to_a'} or any(type(v) is not bool for v in data.values()):
                raise LabError('Specify boolean blocked_a_to_b and blocked_b_to_a')
            state = self.link_states().get(link_id)
            if state is None:
                raise LabError('Link does not exist')
            if not state['available']:
                raise LabError(self.fabric.error if self.fabric and self.fabric.error else 'Start a connected node before changing link traffic')
            try:
                self.fabric.set_blocked(link_id, data['blocked_a_to_b'], data['blocked_b_to_a'])
            except link_fabric.FabricError as exc:
                raise LabError(str(exc)) from exc
            return self.snapshot()

    def ensure_fabric(self):
        if self.fabric:
            if self.fabric.error:
                raise LabError(self.fabric.error + '; stop the lab before restarting it')
            return
        links = []
        for cable in self.topology['links']:
            record = {'id': cable['id']}
            for side in ('a', 'b'):
                endpoint = cable[side]
                node = self.node(endpoint['node'])
                major, minor = map(int, netmap_port(node, endpoint['port']).split('/'))
                record[side] = {'node': node['id'], 'interface': endpoint['port'],
                                'id': node['iol_id'], 'port': major | minor << 4}
            links.append(record)
        occupied = self.occupied_ids() | {n['iol_id'] for n in self.topology['nodes']}
        self.fabric = link_fabric.LinkFabric(self.directory, self.netio, links, occupied)
        self.l1 = iol_l1.Controller(self.directory, self.netl1, self.fabric)

    def write_netmap(self):
        hostname = socket.gethostname()
        if not re.fullmatch(r"[\w-]+", hostname):
            raise LabError("iou2net requires a hostname containing letters, digits, underscores or hyphens")
        try:
            socket.gethostbyname(hostname)
        except socket.gaierror as exc:
            raise LabError(f"Lab hostname {hostname!r} does not resolve to IPv4; add a local /etc/hosts entry (Compose: extra_hosts)") from exc
        lines = []
        for link in self.topology["links"]:
            if self.fabric:
                for side in ('a', 'b'):
                    endpoint = link[side]
                    node = self.node(endpoint['node'])
                    peer = self.fabric.peers[(node['id'], endpoint['port'])]
                    local = f"{node['iol_id']}:{netmap_port(node, endpoint['port'])}@{hostname}"
                    relay = f"{peer['id']}:{peer['port'] & 15}/{peer['port'] >> 4}@{hostname}"
                    # The existing PC TAP bridge discovers its pseudo-ID on the right.
                    lines.append(f"{relay}  {local}" if node['type'] == 'pc' else f"{local}  {relay}")
            else:
                a, b = link['a'], link['b']
                if self.node(a['node'])['type'] == 'pc':
                    a, b = b, a
                left, right = self.node(a['node']), self.node(b['node'])
                lines.append(f"{left['iol_id']}:{netmap_port(left, a['port'])}@{hostname}  "
                             f"{right['iol_id']}:{netmap_port(right, b['port'])}@{hostname}")
        (self.directory / "NETMAP").write_text("\n".join(lines) + "\n")

    def node_dir(self, node_id):
        path = self.directory / "nodes" / node_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def spawn(self, node_id, key, command, cwd, extra_env=None):
        environment = {k: v for k, v in os.environ.items() if k != "SUDO_UID"}
        environment.update(extra_env or {})
        log_path = self.node_dir(node_id) / f"{key}.log"
        with log_path.open("ab") as log:
            log.write(("\n--- " + time.strftime("%Y-%m-%d %H:%M:%S") + " ---\n").encode())
            log.flush()
            proc = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                    start_new_session=True, env=environment)
        self.children[proc.pid] = proc
        record = {"pid": proc.pid, "stamp": process_stamp(proc.pid)}
        self.runtime[node_id][key] = record
        self.journal()
        return proc

    def alive(self, record):
        proc = self.children.get(record["pid"])
        if proc and proc.poll() is not None:
            return False
        return record.get("stamp") is not None and process_stamp(record["pid"]) == record["stamp"]

    def wait_ready(self, node_id, socket_path=None):
        runtime = self.runtime[node_id]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if runtime.get('iol_l1') and not runtime.get('iol_l1_identity'):
                expected = link_fabric.identity(self.netl1 / f"L1{runtime['iol_id']}")
                if expected:
                    runtime['iol_l1_identity'] = expected
                    self.journal()
            if not self.alive(runtime["console"]):
                raise LabError("Console process exited. " + self.logs(node_id)[-1800:])
            if "bridge" in runtime and not self.alive(runtime["bridge"]):
                raise LabError("Network bridge exited. " + self.logs(node_id)[-1800:])
            try:
                with socket.create_connection(("127.0.0.1", runtime["port"]), timeout=.2):
                    if ((socket_path is None or socket_path.exists()) and
                            (not runtime.get('iol_l1') or runtime.get('iol_l1_identity'))):
                        time.sleep(.2)
                        if self.alive(runtime["console"]):
                            return
            except OSError:
                pass
            time.sleep(.15)
        raise LabError("Node did not create its console / IOL socket within 15 seconds")

    def pc_peer(self, node_id):
        for link in self.topology["links"]:
            for side, other in (("a", "b"), ("b", "a")):
                if link[side]["node"] == node_id:
                    return self.node(link[other]["node"])
        raise LabError("Connect the PC eth0 interface to a switch or router first")

    def start(self, node_id):
        with self.lock:
            node = self.node(node_id)
            if node_id in self.runtime:
                return
            if node["type"] == "pc":
                peer = self.pc_peer(node_id)
                if peer["id"] not in self.runtime:
                    raise LabError(f"Start {peer['name']} before starting this PC, or use Start lab")
            socket_path = self.netio / str(node["iol_id"])
            if socket_path.exists():
                raise LabError(f"IOL ID {node['iol_id']} is in use outside this app; recreate this node to allocate another ID")
            if iol_l1.supported(node) and os.path.lexists(self.netl1 / f"L1{node['iol_id']}"):
                raise LabError('IOL L1 socket is already in use; use an available node ID')
            cwd = self.node_dir(node_id)
            netmap = cwd / "NETMAP"
            if netmap.is_symlink() and os.readlink(netmap) != "../../NETMAP":
                netmap.unlink()
            if not netmap.exists() and not netmap.is_symlink():
                netmap.symlink_to("../../NETMAP")
            # Keep a node-specific license when present; otherwise link the shared one.
            license_path = self.image_dir / "iourc"
            node_license = cwd / "iourc"
            if node_license.is_symlink() and not node_license.exists():
                node_license.unlink()
            if license_path.is_file() and not node_license.exists():
                node_license.symlink_to(license_path)
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            self.runtime[node_id] = {"port": port, "iol_id": node["iol_id"]}
            if iol_l1.supported(node):
                self.runtime[node_id]['iol_l1'] = True
            self.errors.pop(node_id, None)
            self.journal()
            try:
                self.ensure_fabric()
                self.write_netmap()
                if node["type"] == "pc":
                    self.start_pc(node)
                    image, arguments = shutil.which("docker"), ["exec", "-it", self.runtime[node_id]["container"], "/bin/sh"]
                elif frr.is_frr(node):
                    image, arguments = frr.start(self, node, run, LabError, ROOT)
                elif vios.is_qemu(node):
                    image, arguments = self.start_vios(node)
                else:
                    image = str(self.image_dir / node["image"])
                    arguments = ["-e", str(node["ethernet"]), "-s", "0", "-m", str(node["memory"]), "-n", "64"]
                    if self.runtime[node_id].get('iol_l1'):
                        arguments.append('-l')
                    nvram = cwd / f"nvram_{node['iol_id']:05d}"
                    if node.get("startup_config") and not nvram.exists():
                        initial_config.seed_iol(nvram, node["startup_config"])
                    arguments.append(str(node["iol_id"]))
                self.spawn(node_id, "console", ["perl", str(ROOT / "wrapper-ws.pl"), "--bind", "127.0.0.1",
                                                "--path", "/console", "--exit-output-bytes", "16384",
                                                "--transcript", str(cwd / "console-output.log"),
                                                "-m", image, "-p", str(port), "--", *arguments], cwd,
                           {"IOURC": str(node_license)} if node["type"] != "pc" and not vios.is_qemu(node) and not frr.is_frr(node) and node_license.is_file() else None)
                self.wait_ready(node_id, None if node["type"] == "pc" else socket_path)
                if self.runtime[node_id].get('iol_l1_identity'):
                    self.l1.add_node(node_id, self.runtime[node_id]['iol_l1_identity'])
            except Exception as exc:
                self.errors[node_id] = str(exc)
                try:
                    self.stop(node_id, dependents=False)
                except LabError as cleanup:
                    self.errors[node_id] += f"; cleanup: {cleanup}"
                raise LabError(self.errors[node_id]) from exc

    def start_vios(self, node):
        qemu = shutil.which("qemu-system-x86_64")
        if not qemu:
            raise LabError("Install qemu-system-x86 and qemu-utils, or rebuild the lab container for QEMU devices")
        vios.require_kvm(LabError)
        boot = vios.boot_image(node, self.image_dir, LabError, launch=True)
        cwd = self.node_dir(node["id"])
        # Decide before creating the writable disk: existing/restored disks win.
        fresh = not vios.disk_paths(node, cwd)[1].exists()
        seed = vios.config_disk(node, cwd, run, LabError) if fresh and vios.is_vios(node) and node.get("startup_config") else None
        disk = vios.disk_for(node, cwd, self.image_dir, run, LabError)
        runtime = self.runtime[node["id"]]
        sockets = Path(tempfile.mkdtemp(prefix=f"iol-{self.owner}-{node['iol_id']}-"))
        runtime["qemu_sockets"] = str(sockets)
        self.journal()
        routes = {}
        for link in self.topology["links"]:
            for side, other in (("a", "b"), ("b", "a")):
                if link[side]["node"] == node["id"]:
                    peer = self.node(link[other]["node"])
                    major, minor = map(int, netmap_port(peer, link[other]["port"]).split("/"))
                    routes[ports(node).index(link[side]["port"])] = (self.fabric.peers[(node["id"], link[side]["port"])]
                        if self.fabric else {"id": peer["iol_id"], "port": major | minor << 4})
        config = cwd / "qemu-network.json"
        atomic_json(config, {"id": node["iol_id"], "netio": str(self.netio), "count": node["ethernet"],
                             "sockets": str(sockets), "routes": routes})
        self.spawn(node["id"], "bridge", [shutil.which("python3"), str(ROOT / "qemu_net.py"), str(config)], cwd)
        deadline = time.monotonic() + 5
        while not (sockets / "ready").exists():
            if not self.alive(runtime["bridge"]) or time.monotonic() > deadline:
                raise LabError("QEMU network bridge failed. " + self.logs(node["id"])[-1800:])
            time.sleep(.05)
        return qemu, vios.command(node, disk, sockets, seed, boot)

    def start_pc(self, node):
        node_id = node["id"]
        runtime = self.runtime[node_id]
        run("docker", "image", "inspect", node["image"])
        stem = f"iol-{self.owner}-{node['iol_id']}"
        tap = f"it{self.owner}{node['iol_id']}"
        runtime.update({"container": stem, "network": stem, "tap": tap})
        self.journal()
        # Create resources without adopting any pre-existing object of the same name.
        run("ip", "tuntap", "add", "dev", tap, "mode", "tap")
        runtime["tap_created"] = True
        self.journal()
        run("ip", "link", "set", "dev", tap, "up")
        self.spawn(node_id, "bridge", ["perl", str(ROOT / "iou2net.pl"), "-t", tap, "-p", str(node["iol_id"]),
                                       "-n", str(self.directory / "NETMAP")], self.node_dir(node_id))
        time.sleep(.25)
        if not self.alive(runtime["bridge"]):
            raise LabError("iou2net failed. " + self.logs(node_id)[-1800:])
        # Docker's address is only for provisioning; remove it before applying the lab address.
        subnet = f"198.18.{node['iol_id'] // 64}.{(node['iol_id'] % 64) * 4}/30"
        run("docker", "network", "create", "-d", "macvlan", "--internal", "--subnet", subnet,
            "--label", f"iol.lab={self.owner}", "-o", f"parent={tap}", stem)
        runtime["network_created"] = True
        self.journal()
        run("docker", "run", "-d", "--name", stem, "--label", f"iol.lab={self.owner}", "--network", stem,
            "--cap-add", "NET_ADMIN", node["image"], "sleep", "2147483647")
        runtime["container_created"] = True
        self.journal()
        run("docker", "exec", stem, "ip", "-4", "addr", "flush", "dev", "eth0")
        if node["ipv4"]:
            run("docker", "exec", stem, "ip", "addr", "add", node["ipv4"], "dev", "eth0")
        if node["gateway"]:
            run("docker", "exec", stem, "ip", "route", "replace", "default", "via", node["gateway"])

    def terminate(self, record):
        if self.alive(record):
            # The wrapper's PTY child has its own session. Record it before terminating its parent.
            children = []
            try:
                children = [(int(pid), process_stamp(int(pid))) for pid in
                            Path(f"/proc/{record['pid']}/task/{record['pid']}/children").read_text().split()]
            except OSError:
                pass
            try:
                os.kill(record["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 4
            while self.alive(record) and time.monotonic() < deadline:
                time.sleep(.05)
            for pid, stamp in children + [(record["pid"], record["stamp"])]:
                if stamp is not None and process_stamp(pid) == stamp:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        proc = self.children.pop(record["pid"], None)
        if proc:
            proc.wait(timeout=5)

    def stop(self, node_id, dependents=True):
        with self.lock:
            if dependents:
                for node in self.topology["nodes"]:
                    if node["type"] == "pc" and node["id"] in self.runtime and self.pc_peer(node["id"])["id"] == node_id:
                        self.stop(node["id"], dependents=False)
            runtime = self.runtime.get(node_id)
            if not runtime:
                return
            if runtime.get("frr"):
                try:
                    frr.save(self, node_id, run)
                except (LabError, ValueError, OSError) as exc:
                    raise LabError(f"FRR saved config could not be preserved; container retained. Retry Stop: {exc}") from exc
            if self.l1:
                self.l1.remove_node(node_id)
            for key in ("console", "bridge"):
                if key in runtime:
                    self.terminate(runtime[key])
                    runtime.pop(key)
                    self.journal()
            errors = []
            for flag, command in [
                ("container_created", ["docker", "rm", "-f", runtime.get("container", "")]),
                ("network_created", ["docker", "network", "rm", runtime.get("network", "")]),
                ("tap_created", ["ip", "link", "delete", runtime.get("tap", "")])]:
                if runtime.get(flag):
                    try:
                        run(*command)
                        runtime.pop(flag)
                    except LabError as exc:
                        if not any(term in str(exc) for term in ("No such container", "not found", "Cannot find device", "does not exist")):
                            errors.append(str(exc))
                        else:
                            runtime.pop(flag)
                    self.journal()
            if errors:
                self.errors[node_id] = "Cleanup failed; retry Stop: " + "; ".join(errors)
                raise LabError(self.errors[node_id])
            if runtime.get("frr"):
                frr.cleanup_ports(self, runtime, run, LabError)
            if runtime.get("qemu_sockets"):
                try:
                    shutil.rmtree(runtime["qemu_sockets"])
                except FileNotFoundError:
                    pass  # /tmp may already have been cleared during host recovery.
                except OSError as exc:
                    self.errors[node_id] = f"Cleanup failed; retry Stop: {exc}"
                    raise LabError(self.errors[node_id]) from exc
                runtime.pop("qemu_sockets")
                self.journal()
            if runtime.get('iol_l1_identity'):
                path = self.netl1 / f"L1{runtime['iol_id']}"
                if link_fabric.identity(path) == runtime['iol_l1_identity']:
                    path.unlink(missing_ok=True)
            for suffix in ("", ".lck"):
                (self.netio / (str(runtime["iol_id"]) + suffix)).unlink(missing_ok=True)
            if self.fabric:
                self.fabric.reset_node(node_id)
            self.runtime.pop(node_id)
            self.journal()
            if not self.runtime and self.fabric:
                if self.l1:
                    self.l1.close()
                    self.l1 = None
                self.fabric.close()
                self.fabric = None

    def start_all(self):
        with self.lock:
            for node in self.topology["nodes"]:
                if node["type"] == "pc":
                    self.pc_peer(node["id"])
            started = []
            try:
                for node in sorted(self.topology["nodes"], key=lambda n: n["type"] == "pc"):
                    if node["id"] not in self.runtime:
                        self.start(node["id"])
                        started.append(node["id"])
            except LabError:
                for node_id in reversed(started):
                    self.stop(node_id)
                raise

    def stop_all(self):
        with self.lock:
            failures = []
            for node_id in list(self.runtime):
                try:
                    self.stop(node_id)
                except LabError as exc:
                    failures.append(str(exc))
            if not self.runtime and self.fabric:
                if self.l1:
                    self.l1.close()
                    self.l1 = None
                self.fabric.close()
                self.fabric = None
            if failures:
                raise LabError("; ".join(failures))

    def logs(self, node_id):
        self.node(node_id)
        chunks = []
        for key in ("console", "bridge"):
            path = self.node_dir(node_id) / f"{key}.log"
            if path.exists():
                with path.open("rb") as stream:
                    stream.seek(max(0, path.stat().st_size - 10000))
                    chunks.append(f"{key}:\n" + stream.read().decode(errors="replace"))
        return "\n".join(chunks) or "No launcher logs yet."

    def monitor(self, shutdown):
        while not shutdown.wait(2):
            with self.lock:
                lab_backup.expire(self)
                for node_id, runtime in list(self.runtime.items()):
                    if node_id not in self.runtime:
                        continue
                    failed = next((key for key in ("console", "bridge") if key in runtime and not self.alive(runtime[key])), None)
                    if failed:
                        child = self.children.get(runtime[failed]['pid'])
                        code = child.poll() if child else None
                        detail = f" (status {code})" if code is not None else ""
                        self.errors[node_id] = f"{failed.capitalize()} process exited{detail}. Open launcher logs for details."
                        stamp = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
                        print(f"{stamp} node {node_id}: {self.errors[node_id]}", file=sys.stderr, flush=True)
                        try:
                            self.stop(node_id)
                        except LabError:
                            pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    rbufsize = 0  # Do not prefetch WebSocket frames before switching to the tunnel.

    @property
    def lab(self):
        return self.server.lab

    def reply(self, status, value, content_type="application/json"):
        body = json.dumps(value).encode() if content_type == "application/json" else value
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def same_origin(self):
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            raise LabError("Cross-origin requests are not allowed")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise LabError("Cross-site requests are not allowed")

    def do_GET(self):
        try:
            path = urlsplit(self.path).path
            if path == "/api/state":
                self.reply(200, self.lab.snapshot())
            elif re.fullmatch(r"/api/exports/[a-f0-9]{32}", path):
                self.same_origin()
                stream, filename = lab_backup.take(self.lab, path.rsplit("/", 1)[1])
                with stream:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(os.fstat(stream.fileno()).st_size))
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    shutil.copyfileobj(stream, self.wfile, lab_backup.CHUNK)
            elif path == "/iol-console.html":
                self.reply(200, (ROOT / "iol-console.html").read_bytes(), "text/html")
            elif re.fullmatch(r"/api/nodes/[\w-]+/logs", path):
                self.reply(200, {"logs": self.lab.logs(path.split("/")[3])})
            elif re.fullmatch(r"/ws/[\w-]+", path):
                self.same_origin()
                self.tunnel(path.split("/")[2])
            else:
                relative = "index.html" if path == "/" else path.lstrip("/")
                target = (WEB / relative).resolve()
                if not target.is_relative_to(WEB) or not target.is_file():
                    self.reply(404, {"error": "Not found"})
                else:
                    self.reply(200, target.read_bytes(), mimetypes.guess_type(target)[0] or "application/octet-stream")
        except (LabError, ValueError) as exc:
            self.reply(409, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def mutate(self):
        try:
            self.same_origin()
            path = urlsplit(self.path).path
            if self.command == "POST" and path == "/api/import":
                self.close_connection = True
                if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Type", "").split(";")[0] != "application/zip":
                    raise LabError("Upload a ZIP with Content-Length and Content-Type: application/zip")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= lab_backup.MAX_BYTES:
                    raise LabError("Backup must be between 1 byte and 8 GiB")
                with self.lab.lock:
                    lab_backup.stopped(self.lab)
                self.connection.settimeout(60)
                with tempfile.TemporaryFile(dir=self.lab.directory) as stream:
                    while length:
                        chunk = self.rfile.read(min(length, lab_backup.CHUNK))
                        if not chunk:
                            raise LabError("Incomplete backup upload")
                        stream.write(chunk)
                        length -= len(chunk)
                    stream.seek(0)
                    result = lab_backup.restore(self.lab, stream, run)
                self.reply(200, result)
                return
            if self.command == "POST" and path.startswith("/api/images/"):
                self.close_connection = True
                if self.headers.get("Transfer-Encoding"):
                    raise LabError("Upload with Content-Length, not chunked transfer encoding")
                if self.headers.get("Content-Type", "").split(";")[0] != "application/octet-stream":
                    raise LabError("Use Content-Type: application/octet-stream")
                self.connection.settimeout(60)
                result = self.lab.upload_image(unquote(path[len("/api/images/"):]), self.rfile,
                                               int(self.headers.get("Content-Length", "0")))
                self.reply(201, result)
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise LabError("Use Content-Type: application/json")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1_000_000:
                raise LabError("Invalid request body size")
            chunks, remaining = [], length
            self.connection.settimeout(30)
            while remaining:
                chunk = self.rfile.read(remaining)
                if not chunk:
                    raise LabError("Incomplete request body")
                chunks.append(chunk)
                remaining -= len(chunk)
            data = json.loads(b"".join(chunks))
            path = urlsplit(self.path).path
            if self.command == "PUT" and path == "/api/topology":
                result = self.lab.save(data)
            elif self.command == "POST" and re.fullmatch(r"/api/links/[\w-]+/carrier", path):
                result = self.lab.set_link_carrier(path.split('/')[3], data)
            elif self.command == "POST" and re.fullmatch(r"/api/links/[\w-]+/traffic", path):
                result = self.lab.set_link_traffic(path.split('/')[3], data)
            elif self.command == "POST" and path == "/api/export":
                result = lab_backup.create(self.lab, data.get('include_logs', False), data.get('compact_veos', True))
            elif self.command == "POST" and path == "/api/export/initial-configs":
                result = console_capture.export(self.lab)
            elif self.command == "POST" and path == "/api/export/saved-configs":
                result = saved_config.export(self.lab, run)
            elif self.command == "POST" and path == "/api/lab/start":
                self.lab.start_all()
                result = self.lab.snapshot()
            elif self.command == "POST" and path == "/api/lab/stop":
                self.lab.stop_all()
                result = self.lab.snapshot()
            elif self.command == "POST" and re.fullmatch(r"/api/nodes/[\w-]+/(start|stop)", path):
                _, _, _, node_id, action = path.split("/")
                self.lab.node(node_id)
                getattr(self.lab, action)(node_id)
                result = self.lab.snapshot()
            else:
                self.reply(404, {"error": "Not found"})
                return
            self.reply(200, result)
        except (LabError, ValueError, TypeError) as exc:
            self.close_connection = True
            self.reply(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.log_error("Operation failed: %s", exc)
            self.reply(500, {"error": str(exc)})

    do_POST = mutate
    do_PUT = mutate

    def tunnel(self, node_id):
        with self.lab.lock:
            runtime = self.lab.runtime.get(node_id)
            if not runtime or "console" not in runtime:
                raise LabError("Start this node before opening its console")
            port = runtime["port"]
        if self.headers.get("Upgrade", "").lower() != "websocket":
            raise LabError("WebSocket upgrade required")
        try:
            upstream = socket.create_connection(("127.0.0.1", port), timeout=5)
        except OSError as exc:
            raise LabError("Console is unavailable") from exc
        self.close_connection = True
        with upstream:
            query = urlsplit(self.path).query
            suffix = "?" + query if re.fullmatch(r"cursor=[a-z0-9-]{1,80}:\d{1,16}", query) else ""
            request = f"GET /console{suffix} HTTP/1.1\r\n"
            for name, value in self.headers.items():
                if name.lower() in ("host", "upgrade", "connection", "sec-websocket-key", "sec-websocket-version", "sec-websocket-protocol"):
                    request += f"{name}: {value}\r\n"
            upstream.sendall((request + "\r\n").encode("latin-1"))
            upstream.settimeout(10)
            self.connection.settimeout(10)
            try:
                while True:
                    ready, _, _ = select.select([self.connection, upstream], [], [], 1)
                    if self.server.stopping.is_set():
                        break
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        (upstream if source is self.connection else self.connection).sendall(data)
            except (OSError, ValueError):
                pass

    def log_message(self, fmt, *args):
        if "/api/state" not in str(args):
            super().log_message(fmt, *args)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default=os.environ.get("WL_BIND", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=os.environ.get("WL_PORT", "8080"))
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("WL_DATA_DIR", ROOT / ".lab")))
    parser.add_argument("--image-dir", type=Path, default=Path(os.environ.get("WL_IMAGES_DIR", ROOT)),
                        help="Device .bin / .qcow2 images and optional iourc (default: application directory or WL_IMAGES_DIR)")
    return parser.parse_args(argv)


def main():
    args = parse_args()
    lab = Lab(args.data_dir, args.image_dir)
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    server.daemon_threads = True
    server.lab, server.stopping = lab, threading.Event()
    def shutdown(signum, frame):
        server.stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    threading.Thread(target=lab.monitor, args=(server.stopping,), daemon=True).start()
    print(f"Weblab: http://{args.bind}:{args.port}  |  data: {lab.directory}  |  images: {lab.image_dir}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.stopping.set()
        lab.stop_all()
        with lab.lock:
            lab_backup.expire(lab, all_files=True)
        server.server_close()


if __name__ == "__main__":
    main()
