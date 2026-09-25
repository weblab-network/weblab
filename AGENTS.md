# Working in Weblab

Weblab is a self-hosted network lab manager. Read the [help index](docs/README.md),
[device profiles](docs/devices.md), [backup semantics](docs/backups.md), and
[development guide](docs/development.md) before changing related behavior.

## Creating a practice lab

Follow [Creating practice labs and topology JSON](docs/practice-labs.md). Deliver
an importable JSON file and companion Markdown exercise under `examples/` or the
requested directory. YAML is not supported. Use exact locally available image
filenames or the supported FRR container tag, unique exercise node IDs, and distinct interfaces for each cable.
Create original exercises; do not use exam dumps or claim current blueprint
coverage without checking the vendor's official blueprint.

Use `startup_config` only for the intended baseline on fresh Cisco or FRR devices,
using each platform’s configuration syntax. Existing NVRAM/disks/FRR saved
configuration take precedence, including after ZIP import. EXOS/Arista/Junos do not
support initial snippets or configuration-text exports. PC addressing belongs
in `ipv4`/`gateway`; arbitrary shell snippets are not supported.

Validate without starting devices:

```sh
python3 tools/validate_topology.py examples/ospf-practice.json
python3 tools/validate_topology.py examples/ospf-practice.json --image-dir images
```

State whether validation was structural or included actual device boot/protocol
tests. A request for exercise files does not authorize replacing or running an
active lab. Do not delete saved storage to force snippets without a reset request.

## Implementation and testing

- Use disposable data directories. Do not import over, start, stop, reset or deploy
  over an active lab solely for testing. Follow explicit user authorization when
  execution or changes to that lab are requested.
- There is no frontend build step. Docker copies source files, so deployment needs
  a rebuild/recreation. Do not deploy, commit or push unless requested.
- Run meaningful tests for changed behavior. The backend command is
  `python3 -m unittest discover -s tests -v`; browser/native instructions are in
  the development guide. Run suites that launch nodes sequentially to avoid ID
  collisions. Native tests require user-supplied images and any required licenses.
- Preserve shared-console transport, input locks, saved-state precedence and
  transactional archive restore. Never read active guest storage or bypass QEMU
  disk locks with forced sharing. Exported logs are not restored as device state.
- Keep saved and live configuration extraction separate. Add explicit handlers
  for new vendors rather than applying Cisco parsers/commands to them. Keep device
  handlers independently implemented and preserve third-party license notices.
- IOL carrier control is enabled only for the exact tested 17.18.02 profiles.
  Nodes must opt in with `iol_l1: true`; the default is off to avoid unnecessary
  CPU load. Preserve socket identity checks and runtime-only link faults; validate native
  behavior before enabling other profiles. Unplug is a requested state with a
  detection delay, not an instant guest acknowledgment.
- Except for the explicitly pinned optional EXOS demo distribution described in
  `docs/containers.md`, vendor images and licenses are user-supplied. Do not generate licenses or add
  images, credentials, runtime data or personal lab files to source control.
- Keep user documentation and JSON examples consistent with implemented behavior.
  Licensing/provenance decisions are separate from routine code changes; preserve
  third-party notices and do not invent license grants.
