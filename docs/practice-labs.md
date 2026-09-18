# Creating practice labs and topology JSON

[Help index](README.md) · [Project overview](../README.md)

Use this contract when writing a topology by hand or asking an agent to generate
an exercise. The validator checks structure; device boot and protocol verification
are separate steps. Importing a file replaces the selected topology, so export
any current work first.

## Design an original exercise

Use JSON. YAML is not an accepted import format. Deliver a complete importable
file under `examples/` or the user's requested directory, not just a code fragment.
The browser's Import button accepts that JSON directly. A companion Markdown
exercise can be opened with Instructions beside Start lab; it stays in the user's
browser tab and is not embedded in topology JSON or ZIP. No ZIP, Cisco binaries,
licenses or saved device files are needed for an initial exercise.

1. Identify the desired topics, difficulty, device budget and image families.
   Ask only for material missing requirements; otherwise state reasonable
   assumptions. Create original exercises. Do not reproduce exam dumps or claim
   a lab matches a current certification blueprint without checking Cisco's
   official blueprint. CCNP/CCIE is a difficulty/coverage target, not a guarantee
   that every feature is supported by the installed virtual images.
2. List available filenames in `images/`, or use a provided catalog. Preserve
   exact image filenames. Do not download device images or licenses. If no
   catalog is available, choose explicit placeholders and tell the user which
   filenames to replace. Router/switch features differ by image and release.
3. Design the graph and address plan before writing configurations. Allocate a
   distinct physical interface to each cable. Choose readable positions and
   names, and avoid overlapping cards. Use IDs unique to the exercise, e.g.
   `ospf_a_r1`, to avoid reusing another lab's saved node storage.
4. Put only the intended baseline into `startup_config`. For a routing exercise,
   initialize hostnames, addressing and enabled interfaces; leave the routing
   protocol for the learner unless the exercise is troubleshooting a supplied
   configuration. If requested, supply a separate solution JSON/file.
5. Include a short companion Markdown exercise with objectives, addressing,
   tasks, constraints, verification commands and expected outcomes. Distinguish
   what should work initially from what should work after solving the exercise.
   A troubleshooting lab should explicitly say that faults are intentional.
6. Validate the JSON locally using the command below. Check address overlap,
   interface names and image capabilities yourself: structural validation does
   not parse Cisco configuration syntax or prove protocol convergence.
7. Deliver links to the JSON and exercise. State assumptions, required images,
   and whether you tested syntax only or actually booted disposable devices.
   Do not claim a working lab merely because JSON validation passed.

## Topology JSON contract

Top level: `{"version":1,"name":"Lab name","nodes":[],"links":[]}`.
The name is 1–80 characters. At most 64 nodes, 256 links and 1 MB per JSON file.
Do not add exercise prose as unknown top-level/node fields: the app normalizes
and drops unsupported fields on import. Keep instructions in a companion file.

Each node uses:

| Field | Meaning |
| --- | --- |
| `id` | Unique string, 1–40 ASCII letters/digits/underscores/hyphens |
| `name` | Display label, 1–40 characters |
| `type` | `router`, `switch` or `pc` |
| `image` | Exact `.bin` or `.qcow2` filename; PCs use `alpine:latest` or another installed Alpine tag |
| `x`, `y` | Integer canvas center coordinates; x 70–2330, y 60–1540 |
| `memory` | Integer MB, 256–8192; normally 1024 for Cisco devices |
| `ethernet` | IOL: 1–8 slots, four ports per slot; IOSv: 1–16 individual interfaces |
| `ipv4` | PC startup address and prefix, e.g. `192.0.2.10/24`; otherwise empty |
| `gateway` | PC gateway in its configured IPv4 subnet, or empty |
| `startup_config` | Optional Cisco configuration-file text, up to 16 KiB UTF-8; see precedence below |

Omit `iol_id` from new lab files; the importer allocates an available application
ID. Defaults exist for position/memory/interface count, but specify them for a
predictable exercise. Supply `ipv4` and `gateway` as empty strings on Cisco nodes.
PCs have only `eth0`; their memory/ethernet fields do not add interfaces.

Each link is `{"id":"cable1","a":{"node":"r1","port":"0/0"},
"b":{"node":"r2","port":"0/0"}}`. Link IDs follow the same identifier rules.
No interface may appear on two cables. No self-links or direct PC-to-PC cables.
An unconnected PC can be imported, but cannot start until cabled to a router/switch.

| Device | Link `port` spelling | Interface spelling inside a Cisco snippet |
| --- | --- | --- |
| IOL router/switch | `0/0`…`0/3`, `1/0`… according to slot count | `Ethernet0/0`, `Ethernet1/0`, etc.; verify image |
| IOSv router | `Gi0/0`…`Gi0/15` according to interface count | `GigabitEthernet0/0`, etc. |
| IOSvL2 switch | `Gi0/0`…`Gi0/3`, `Gi1/0`…`Gi3/3` | `GigabitEthernet0/0`, etc. |
| Alpine PC | `eth0` | Use JSON `ipv4`/`gateway`; Cisco `startup_config` is rejected |
| Virtual EXOS | `Mgmt`, `1`…`12` | No startup snippets yet; configure through console |
| Arista vEOS-lab | `Management1`, `Ethernet1`…`Ethernet15` | No startup snippets yet; configure through console |

