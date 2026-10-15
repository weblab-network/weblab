# Weblab FRR — LLDP, SNMP, VLANs and IPv4 VRRP

This image adds native `vtysh` LLDP, read-only SNMPv2c and routed 802.1Q
interface commands, plus automatic IPv4 VRRP interface provisioning, to **FRR 10.7.1**. The builder produces
`weblab-frr:10.7.1-lldp-snmp-vlan-vrrp1`. The earlier LLDP-only, LLDP/SNMP and VLAN
selectors remain supported with their existing images. This is a router
extension; bridge switching and routing-protocol SNMP MIBs are not included.

## Release bundle

For Weblab v0.8.0, select `ghcr.io/weblab-network/weblab-frr:10.7.1-wl1`.
Install the image on the Docker host using the registry package, or download
`weblab-frr-linux-amd64.tar.gz` and `SHA256SUMS` from the matching Weblab release,
verify its checksum and run:

```sh
docker load -i weblab-frr-linux-amd64.tar.gz
```

After registry publication, the equivalent command is:

```sh
docker pull ghcr.io/weblab-network/weblab-frr:10.7.1-wl1
```

This Linux amd64 image is checked against an immutable Docker image/config ID
in `frr.py`. It works without `WL_ALLOW_UNTESTED_FRR`; modified/rebuilt images
still require that explicit opt-in. The public selector is separate from earlier
local selectors, so existing saved storage is not silently migrated. Keep an
existing router's selector when continuing its saved lab. To move configurations,
export saved configuration text and use it to initialize a new router, then verify.

The binary is accompanied by `weblab-frr-sources.tar.gz`, `image.json` and
checksums. See [corresponding sources and licenses](SOURCES.md). Source bundles
must accompany redistribution. Weblab core and the optional Agent remain separate
Compose services; this release is not a Docker-in-Docker appliance.

## Build and select

Docker and a local upstream FRR Git checkout containing release `frr-10.7.1`
are required. From the Weblab source directory:

```sh
python3 tools/build_frr_lldp.py --source /path/to/frr --jobs 2
```

The builder archives the exact upstream commit
`f8c0b08dcb0c78f9e42b9b86ae70b049e4e617c1`; local changes and the checkout's
current branch are not included. It builds FRR and its libraries together,
applies `lldp-vtysh.patch`, and adds Alpine's lldpd and Net-SNMP packages. Network
access and several GiB of free build space are needed. The image contains the pinned FRR
source and patch under `/usr/share/weblab-frr/`, alongside attribution.

Enable `WL_ALLOW_UNTESTED_FRR=1` on a Weblab instance using this source version.
The experimental image then appears in the router selector. Build the image on
that instance's Docker host first; Weblab does not pull or compile it on Start.
Local build tags remain experimental even when built from release sources.
The ordinary upstream FRR profile and its tested-digest policy are unchanged.

Use a **new router** for initial testing. Changing an existing node's image
selector chooses different saved storage; it does not migrate its configuration.
The current Weblab service requires rebuilding/recreation to pick up source
changes. Save and stop your lab before any deployment.

## LLDP CLI

LLDP is disabled by default, including with older startup configurations.

```text
configure terminal
lldp run
end
show lldp
show lldp neighbors
show lldp neighbors detail
write memory
```

All existing lab ports (`eth0`–`eth7`) transmit and receive after global enable.
Per-interface settings are available in the default VRF:

```text
configure terminal
interface eth1
 no lldp transmit
exit
interface eth2
 no lldp receive
end
write memory
```

Use `lldp transmit` / `lldp receive` to restore each direction, or global
`no lldp run` to stop discovery while retaining per-port settings. These are
normal immediate FRR configuration commands; `end` does not commit or save.
Use `write memory` to preserve changes. LLDP uses the configured hostname and
interface names as port IDs. Remote entries can remain until their advertised
TTL expires after a cable fault or transmit disable.

