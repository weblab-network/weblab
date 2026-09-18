# EXOS: VLANs and routing

This original introductory lab uses three Virtual EXOS switches and two Alpine
PCs. R1 is an EXOS switch performing the router role. It is not a certification
exam or a claim of complete hardware feature emulation.

The **EXOS demo container** includes the saved baseline below. The companion
[JSON](exos-demo.json) contains topology and PC addressing only; importing JSON
does not configure EXOS. For a manually created lab, apply the commands in
[configs.json](../packaging/exos/configs.json) through each switch console, then
run `save configuration` and answer `y`.

## Start and explore

Click **Start lab**, then allow a few minutes for EXOS to boot. Open each console
and wait until the authentication service is ready. Log in as `admin` with an
empty password. The demo is intended for an isolated, trusted lab environment.

```text
             R1 (EXOS, routing)
           1 /                 \ 2
     VLAN 10 tagged         VLAN 20 tagged
         1 /                     \ 1
        SW1                      SW2
         2                        2
      untagged                 untagged
         |                        |
        PC1                      PC2
```

| Device | Address | Purpose |
| --- | --- | --- |
| R1 USERS VLAN 10 | 10.10.10.1/24 | PC1 gateway |
| R1 SERVERS VLAN 20 | 10.10.20.1/24 | PC2 gateway |
| PC1 | 10.10.10.10/24 | Gateway 10.10.10.1 |
| PC2 | 10.10.20.10/24 | Gateway 10.10.20.1 |

The switches use data ports 1 and 2. Mgmt is unconnected. All EXOS nodes are
represented as switches in Weblab, including R1.

## 1. Inspect the baseline

On each EXOS console:

```text
disable cli paging
show vlan
show ports information
show lldp neighbors
```

On R1 also run:

```text
show iproute
show iparp
```

Identify the tagged uplinks, untagged PC ports and directly connected routes.
SW1 and SW2 switch traffic; R1 has IP forwarding enabled on its two VLANs.

## 2. Test inter-VLAN forwarding

On PC1:

```sh
ip address show dev eth0
ip route
ping -c 4 10.10.10.1
ping -c 4 10.10.20.10
```

On PC2:

```sh
ping -c 4 10.10.10.10
```

Explain why PC1 sends off-subnet traffic to R1 and how the VLAN tag changes as
frames pass between access ports and uplinks. First packets can be lost while
the devices learn neighbors; repeat the check once the lab has settled.

## 3. Introduce and diagnose a fault

Open the actions for the R1–SW1 link and block one traffic direction. Try ping
again, inspect the link state and compare what each endpoint observes. Interfaces
remain up during a traffic block. Restore both directions and verify recovery.

Virtual EXOS does not currently expose Weblab's carrier **Unplug** control.
This topology has no redundant Layer 2 path; it is not an STP convergence lab.

## 4. Save your work

Use `save configuration` on every changed EXOS device, answer `y`, then **Stop
lab** and export **Saved lab ZIP**. That ZIP preserves the writable EXOS disks;
plain JSON does not. PCs retain their configured address/gateway, but their shell
files are temporary.

The demo is seeded only into an empty data directory. Restarting or updating its
container preserves your changes. To repeat the baseline, use a new empty data
directory or import a baseline ZIP you saved earlier.
