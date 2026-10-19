# Internet gateway helper

For installer/prebuilt users, the [complete helper setup](../../docs/internet.md#build-the-gateway-image)
includes fetching the matching source, building on the Docker host and verifying
the image. Build from the repository root:

```sh
docker build -f packaging/internet/Dockerfile -t weblab-internet:dev .
```

See [Internet cloud](../../docs/internet.md) for setup and routing examples.
The helper uses unmodified Alpine packages (Python, iproute2, iptables, dnsmasq).
It has no Docker socket, host filesystem mounts or interactive console.
Weblab supplies normalized JSON via WL_INTERNET_CONFIG, starts the helper with
a read-only root filesystem and temporary /run, and attaches the lab relay only
after its firewall, addresses, return routes and DNS listener are ready.
The Docker uplink and internal TAP/macvlan lab interface are separate networks.
DNS uses Docker's embedded resolver on the uplink. iptables-restore --noflush
preserves Docker's resolver NAT rules in the helper namespace.

Do not run this helper as a general-purpose service or on host networking.
Only the lifecycle manager creates its configuration, interfaces and resources.
No prebuilt helper image is published with this change; source-build use only.

Package licenses and sources remain those of Alpine and their upstream authors:
[Alpine aports](https://gitlab.alpinelinux.org/alpine/aports),
[dnsmasq](https://thekelleys.org.uk/dnsmasq/doc.html),
[Netfilter](https://www.netfilter.org/),
[iproute2](https://git.kernel.org/pub/scm/network/iproute2/iproute2.git/),
[Python](https://www.python.org/).
Before distributing a binary helper image, include the applicable notices and
corresponding-source arrangements for its packaged components, as for the other
Weblab container distributions. Weblab's original helper code is MIT licensed.