The neighbor summary includes enabled capabilities. `show lldp neighbors detail`
shows each neighbor's chassis/port IDs, descriptions, enabled capabilities,
management addresses, advertised TTL and age reported by lldpd. Missing fields
display `-`; a management address is an advertisement, not a reachability check.
Neighbors see the advertised system description
`Weblab FRR 10.7.1 - LLDP, SNMP, VLAN and IPv4 VRRP extensions (weblab.network)` instead of the
base Alpine release. SNMP reports the same string in `sysDescr`.

## SNMPv2c CLI

SNMP defaults off. Configure an existing local IPv4 address, an allowed source
prefix, and one read-only community before enabling it:

```text
configure terminal
interface eth0
 ip address 10.88.0.1/30
exit
snmp-server listen 10.88.0.1
snmp-server source 10.88.0.2/32
snmp-server community labMonitor ro
snmp-server contact Lab Operations
snmp-server location Rack A
snmp-server enable
end
show snmp
write memory
```

Replace the example addresses and community for your lab. The listener uses UDP
161 on that specific address, on `lo`, a physical lab interface or an `ethN.VID` VLAN interface
in the default VRF. It waits if the address is not present yet and stops if it is removed.
The source prefix restricts incoming requests; it does not select an outgoing
source address. An explicit `/0` allows any IPv4 source that can reach the lab.

The exposed read-only view contains system information, IF-MIB/ifXTable and,
when LLDP is enabled, LLDP-MIB through a local AgentX socket. SNMPv1, SNMPv3,
writes, traps, IPv6 listeners, VRF listeners, OSPF-MIB and BGP-MIB are not enabled.
SNMP service failure does not restart the routing daemons or lldpd.

`no snmp-server enable` stops polling while retaining settings. Clearing a required
setting with `no snmp-server community`, `no snmp-server listen` or
`no snmp-server source` also disables SNMP; re-enable after restoring the setting.
Contact/location can be changed or removed independently. Community strings accept 1–64 letters,
digits or `_ . @ -`; contact/location accept up to 128 printable ASCII
characters excluding quote, backslash and `#`.

From a reachable monitoring host, using the example configuration:

```sh
snmpget -v2c -c labMonitor 10.88.0.1 .1.3.6.1.2.1.1.1.0
snmpwalk -v2c -c labMonitor 10.88.0.1 .1.3.6.1.2.1.31.1.1.1.1
snmpwalk -v2c -c labMonitor 10.88.0.1 .1.0.8802.1.1.2
```

SNMPv2c communities are unencrypted on the lab network and are stored in plain
text in `show running-config`, saved configuration and configuration backups.
`show snmp` reports service status without displaying the community.

## Routed VLAN interfaces

Create the tagged interface from global configuration mode, then configure it
using ordinary FRR interface commands:

```text
configure terminal
vlan interface eth0.10
interface eth0.10
 description VLAN 10 gateway
 ip address 192.0.2.1/24
 ipv6 address 2001:db8:10::1/64
exit
vlan interface eth0.20
interface eth0.20
 ip address 198.51.100.1/24
end
show vlan interfaces
show interface eth0.10
write memory
```

The name specifies both the physical parent and tag: `eth0.10` is VLAN 10 on
`eth0`. Supported names are `eth0.VID` through `eth7.VID`, VID 1–4094, up to
64 interfaces per router. Parents must exist in the default VRF. The peer must
send and accept the same 802.1Q tags. Untagged traffic remains on `eth0`.
Configure addresses and routing protocols normally; this supports routing
between VLANs, including a router connected to a switch trunk. It does not
create switch access ports, bridge VLANs, Q-in-Q or VRF assignments. LLDP stays
on physical ports; SNMP can listen on a VLAN address and report its counters.

`shutdown` / `no shutdown` under the interface control administrative state.
`no vlan interface eth0.10` removes the Linux tagged interface. FRR's interface
configuration is separate: remove its address/routing settings too when retiring
the VLAN, or retain them for later recreation. FRR rejects `no interface`
while a kernel interface is still active; remove the VLAN declaration first
and wait for interface removal before deleting its FRR configuration block. Removing only an FRR interface
configuration does not remove the VLAN creation declaration. Save the intended
result with `write memory`.