EXOS images must keep the `EXOS-VM_` or `EXOS-VM-` filename prefix and use switch
nodes. For EXOS, `ethernet` is the total NIC count (2–13), including Mgmt; default
13 with 1024 MB RAM. Its first NIC is management, followed by numbered data ports.
Use data ports for normal lab cables. EXOS startup_config, live capture and saved
configuration-text extraction are not supported yet. Plain topology JSON and
stopped-lab saved-state ZIP are available. Do not assume Cisco snippets or NVRAM
readers apply to EXOS. The selected EXOS image's virtual data plane determines
which protocols can actually forward traffic.

Arista vEOS-lab images keep their `vEOS64-lab-` or `vEOS-lab-` prefix and use switch
nodes. Default memory is 6144 MB with two fixed vCPUs. `ethernet` is 2–16 total
NICs including Management1 (default 5); use Ethernet data ports for normal cables.
The exact `Aboot-veos-serial-8.0.2.iso` must also be installed; it is not a node
image. On fresh boot, log in as admin with no password and run `zerotouch disable`
(reboots). Save configuration with `write memory` before stopping. Startup
snippets and config-text exports are rejected; topology JSON and saved-state ZIP
work. ZIP records the base disk and Aboot hashes, without embedding either image.
Do not apply Cisco seeds/NVRAM readers to Arista. See [device profiles](devices.md) for tested releases and [development](development.md) for native tests. This profile is not CloudEOS/vEOS Router.

For switches, choose an L2 image (filenames normally contain `l2`). Do not assume
a router accepts switchport commands or an L2 image supports every routing feature.

## Initial configuration and saved-state precedence

`startup_config` is a JSON string with escaped newlines, e.g.
`"hostname R1\ninterface Ethernet0/0\n ip address 10.0.12.1 255.255.255.252\n no shutdown\nend\n"`.
Use configuration-file syntax, not a console transcript or shell script. Omit
`enable`, `configure terminal`, prompts, `show`, `write memory` and pager output.
A final `end` is appropriate. Do not include credentials unless explicitly needed
for the exercise; any included values are plain text in a shared JSON file.
For banners, use a single printable delimiter absent from the message, e.g.
`banner motd #\nPractice lab\n#\n`. Do not paste the two-character `^C` delimiter
shown by IOS; the configuration-file parser treats it differently.

Snippets initialize fresh Cisco nodes on their first start. Existing IOL NVRAM
and existing IOSv writable disks take precedence, whether local or restored from
a ZIP archive. Editing a snippet does not reconfigure an already initialized
node. Use new node IDs/a fresh data directory for a new exercise. Importing JSON
with old IDs is not a factory reset. Never delete saved state to force a snippet
without an explicit user request to reset it.

The launcher creates IOL NVRAM from the text, or attaches an IOSv FAT seed disk
containing `ios_config.txt` and its checksum on first boot only. IOSv requires
`mtools` (included in the Docker image). Give the initial boot time to complete;
a disk created by an interrupted first boot is still treated as existing state.
A human can create a baseline with **Export → JSON + saved configs**, which reads
saved IOL NVRAM/IOSv disk configuration while Cisco devices are stopped, without
accessing consoles. Unsaved changes are excluded. **JSON + live configs** reads
current running configurations while all Cisco devices are at unlocked privileged
EXEC prompts. A plain JSON export retains the original snippets only. Neither
configuration export silently substitutes another source when retrieval fails;
choose live capture explicitly when disk retrieval is unavailable. Saved JSON
contains startup text, not private NVRAM records/VLAN databases/other flash files;
use Saved lab ZIP to preserve device storage.
ZIP preserves IOL `vlan.dat-<application-ID>` alongside NVRAM and remaps both
filenames when import assigns a different application ID. ZIP restore replaces
the full nodes/ tree transactionally, not by merging
files. Do not purge the whole data directory before import.
Live capture temporarily suppresses enabled console logging, restores and verifies
the original settings, and preserves them in the exported snippets. It does not
save the running configuration. If restoration cannot be verified, export fails
with manual recovery commands; address that error before saving device state.
Inspect the device console for configuration errors and save the resulting
configuration with `write memory` before stopping or exporting a saved lab ZIP.

Saved ZIP has an optional Include logs checkbox. It exports retained console
transcripts and launcher logs under `logs/`, separate from device files. Import
validates those entries but does not restore them as device state. The wrapper
records raw PTY output on the server from device start, without requiring an open
browser, retaining two 4 MiB segments across restarts. Launcher exports keep an
8 MiB tail per file. Logs may contain configuration details; no hidden input is
recorded separately. See [backups](backups.md) for retention and archive compatibility.

PC `ipv4`/`gateway` settings are applied each time its disposable Alpine container
starts. They can be edited while that PC is stopped even if Cisco nodes are
running. Other device/cable changes still require the lab to be stopped.

## Validate without touching the active lab

```sh
python3 tools/validate_topology.py examples/ospf-practice.json
python3 tools/validate_topology.py examples/ospf-practice.json --image-dir images
```

The first command checks structure, interface allocation, addressing and snippet
limits without requiring installed images. The second also checks image filenames
against the local catalog. Neither writes lab state, imports a file or starts nodes.
See [the OSPF exercise](../examples/ospf-practice.md) for an original exercise using initial snippets.
