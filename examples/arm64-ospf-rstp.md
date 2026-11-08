# ARM64 — OSPF over redundant LL2S

Import [the topology JSON](arm64-ospf-rstp.json) into an empty/stopped workspace
after following [native ARM64 setup](../docs/arm64.md). Open this document in
Instructions. Import replaces the current topology; export existing work first.

Two FRR routers connect two PC LANs through VLAN 100 on two LL2S switches.
Parallel switch trunks create a redundant Layer 2 path. These are real OSPF,
LLDP and RSTP implementations running in native containers.

## Establish a baseline

1. Start the lab. On both routers, use `show ip ospf neighbor` and
   `show ip route ospf`. Wait for a Full adjacency and the other PC LAN route.
2. On SW1 and SW2, use `show spanning-tree`. Predict the root from the configured
   priorities, then identify which redundant port is alternate/discarding.
3. On the routers, use `show lldp neighbors` to identify the adjacent switches.
   LLDP is enabled in this exercise; SNMP is left disabled.
4. From PC1, run `ping -c 5 10.0.2.10`. Explain why its default gateway is R1,
   while OSPF learns the remote LAN across the switch domain.

## Fail over and recover

Start a traffic capture on a switch trunk. Inspect BPDUs and compare them with
the switch's RSTP port roles. Then unplug the forwarding trunk using the cable
control. Use a bounded ping and check RSTP and OSPF again. Restore the cable and
verify the original topology and end-to-end reachability recover.

Do not infer an exact convergence time from the ping loss count alone. Compare
packet timestamps and device state if you need a measured convergence interval.
Clear any directional frame blocks separately from reconnecting a cable.

## Save

FRR: `write memory`. LL2S: finish candidate edits with `end`, then
`write memory`. Stop the lab before ZIP export. Startup snippets initialize fresh
nodes; saved state takes precedence on subsequent starts.

The topology uses native ARM64 image selectors. For amd64, select the appropriate
installed FRR and LL2S images on fresh nodes; changing selectors does not migrate
saved state or make ZIP archives portable across architectures.

This example was boot-tested on Linux ARM64 with native FRR, LL2S and Alpine
images: OSPF adjacency, RSTP redundancy, LLDP, routed PC traffic, capture decoding,
cable failover/recovery and stopped-lab ZIP restore passed. The separate native
test also enabled and verified FRR SNMPv2c over the lab VLAN.
