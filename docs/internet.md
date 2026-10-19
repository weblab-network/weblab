# Internet cloud

[Help index](README.md) · [Device profiles](devices.md)

Drag **Internet** from the device library onto the map and cable its **eth0** to
a lab device. Clicking the cloud opens a movable, resizable settings window.
The first version provides an IPv4 gateway, outbound NAT and a DNS forwarder.
It uses a dedicated Docker bridge for its uplink; no physical NIC is bridged.

The separate **Network cloud** library item is a customizable drawing of an
unmanaged network. It has no forwarding or Internet access. See
[network clouds for diagrams](topology.md#network-clouds-for-diagrams).

## Build the gateway image

Use Weblab **0.10.0 or later**. The gateway is a separate, locally built image;
the installer does not download or build it. Build it on the **same Docker host**
that runs Weblab's lab devices, using the same Docker daemon/context. Building
on your browser's computer or inside the Agent container will not make the
image available to Weblab. The host needs Internet access to download the base
image and Alpine packages during this build.

If you installed Weblab using the installer or a prebuilt image, install Git
if necessary, then fetch the matching release source into a new directory:

```sh
git clone --depth 1 --branch v0.10.0 https://github.com/weblab-network/weblab.git weblab-0.10.0
docker build -f weblab-0.10.0/packaging/internet/Dockerfile \
  -t weblab-internet:dev weblab-0.10.0
```

If you already have the matching Weblab source checkout, run this from its root:

```sh
docker build -f packaging/internet/Dockerfile -t weblab-internet:dev .
```

Use `sudo docker` if your account requires it. Verify the resulting image:

```sh
docker image inspect weblab-internet:dev --format '{{index .Config.Labels "network.weblab.internet"}}'
```

The command should print `1`. Keep the `weblab-internet:dev` tag: it is the
helper name Weblab expects by default. You do not need to run the helper yourself,
add a Compose service or restart a Weblab 0.10.0 container just to make this
image available. Weblab creates and removes gateway containers when you start
and stop Internet nodes. Continue with [Direct PC access](#direct-pc-access)
or [A network behind FRR](#a-network-behind-frr).

If your core is older than 0.10.0, follow the
[upgrade instructions](installation.md#upgrade-or-move-an-installation) first. Source installations
must rebuild/recreate the core using their normal Compose command and overlays
(Agent/KVM), project name and volumes. Use `docker-compose` if that is the
installed command, or `docker compose` for the plugin. Recreating Weblab stops
its lab; save and stop your work first.

Native Python deployments can also use the helper image. Docker, Linux TAP
support and the existing host-network/NET_ADMIN requirements apply. An operator
can set `WL_INTERNET_IMAGE` to another locally built tag of this gateway image;
it must carry the gateway compatibility label. This is a server setting, not
an image name accepted from topology imports.

## Direct PC access

1. Add a cloud and an Alpine PC; connect their eth0 ports.
2. Leave the PC's IPv4 and gateway fields blank.
3. Choose **Start lab**. Weblab starts the gateway before the PC.

By default the cloud is **192.0.2.1/24**. The directly connected PC receives
**192.0.2.2/24**, a default route via **192.0.2.1**, and that address as its DNS
resolver. These are startup settings, not DHCP. Changing the cloud's address
changes the automatic PC address accordingly. Explicit PC addressing and its
chosen default gateway are preserved; select an address on the cloud subnet.

Verify in the PC console:

```sh
ip route
ping -c 3 1.1.1.1
nslookup dl-cdn.alpinelinux.org
apk update
```

## A network behind FRR

Connect **PC → FRR → Internet**. Example addressing:

| Device/interface | IPv4 | Purpose |
| --- | --- | --- |
| PC eth0 | 10.44.0.10/24 | Gateway 10.44.0.1 |
| FRR eth0 | 10.44.0.1/24 | Lab LAN |
| FRR eth1 | 192.0.2.2/24 | Cloud-facing interface |
| Cloud eth0 | 192.0.2.1/24 | Internet gateway and DNS |

Configure FRR through its normal CLI:

```text
configure terminal
interface eth0
 ip address 10.44.0.1/24
exit
interface eth1
 ip address 192.0.2.2/24
exit
ip route 0.0.0.0/0 192.0.2.1
end
write memory
```

In the cloud settings, add a **Return route** with destination **10.44.0.0/24**
and next hop **192.0.2.2**. The cloud both routes replies to that network and
performs NAT for it. Add other routed lab prefixes individually. Weblab does
not replace FRR routes or silently assume that all private networks belong to
your lab. Stop all nodes before changing the cloud settings.

A PC behind FRR can query the cloud explicitly:

```sh
nslookup dl-cdn.alpinelinux.org 192.0.2.1
printf 'nameserver 192.0.2.1\n' > /etc/resolv.conf
```

The last command configures this running PC's resolver; PC filesystem changes
do not survive recreation. Automatic cloud DNS setup applies only to a PC
connected directly to the cloud.

## Access policy and limits

- Only administrators can create/change clouds or start their Internet access
  when authentication is enabled. Writers can operate the rest of a lab after
  its cloud is started by an administrator, and can stop it.
- The gateway allows outbound IPv4 sourced from its attached subnet and its
  configured return networks. It accepts reply traffic and blocks unsolicited
  inbound forwarding. DNS and ping to the gateway are allowed from those networks.
- This is outbound host connectivity, including reachable host/LAN destinations;
  it is **not an Internet-only destination filter**. The ordered firewall rule
  editor (protocol, source/destination, ports and allow/deny) is a later step.
- One cloud has one lab cable. Connect it to a router or switch to serve multiple
  devices. Direct Alpine PC and routed Weblab FRR are the initial tested cases;
  other Ethernet devices require their usual addressing/routing configuration.
- IPv6 Internet forwarding, DHCP, inbound port forwarding and physical-interface
  bridging are not included. The cloud has settings rather than a shell console.
- Cloud settings survive JSON and saved ZIP export/import; runtime connections,
  DNS cache and firewall connection state do not. Stop/Start recreates the gateway.
- The cloud cable uses the usual link relay: **Monitor traffic** and directional
  frame blocking work on its lab side. Captures show traffic before outbound NAT.

Docker allocates the uplink subnet. If it overlaps the cloud subnet or a return
network, startup fails with a message. Use **Docker uplink → Subnet** to choose
a different unused subnet. No changes are made to unrelated Docker networks,
the host's default route or the Agent companion's networking. Docker manages
the normal NAT/firewall rules for the dedicated uplink bridge; the gateway's
lab-facing firewall and NAT live inside its own container namespace.
