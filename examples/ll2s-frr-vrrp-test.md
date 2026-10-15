# LL2S + Weblab FRR: VLAN, VRRP, OSPF and RSTP test lab

Import [the topology JSON](ll2s-frr-vrrp-test.json) into a fresh workspace.
This is a preconfigured, user-contributed test snapshot: three LL2S switches,
three Weblab FRR routers and six Alpine PCs, connected by 12 links.
It uses the published images installed by the
[standalone Docker instructions](../docs/installation.md#run-the-prebuilt-image-without-compose).
No KVM or vendor images are needed; load the host `openvswitch` module first.

The contributor reported that the standalone installation worked end-to-end on
a fresh VM and supplied this topology as their FRR/LL2S test lab. Publication
checks validated the JSON structure; this particular exported configuration was
not independently boot-tested during publication. The R2 SNMP listener and
loopback advertisement were corrected as described below; other device
configurations, addresses, resource settings and cable layout are preserved. Node/link IDs are
example-specific and runtime `iol_id` values are omitted for import allocation.

Import replaces the current topology and requires stopped devices. Use a new
workspace to avoid overwriting a personal lab. Startup snippets initialize fresh
nodes; existing saved configuration takes precedence on later imports.

## Layout and addressing

SW1, SW2 and SW3 form a triangle. Their inter-switch trunks carry VLANs 100 and
200. SW1 and SW2 connect to R1 and R2 through trunks; SW3 connects to R3 through
an access port in VLAN 100. Each router also connects directly to one PC.

| Segment | Configuration |
| --- | --- |
| VLAN 100 | OSPF transit: R1 `10.100.0.1/24`, R2 `.2`, R3 `.3` |
| VLAN 200 | R1 `10.0.20.2/24`, R2 `.3`; VRRP group 20, virtual gateway `10.0.20.1` |
| PC1 / R1 eth1 | `10.0.1.10/24`, gateway `10.0.1.1` |
| PC2 / R2 eth1 | `10.0.2.10/24`, gateway `10.0.2.1` |
| PC3 / R3 eth1 | `10.0.3.10/24`, gateway `10.0.3.1` |
| PC4 / SW2 eth3 | VLAN 200, `10.0.20.10/24`, virtual gateway |
| PC6 / SW3 eth4 | VLAN 200, `10.0.20.60/24`, virtual gateway |
| PC5 / SW3 eth3 | VLAN 100, `10.0.30.10/24`, gateway `10.0.30.1` on R1 |
| SW2 management | VLAN 200, `10.0.20.20/24`, virtual gateway |

PC5 uses a second IP subnet on VLAN 100; it is not in VLAN 30 and
its gateway has no VRRP redundancy. R1 and R2 use `eth0.100` and `eth0.200`;
R3 uses untagged `eth0` for VLAN 100.

## Inspect the baseline

1. On switches, inspect `show spanning-tree`, `show vlan` and
   `show mac address-table`. SW1 has the lowest configured RSTP priority (4096),
   followed by SW2 (8192) and SW3 (12288).
2. On routers, inspect `show ip ospf neighbor` and `show ip route ospf`.
   OSPF area 0 uses 1/4-second hello/dead timers on VLAN 100. R1/R2 priorities
   are 100/50; R3 has priority 0. DR/BDR election depends on startup order and
   existing elections, so verify the actual result.
3. On R1/R2, inspect `show vlan interfaces`, `show vrrp platform` and
   `show vrrp`. R2 has VRRP priority 250 and R1 has 200; R2 is the expected
   Master after convergence with both routers healthy.
4. From PC4 and PC6, ping `10.0.20.1`, then a remote PC such as `10.0.3.10`.
   Test PC1-to-PC2/PC3 routing separately. Record results before inducing faults.
5. Inspect `show lldp neighbors detail` on switches and R1/R2.
   R3's supplied snippet does not enable LLDP.

## Monitoring caveats in the supplied snapshot

The community `labMonitor` is a public example value, not a private credential.
R1 and SW2 allow any source (`0.0.0.0/0`); constrain this to the intended polling
host/subnet before using the configuration beyond an isolated practice lab.
R1 listens at `10.255.0.1`; SW2 uses its management address `10.0.20.20`.

R2 listens at its own loopback, `10.255.0.2`, and permits only source
`10.0.1.10/32` (PC1). The supplied snapshot pointed R2's listener at R1's address;
this example corrects that address and enables passive OSPF advertisement on
R2's loopback so PC1 can reach it. Inspect `show snmp` and routing before polling.
R3's loopback is not enabled in OSPF; a router ID alone does not advertise a
loopback. SW1, SW3 and R3 have no enabled SNMP polling baseline.

## Recovery exercise

With continuous pings from a VLAN 200 PC to PC3, unplug the R2–SW2 link using
Weblab's link controls. Inspect R1 becoming VRRP Master and traffic recovering.
Reconnect the link and inspect R2's return. Measure loss rather than assuming
zero-loss convergence.

Separately interrupt one inter-switch cable, observe the RSTP port roles and
repeat the reachability checks. Restore each cable before the next experiment.
Compare a switching-path failure with a virtual-gateway failure.

Save FRR changes with `write memory`. On LL2S, commit interactive edits with
`commit` or `end`, then use `write memory`. Export a stopped-lab ZIP to preserve
saved configuration; a JSON export does not contain device storage or images.
