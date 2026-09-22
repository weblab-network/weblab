# FRR OSPF practice

An original introductory routing exercise with two FRR routers and two Alpine
PCs. Import [the topology JSON](frr-ospf.json) into a fresh lab, then open this file
in Weblab's Instructions window. No KVM or vendor images are needed.

Before starting, pull these images on the Docker host:

```sh
docker pull quay.io/frrouting/frr:10.7.1
docker pull alpine:latest
```

Use the supported FRR profile described in [device help](../docs/devices.md#frrouting).
The initial snippets set addressing only. Existing saved configurations take
precedence; importing the JSON over an old copy of this lab does not reset it.

## Address plan

| Device | Interface | Address |
| --- | --- | --- |
| R1 | eth0 (to R2) | 10.0.12.1/30 |
| R2 | eth0 (to R1) | 10.0.12.2/30 |
| R1 | eth1 (to PC1) | 10.0.1.1/24 |
| R2 | eth1 (to PC2) | 10.0.2.1/24 |
| R1 / R2 | lo | 10.255.0.1/32 / 10.255.0.2/32 |
| PC1 / PC2 | eth0 | 10.0.1.10/24 / 10.0.2.10/24 |

Each PC uses its local router as the default gateway. Initially, each PC can ping
its gateway; PC1 cannot reach PC2 because the routers have no remote LAN routes.
Router consoles open directly into `vtysh`.

## Tasks

1. Inspect `show interface brief`, `show ip route` and `show running-config`.
2. Enable OSPF area 0 on the transit link, both LANs and both loopbacks. Choose
   the loopback address as each router's OSPF router ID.
3. Make the LAN-facing interfaces passive; they must still be advertised.
4. Verify adjacency, learned routes, and successful PC1-to-PC2 pings.
5. Save both routers with `write memory`.
6. On the R1–R2 link, block one direction. Watch the neighbors and routes age
   out. Restore traffic and observe recovery. Frame loss leaves carrier up.
7. Try Unplug on one FRR endpoint. Inspect `show interface eth0`. Reconnect and
   confirm that forwarding recovers. The peer's carrier is controlled separately.
8. Stop the lab, save a ZIP, restore it, start it and verify forwarding again.

## Configuration reference

R1:

```text
configure terminal
router ospf
 ospf router-id 10.255.0.1
 network 10.0.0.0/16 area 0
 network 10.255.0.1/32 area 0
 passive-interface eth1
end
write memory
```

R2:

```text
configure terminal
router ospf
 ospf router-id 10.255.0.2
 network 10.0.0.0/16 area 0
 network 10.255.0.2/32 area 0
 passive-interface eth1
end
write memory
```

Useful checks: `show ip ospf neighbor`, `show ip route ospf`, `show interface eth0`.
From PC1 run `ping -c 4 10.0.2.10`. Allow time for adjacency and route convergence;
these default OSPF timers are deliberately not accelerated.

FRR is a routing suite, not a replacement for STP/MSTP switch exercises. Linux
shell changes and installed packages are not included in FRR ZIP persistence.
