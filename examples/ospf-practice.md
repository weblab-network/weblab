# OSPF multi-area practice

An original routing exercise for intermediate study, not a reproduction of an
exam or a complete certification blueprint. Import `ospf-practice.json` into a
fresh lab. It requires `cisco_iol-17.18.02.bin` and an installed `alpine:latest`
Docker image; adjust filenames if necessary. Three routers use 1024 MB each.
The snippets configure hostnames, loopbacks and interface addresses, but no OSPF.

| Segment | Endpoint A | Endpoint B | Target OSPF area |
| --- | --- | --- | --- |
| R1 E0/0—R2 E0/0 | 10.0.12.1/30 | 10.0.12.2/30 | 0 |
| R2 E0/1—R3 E0/0 | 10.0.23.1/30 | 10.0.23.2/30 | 10 |
| R1 E0/1—PC1 eth0 | 192.0.2.1/24 | 192.0.2.10/24 | 0, passive on R1 |
| R3 E0/1—PC2 eth0 | 198.51.100.1/24 | 198.51.100.10/24 | 10, passive on R3 |
| R1/R2/R3 Loopback0 | 10.255.0.1/32, .2/32, .3/32 | — | R1/R2 in 0; R3 in 10 |

PC default gateways are the adjacent router addresses. Let devices finish booting
before testing. Initially each PC should reach its own gateway and each router
should reach its directly connected router neighbor. PC1 should not yet reach
PC2 because no remote routes are configured.

## Tasks

1. Configure OSPF process 10 with router IDs matching the loopbacks. Follow the
   area plan above; R2 is the area border router.
2. Make interfaces passive by default and enable neighbor formation only on
   router-to-router links. Advertise both client LANs and all loopbacks.
3. Verify full adjacencies R1–R2 and R2–R3, and end-to-end PC reachability. Explain
   which routes are intra-area versus inter-area on R1 and R3.
4. Set the R2–R3 link to OSPF point-to-point network type on both ends. Verify
   that the adjacency recovers and explain the difference in DR/BDR behavior.
5. Deliberately introduce an area mismatch on one end of R2–R3. Diagnose it,
   restore the configuration, and explain why local link pings still work while
   OSPF fails. Do not use static/default routes to bypass the exercise.

## Verification

Use `show ip interface brief`, `show ip ospf neighbor`, `show ip ospf interface`,
`show ip route ospf` and `show ip protocols` on the routers. Use
`ping -c 3 198.51.100.10` from PC1 and the reverse from PC2. At completion the
adjacencies should be FULL and both PCs should reach each other and all loopbacks.
Save Cisco configurations with `write memory` before stopping/exporting.

Initial snippets do not overwrite existing NVRAM. Reimporting this JSON over a
lab with the same node IDs does not remove an existing solution. Use a fresh lab
or new IDs for a clean attempt. Structural validation does not validate IOS
commands; boot testing and protocol verification are separate checks.