The backend tracks the kernel identity of interfaces it creates. A manually
created interface with the same name is a conflict; it is not adopted or deleted.
Do not rename, reparent or replace managed VLAN interfaces from the shell.
`show vlan interfaces` reports configured interfaces, administrative state and
conflicts. Resolve a foreign-link conflict explicitly before retrying.

## IPv4 VRRP through native FRR commands

Use the VRRP image above. Configure a unique real address on each router, then
configure the shared virtual gateway with FRR's existing commands. For router 1:

```text
configure terminal
vlan interface eth0.10
interface eth0.10
 ip address 192.168.10.2/24
 vrrp 10 version 3
 vrrp 10 priority 200
 vrrp 10 ip 192.168.10.1
end
show vrrp platform
show vrrp
write memory
```

Router 2 uses real address `192.168.10.3/24`, priority `100`, and the same VLAN,
VRID, version and virtual IP. The switch carries VLAN 10 to both routers. Clients
use `192.168.10.1` as their gateway. For an untagged connection, use `interface
eth0` directly and omit the VLAN declaration.

A local reconciler reads `vrrpd`'s native JSON state approximately once per second
and creates the required IPv4 macvlan, virtual MAC and VIP addresses. FRR handles
all elections, advertisements, priority, preemption and Master/Backup protodown
state. Configuration is native FRR configuration; no duplicate VRRP configuration
is written by the helper. `show vrrp` shows protocol state; `show vrrp platform`
shows provisioning readiness, missing prerequisites and ownership conflicts.
Allow provisioning and election time after configuring a group. VRRP moves the
virtual gateway; it does not install alternate routes or move other groups when
one interface fails. Plan return paths and routing redundancy separately.

The VIP must differ from the router's real address and belong to a real parent
IPv4 subnet (prefix length /0–/30; subnet network/broadcast addresses excluded).
The longest matching parent prefix is used. Up to 64 groups and 16 IPv4 VIPs per
group are supported. Parents are `eth0`–`eth7` or their `ethN.VID` children in the
default VRF. IPv6 provisioning, VRF-scoped groups and `vrrp autoconfigure` are not
managed by this integration. Existing manual macvlans are not adopted or deleted.

`vrrp 10 shutdown` / `no vrrp 10 shutdown` remain native administrative controls.
`no vrrp 10 ip 192.168.10.1` removes that VIP; removing the last IPv4 VIP or using
`no vrrp 10` removes the owned virtual interface. Removing the prerequisite real
parent address also withdraws the owned interface until the subnet is valid
again. Changes reconcile asynchronously; a failed daemon read retains existing
interfaces rather than interpreting failure as an empty configuration.

The helper reserves names starting with `wv` for its generated interfaces, such
as `wv0v10g10`. It checks recorded kernel identity, parent and virtual MAC before
changing/removing an interface. Do not rename or edit managed macvlans from the
shell. Restarting the helper preserves existing protocol state and ownership.

The VRRP profile uses a passthru Docker endpoint and a private one-port Linux
bridge for each `ethN`. This accepts virtual MAC traffic and prevents nested
macvlan flattening; different lab ports are never bridged together. Internal
`wpN` bridge members are transport plumbing: configure `ethN`, not `wpN`.
Container-local ARP settings keep VIP replies on their virtual interfaces and
ignore routes through Backup interfaces. Existing FRR image profiles retain
their original port attachment. The upstream VRRP election implementation is
unchanged.

## Persistence and failure behavior

LLDP, SNMP and VLAN intent participate in FRR's integrated configuration collection/replay.
`show running-config`, `write memory`, `vtysh -w` and startup replay use the
same patched CLI. Stop copies the last saved `frr.conf`; it does not save
unsaved LLDP/SNMP/VLAN/VRRP changes. Saved-config exports and ZIP restore use that same file.
ZIPs require the exact image ID on the destination. Local experimental selectors
also require the untested-image opt-in.
Use `docker save` / `docker load` to transfer a local image; ZIPs omit images.

