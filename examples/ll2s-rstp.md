# LL2S: RSTP and VLAN isolation

Import [ll2s-rstp.json](ll2s-rstp.json) into a disposable lab. It contains three
LL2S switches in a triangle and three Alpine PCs. Requires local `ghcr.io/weblab-network/ll2s:0.2.0`
(OVS-based version 0.2.0), `alpine:latest`, and the host `openvswitch` module.

| Device | Role |
| --- | --- |
| SW1 | Priority 4096, intended RSTP root; PC1 access VLAN 10 |
| SW2 | Priority 8192; PC2 access VLAN 20 |
| SW3 | Priority 12288; PC3 access VLAN 10 |
| PC1 / PC2 / PC3 | 10.0.10.1 / .2 / .3, all /24, no gateway |

Switch `eth0` and `eth1` are trunks allowing VLANs 10 and 20. `eth2` is the PC
access port with portfast enabled. All VLANs share one spanning tree.
The PCs deliberately share an IP subnet across different VLANs to demonstrate
that VLAN separation prevents direct Layer 2 communication.

1. Start the lab. Run `show spanning-tree`, `show vlan`, and `show interfaces` on
   each switch. Confirm SW1 is root and SW3's path toward SW2 is alternate.
2. From PC1, ping `10.0.10.3`; it should succeed once the tree converges. Ping
   `10.0.10.2`; it should fail because PC2 belongs to VLAN 20.
3. Unplug SW1's endpoint of cable `s13` using Weblab's cable controls. Observe
   the alternate path becoming active and PC1-to-PC3 connectivity recovering.
   Reconnect the cable and inspect the tree again.
4. Change a hostname using `configure terminal`, `hostname YOURNAME`, `commit`,
   `end`, then `write memory`. Stop/start the switch and confirm it persists.
5. Save all intended changes, Stop lab, and export a Saved lab ZIP. Import it
   into a disposable lab and verify root election, forwarding and VLAN isolation.

`commit` and interactive `end` apply candidate edits and briefly interrupt
managed ports. Neither saves startup configuration; use `write memory` for that.

For optional LLDP discovery or management/SNMP polling, see the
[LL2S monitoring setup](../docs/devices.md#ll2s-discovery-and-monitoring).
This baseline leaves those features disabled.
