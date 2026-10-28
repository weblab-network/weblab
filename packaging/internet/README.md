# Internet gateway helper

Starting with Weblab 0.11.0, the release workflow publishes a tested Linux amd64
helper as `ghcr.io/weblab-network/weblab-internet:0.11.0`, with matching binary,
source and checksum downloads. The tag is available after that release is
published. See [complete setup](../../docs/internet.md#install-a-prebuilt-gateway)
and [source distribution](SOURCES.md).

For a quick local development build, run from the repository root:

```sh
docker build -f packaging/internet/Dockerfile -t weblab-internet:dev .
```

See [Internet cloud](../../docs/internet.md) for setup and routing examples.
The helper uses unmodified Alpine packages (Python, iproute2, iptables, dnsmasq).
It has no Docker socket, host filesystem mounts or interactive console.
Weblab supplies normalized JSON via WL_INTERNET_CONFIG, starts the helper with
a read-only root filesystem and temporary /run, and attaches the lab relay only
after its firewall, addresses, return routes and DNS listener are ready.
The `network.weblab.internet.firewall=1` label declares support for ordered outbound
rules and the unmatched allow/deny action. Rebuild both core and helper from the
updated source before using firewall controls. Older helpers are accepted only for
the original empty-rule, default-allow policy; custom policy is never silently ignored.
The Docker uplink and internal TAP/macvlan lab interface are separate networks.
DNS uses Docker's embedded resolver on the uplink. iptables-restore --noflush
preserves Docker's resolver NAT rules in the helper namespace.

Live firewall changes additionally require
`network.weblab.internet.firewall.live=1` on the running helper. The update
replaces only the outbound policy, preserving NAT, published forwards and
connection tracking. TCP/UDP port forwarding requires
`network.weblab.internet.port-forwards=1`; older helpers accept only an empty
mapping list. Host publications are created with the gateway container, so
editing mappings requires stopping/starting only that Internet node.

A normal Weblab Compose build does not build this separate image. Rebuild it
explicitly with the command above, then stop/start Internet nodes to use the new
helper. Rebuilding the core requires the deployment's normal Compose command
and overlays; recreating the core stops its guests, so save and stop them first.

Do not run this helper as a general-purpose service or on host networking.
Only the lifecycle manager creates its configuration, interfaces and resources.
CI publishes only the `release` target from `Dockerfile.release`. The default `development` target
keeps the quick local build; it is not accepted by the release bundler.

Package licenses and sources remain those of Alpine and their upstream authors:
[Alpine aports](https://gitlab.alpinelinux.org/alpine/aports),
[dnsmasq](https://thekelleys.org.uk/dnsmasq/doc.html),
[Netfilter](https://www.netfilter.org/),
[iproute2](https://git.kernel.org/pub/scm/network/iproute2/iproute2.git/),
[Python](https://www.python.org/).
Release images retain upstream notices and inventories. The corresponding-source
collector/bundler and checks are described in [SOURCES.md](SOURCES.md).
Weblab's original helper code is MIT licensed.
