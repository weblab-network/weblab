# Weblab help

[Help index](README.md) · [Project overview](../README.md)

Start with [installation](installation.md), then build a small lab using the
[starter exercise](../examples/starter.md). These pages describe the current
workspace and its supported profiles.

Release notes: [v0.11.1](releases/v0.11.1.md), [v0.11.0](releases/v0.11.0.md), [v0.10.2](releases/v0.10.2.md), [v0.10.1](releases/v0.10.1.md), [v0.10.0](releases/v0.10.0.md), [v0.9.1](releases/v0.9.1.md), [v0.9.0](releases/v0.9.0.md), [v0.8.3](releases/v0.8.3.md), [v0.8.2](releases/v0.8.2.md), [v0.8.1](releases/v0.8.1.md), [v0.8.0](releases/v0.8.0.md), [v0.7.0](releases/v0.7.0.md), [v0.6.0](releases/v0.6.0.md),
[v0.5.1](releases/v0.5.1.md). Authentication and shared instructions are available starting with v0.7.0.

## User guide

[Optional authentication and roles](authentication.md) — fixed accounts, login,
writer/reader permissions, admin activity records and current integration limits.

1. [Installation and upgrades](installation.md) — Docker, native Linux, KVM and remote access.
2. [Images and device profiles](devices.md) — supported releases, ports, boot and saving.
3. [Topology and window arrangement](topology.md) — themes, cabling, panels, zoom, fullscreen and tiling.
4. [Consoles](consoles.md) — tabs, floating windows, mobile keys, clipboard and shared input locks.
5. [Instructions viewer](instructions.md) — preview Markdown/text exercises and publish them to all stations.
6. [Link failures and traffic monitoring](link-actions.md) — directional frame loss, carrier controls and packet capture.
7. [Saving and restoring labs](backups.md) — JSON, configuration snippets, ZIP, logs, workspace reset and state precedence.
8. [Creating practice labs](practice-labs.md) — topology JSON contract and authoring guidance.
9. [Storage monitoring](storage.md) — filesystem warnings, per-node usage and growth measurements.
10. [AI assistants and MCP](automation.md) — optional agent discovery, topology previews and approved starts.
11. [Agent window](agent.md) — optional Ollama/OpenAI conversations, previews and approved lab/console actions.
12. [Troubleshooting](troubleshooting.md) — common setup, console, storage and networking issues.
13. [Supported scope and limitations](limitations.md) — deployment boundaries and known gaps.

[Prebuilt container images and EXOS demo](containers.md) provide an alternative
to building locally, including a wired introductory topology and bundled EXOS image.

[Internet cloud](internet.md) — optional IPv4 NAT gateway, live outbound firewall, inbound port forwarding, DNS and return routes
for Internet access from lab PCs and routers (separate prebuilt or locally built helper).

## Examples and contributors

- [Starter lab](../examples/starter.md) and [JSON](../examples/starter.json).
- [LL2S RSTP/VLAN practice](../examples/ll2s-rstp.md) and [JSON](../examples/ll2s-rstp.json).
- [FRR OSPF practice](../examples/frr-ospf.md) and [JSON](../examples/frr-ospf.json), without KVM/vendor images.
- [OSPF practice](../examples/ospf-practice.md) and [JSON](../examples/ospf-practice.json).
- [Development and testing](development.md).
- [Agent instructions](../AGENTS.md).
- [Third-party notices](../THIRD_PARTY.md).

Relative links work on GitHub or in a Markdown reader. The built-in Instructions
viewer opens one file at a time; use **Open file** for another chapter.
