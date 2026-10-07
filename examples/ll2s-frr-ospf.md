# LL2S RSTP + FRR OSPF

Three LL2S switches form a triangle. Each switch connects one FRR router.
Everything is preconfigured on fresh nodes; no manual configuration is required.
The six containers have a combined memory limit of 2304 MiB, plus Weblab/host overhead.
Requires `ghcr.io/weblab-network/ll2s:0.2.0`, `quay.io/frrouting/frr:10.7.1`,
and the host `openvswitch` module. Pull both images on the Docker host first.

```text
          R1
          |
         SW1
        /   \\
      SW2---SW3
       |     |
       R2    R3
```

Switch eth0/eth1 are trunks carrying VLAN 100; eth2 is an access port with portfast toward its router.
SW1 eth0—SW2 eth0; SW2 eth1—SW3 eth0; SW1 eth1—SW3 eth1.
RSTP priorities are SW1=4096, SW2=8192, SW3=12288. Expect SW1 as root and SW3 eth0 as alternate/discarding.

| Router | Transit eth0 (VLAN 100) | Loopback / router ID |
| --- | --- | --- |
| R1 | 10.100.0.1/24 | 10.255.0.1/32 |
| R2 | 10.100.0.2/24 | 10.255.0.2/32 |
| R3 | 10.100.0.3/24 | 10.255.0.3/32 |

OSPF area 0 runs across the switched broadcast LAN. R1 priority=100, R2=50, R3=0; start R1 then R2 then R3 to obtain R1 as DR and R2 as BDR. Hello/dead timers are 1/4 seconds. Loopbacks are advertised passively.
RSTP supplies a loop-free Ethernet LAN; OSPF uses that LAN to exchange routes. These are separate protocols working at different layers.

## Verify

On every LL2S console: `show spanning-tree`, `show vlan`, `show mac address-table`.
On every FRR console: `show ip ospf neighbor`, `show ip route ospf`, `show ip ospf interface eth0`.
Expect two Full neighbors on each router and OSPF routes to the two remote loopbacks.
From R1's vtysh console, `ping 10.255.0.3 -c 3 -W 2` tests a destination learned through OSPF.

## Observe recovery

Use the cable controls to unplug the SW3 end of the SW1–SW3 cable. Inspect SW3 eth0 becoming Root/Forwarding, check OSPF and repeat the ping. Reconnect the endpoint and inspect the original tree returning. Allow initial convergence and do not assume loss-free failover.

## Saving

LL2S interactive edits need `commit` or `end`, then `write memory`. Each commit briefly interrupts its managed ports. FRR uses `write memory`. Save before Stop and export a Saved lab ZIP to preserve device state. Initial snippets apply only to fresh nodes; existing saved state takes precedence.


## Live verification (6 October 2026)

The running lab was extended with three Alpine PCs, one behind each router:
PC1 10.0.1.10/24 via 10.0.1.1, PC2 10.0.2.10/24 via 10.0.2.1, and
PC3 10.0.3.10/24 via 10.0.3.1. Each router uses eth1 for its PC LAN and
advertises that LAN passively in OSPF area 0. The live configuration advertises
these LANs rather than the loopbacks used in the six-node example above.

Verified through MCP consoles:

- All three routers had two Full OSPF neighbors; R1 was DR and R2 was BDR.
- SW1 was the RSTP root, and SW3 eth0 was Alternate/Discarding.
- PC1-to-PC3 ping returned 3/3 replies before the fault.
- With the SW3 end of the SW1–SW3 cable unplugged, SW3 eth0 became
  Root/Forwarding and eth1 became Disabled/Discarding. R3's OSPF neighbor
  uptimes continued, and another PC1-to-PC3 ping returned 3/3 replies.
- The cable was reconnected after the test. No permanent configuration change
  was needed for the failure simulation. Ping was sampled after failover;
  this was not a measurement of transient packet loss or convergence time.

The example now uses interface-level OSPF activation consistently, including
on its loopbacks. The six-node example's loopback advertisement was not the
traffic path exercised by this live nine-node test.

For optional discovery and polling, see [LL2S LLDP and SNMP setup](../docs/devices.md#ll2s-discovery-and-monitoring). The baseline leaves these disabled.
