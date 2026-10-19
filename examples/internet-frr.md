# FRR Internet gateway exercise

Import [internet-frr.json](internet-frr.json) into an empty or backed-up workspace.
It supplies a configured **PC1 → R1 → Internet** topology. Requires the current
Weblab source, the locally built Internet helper, Alpine, and Weblab FRR
`ghcr.io/weblab-network/weblab-frr:10.7.1-wl1`.
See [Internet setup](../docs/internet.md) for build instructions.

R1 eth0 is 10.44.0.1/24, PC1 is 10.44.0.10/24; R1 eth1 is 192.0.2.2/24.
The cloud gateway is 192.0.2.1/24, with a return route for 10.44.0.0/24 through
R1. Start lab as an administrator when authentication is enabled.

1. On PC1, run `ip route`, then `ping -c 3 192.0.2.1` and `ping -c 3 1.1.1.1`.
2. Run `nslookup dl-cdn.alpinelinux.org 192.0.2.1`. For package downloads, set
   `printf 'nameserver 192.0.2.1\n' > /etc/resolv.conf`, then run `apk update`.
3. In R1, inspect `show ip route`. Identify the connected LAN, uplink and default.
4. Monitor the R1–cloud cable while pinging. Its frames retain PC1's lab source
   address; translation occurs inside the cloud after this capture point.
5. Stop the lab, remove the cloud return route and restart. Explain why merely
   giving R1 a default route does not provide return routing/NAT for PC1.
   Restore the route after the experiment.

No router NAT commands are required: the cloud performs NAT for its subnet and
its listed return networks. Existing saved FRR configuration overrides the JSON
startup snippet; use a fresh exercise workspace if that state differs.
PC resolver edits are temporary. IPv6, DHCP and custom firewall rules are not
part of this first Internet-cloud implementation.

Validation: the JSON is structurally validated. Equivalent direct-PC and routed
FRR scenarios have native checks in `tests/internet_native.py` for NAT, DNS,
traffic capture, inbound denial, restart and cleanup. Actual Internet availability
depends on the Docker host's connection and firewall policy.
