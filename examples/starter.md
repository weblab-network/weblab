# Router–switch–PC starter

[Help index](../docs/README.md) · [Topology JSON](starter.json)

Import the JSON into a fresh lab, or use **Create a starter topology** after
installing suitable IOL images. The JSON names `cisco_iol-17.18.02.bin` and
`cisco_iol-l2-17.18.02.bin`; change them to matching installed IOL filenames if
necessary. It also needs the host Docker image `alpine:latest`.

Start the lab and wait for the guests to finish booting. This JSON has no startup
snippets, so fresh router/switch interfaces need the commands below. If you use
IOSv through the built-in starter, use the GigabitEthernet names shown on the map
instead. Reusing existing node IDs may reuse earlier saved configuration.

The starter connects R1 Ethernet0/0 → SW1 Ethernet0/0, and SW1 Ethernet0/1 → PC1 eth0. The PC is assigned `10.0.10.10/24`, gateway `10.0.10.1`.

R1:

```text
enable
configure terminal
hostname R1
interface Ethernet0/0
 ip address 10.0.10.1 255.255.255.0
 no shutdown
end
write memory
```

SW1:

```text
enable
configure terminal
hostname SW1
interface range Ethernet0/0 - 1
 switchport mode access
 spanning-tree portfast
 no shutdown
end
write memory
```

From PC1: `ping -c 3 10.0.10.1`. ARP and spanning-tree convergence can delay the first reply.

## Check the result

PC1 should reach R1 at `10.0.10.1`. If not, check interface state and addressing,
SW1's access VLAN and spanning-tree state, and any active link-fault settings.
Save device configuration before stopping and exporting a saved ZIP.

The included JSON is structurally validated; that check does not execute the
commands or establish compatibility with every image release.