A local VTY socket connects `vtysh` to the extension backend. There is no extra TCP
listener or host-network access. lldpd is restricted to the eight supported
physical lab port names. VRF-scoped LLDP and CDP are outside this implementation.
VLAN declarations recreate their tagged interfaces during startup; addresses and
routing settings come from the same saved FRR configuration. Runtime ownership
records are container-local and are not part of saved backups.

Use integrated configuration. A daemon-filtered direct save or a non-integrated
save is rejected instead of silently omitting the extension's settings.

Routing, LLDP and SNMP have separate supervision. A missing extension backend makes
integrated saves fail before the existing saved file is renamed or truncated.
Service status is reported by `show lldp` and `show snmp`; routing daemons keep running.
Saved native VRRP configuration reconstructs the required macvlans and VIPs
after Stop/Start and ZIP restore. Runtime recovery state under `/run` is not backed up or restored as startup
configuration. Neighbor text is treated as untrusted and stripped of terminal
control characters before rendering.

## Verification

```sh
python3 -m unittest discover -s tests -p 'test_frr*.py' -v
node tests/frr_ui.cjs
python3 tests/frr_vrrp_cli_native.py
python3 tests/frr_vlan_native.py --vrrp
python3 tests/frr_lldp_native.py --vrrp
python3 tests/frr_snmp_native.py --vrrp
python3 tests/frr_native.py --vrrp
```

The native commands require root, local Docker and TAP access. They create their own
disposable labs and check real LLDP, OSPF, save/replay, backend-unavailable saves
and ZIP restoration. `frr_snmp_native.py` checks actual polling and access
restrictions, LLDP-MIB/AgentX, service recovery and SNMP persistence.
`frr_native.py --snmp` also checks OSPFv3, BGP, forwarding,
link faults, console locking and server recovery with the enhanced image.
Run them sequentially with other node-launching suites.

The prior LLDP-only image passed local validation with FRR 10.7.1: real bidirectional LLDP, eight-port
setup, per-port policy, configuration reload, service recovery, saved-state/ZIP
round trips, and the routing regression above. A user also reported successful
FRR–LL2S discovery after building on a separate VM. Other vendor combinations
and other FRR versions have not yet been validated.

The combined image also passed real SNMPv2c polling across a lab link: system
and interface data, LLDP-MIB through AgentX, wrong-community/source and SNMPv1
rejection, rejected writes, independent service recovery, full SNMP remove/re-add
through configuration reload, and saved-state ZIP restoration. Its routing
regression passed OSPFv2/v3, BGP, IPv4/IPv6 forwarding and link-failure recovery.

The VLAN native suite additionally verifies tagged IPv4/IPv6 forwarding, OSPF
over a VLAN, tag isolation, inter-VLAN routing through a third router, parent and
child shutdown behavior, ownership protection, SNMP on a VLAN address, reload
and ZIP recreation of saved interfaces. These checks use disposable labs on the
development host; they do not claim validation on every host/kernel combination.

The IPv4 VRRP profile passed local two-router/LL2S tests with real clients:
VRRPv2 on untagged ports, VRRPv3 on VLANs, virtual-MAC ARP, inter-VLAN forwarding,
administrative and link-outage failover, preemption, VIP/group removal,
prerequisite withdrawal, ownership conflicts, backend restart, saved-config
reload and ZIP restoration. The LLDP, SNMP, VLAN and routing suites also passed
on this profile, including OSPFv2/v3, BGP and IPv4/IPv6 forwarding. IPv6 **VRRP**
provisioning remains unsupported. These are local validation results; the image
remains experimental.

Architecture references: [FRR vtysh](https://docs.frrouting.org/projects/dev-guide/en/latest/vtysh.html),
[lldpd](https://lldpd.github.io/usage.html),
[Net-SNMP configuration](https://www.net-snmp.org/docs/man/snmpd.conf.html). The extension patch retains vtysh's
GPL-2.0-or-later terms; Weblab's separate backend/build tooling use the project's
MIT license. See [NOTICE](NOTICE).
