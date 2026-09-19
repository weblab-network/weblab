# EXOS: VLANs and routing

This original introductory lab uses three Virtual EXOS switches and two Alpine
PCs. R1 is an EXOS switch performing the router role. It is not a certification
exam or a claim of complete hardware feature emulation.

The **EXOS demo container** bundles the vendor image and the wired topology.
EXOS switches start with factory defaults; no saved configuration is included.
The companion [JSON](exos-demo.json) defines the same topology and pre-fills the
PC addresses. You can use your own configurations or follow the optional VLAN
and routing exercise below.

## Start and explore

Click **Start lab**, then allow a few minutes for EXOS to boot. Open each console
and wait until the authentication service is ready. Log in as `admin` with an
empty password. On first login, answer `q` to accept defaults for the remaining
setup questions. The demo is intended for an isolated, trusted lab environment.

The diagram and addresses below show the target for the optional routing exercise:

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

## 1. Verify the switch links with LLDP

On each EXOS console:

```text
disable cli paging
enable lldp ports all
show ports information
show lldp neighbors
```

Allow time for LLDP advertisements. R1 should see SW1 and SW2 on ports 1 and 2;
SW1 and SW2 each see R1 on port 1. Initially all switches may advertise the same
factory system name. PCs do not advertise LLDP by default. This checks the direct
switch links; inter-VLAN ping requires configuration first.

## 2. Optional: configure VLANs and routing

Use your own configuration, or apply these commands to the corresponding consoles.
R1 will route between VLANs 10 and 20; SW1 and SW2 provide PC access ports.

### R1

```text
disable cli paging
configure vlan Default delete ports all
configure snmp sysName R1
create vlan USERS tag 10
configure vlan USERS add ports 1 tagged
configure vlan USERS ipaddress 10.10.10.1/24
enable ipforwarding vlan USERS
create vlan SERVERS tag 20
configure vlan SERVERS add ports 2 tagged
configure vlan SERVERS ipaddress 10.10.20.1/24
enable ipforwarding vlan SERVERS
enable lldp ports all
```

### SW1

```text
disable cli paging
configure vlan Default delete ports all
configure snmp sysName SW1
create vlan USERS tag 10
configure vlan USERS add ports 1 tagged
configure vlan USERS add ports 2 untagged
enable lldp ports all
```

### SW2

```text
disable cli paging
configure vlan Default delete ports all
configure snmp sysName SW2
create vlan SERVERS tag 20
configure vlan SERVERS add ports 1 tagged
configure vlan SERVERS add ports 2 untagged
enable lldp ports all
```

Check `show vlan` on each switch and `show iproute` on R1.

## 3. Test inter-VLAN forwarding

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

## 4. Introduce and diagnose a fault

Open the actions for the R1–SW1 link and block one traffic direction. Try ping
again, inspect the link state and compare what each endpoint observes. Interfaces
remain up during a traffic block. Restore both directions and verify recovery.

Virtual EXOS does not currently expose Weblab's carrier **Unplug** control.
This topology has no redundant Layer 2 path; it is not an STP convergence lab.

## 5. Save your work

Use `save configuration` on every changed EXOS device, answer `y`, then **Stop
lab** and export **Saved lab ZIP**. That ZIP preserves the writable EXOS disks;
plain JSON does not. PCs retain their configured address/gateway, but their shell
files are temporary.

The bundled topology is installed only into an empty data directory. Restarting or updating its
container preserves your changes. For factory-default switches, use a new empty data
directory. To return to your own saved configuration, import a ZIP you saved earlier.
