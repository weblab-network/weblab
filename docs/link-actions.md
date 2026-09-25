# Simulating link failures

[Help index](README.md) · [Project overview](../README.md)

While the lab runs, click/tap a cable to open **Link traffic**. Right-click also
opens the actions, including on a stopped lab; keyboard users can focus a cable
and press Enter or Space. The controls work on the floating topology too.
Endpoints are named explicitly, so A → B means frames sent by A toward B.

| Action | Effect |
| --- | --- |
| Block A → B | Drop every frame sent from A; B → A continues. |
| Block B → A | Drop every frame sent from B; A → B continues. |
| Block both directions | Drop frames in both directions; keep interfaces up. |
| Clear frame loss | Clear directional frame-loss settings. It does not reconnect an unplugged endpoint. |
| Unplug a supported endpoint | Request guest link-down and immediately block traffic both ways on its cable. IOL detects loss after about ten seconds. |
| Reconnect that endpoint | Report link-up again. Any separately selected frame-loss settings remain active. |

Directional loss is a silent unidirectional-fiber failure model for exercises
such as UDLD and STP loop guard: the reverse direction remains intact and guest
carrier is not lowered. All Ethernet frames are affected, including BPDUs, LLDP,
OSPF multicast and VLAN-tagged traffic. Protocol availability and its response
still depend on the selected image and configuration. This is not a simulation
of optical power levels or every physical fiber/transceiver failure mode.

Unplug/Reconnect is available for running **IOSv/IOSvL2** and the tested IOL
profiles **`cisco_iol-17.18.02.bin` / `cisco_iol-l2-17.18.02.bin`**, plus the
[FRR container profile](devices.md#frrouting). Other IOL
filenames/releases, EXOS, Arista and Alpine retain directional frame-loss controls
only. No guest console commands, IOS `shutdown` or saved configuration changes
are involved. Unplug/reconnect both supported endpoints if both guests should
report cable loss; each endpoint's carrier is controlled separately.

For the tested IOL profiles, **Enable cable unplug control (IOL L1)** in the
node Inspector is **off by default**. With all nodes stopped, select the node,
check the option and click **Apply settings**, then start it. Enabling IOL's L1
mode may consume a full CPU core per node; enable it only where carrier-loss
exercises need it. Ordinary forwarding and directional frame blocking still work
with the option off. Disabled carrier buttons show **L1 off**.

The preference is saved per node as `iol_l1` in topology JSON and lab ZIPs.
Existing labs/archives without this field default to off on their next start;
running processes are not changed. The setting cannot change while any node runs.
This saved launch preference is separate from the temporary unplug/block faults.

FRR changes carrier on the port’s parent TAP; the container sees carrier loss
while its interface remains administratively enabled. It does not issue a
shutdown command inside FRR.

IOSv uses QEMU's local QMP `set_link` on a named e1000 NIC. See
[QEMU set_link](https://www.qemu.org/docs/master/interop/qemu-qmp-ref.html#command-set_link)
for notification limitations. IOL nodes with the option enabled launch with `-l` and use a
separate local L1 datagram socket. The controller sends periodic L1 messages
independently of Ethernet forwarding. Unplug stops those messages to the selected
port; the guest normally detects loss after **about ten seconds**. Reconnect
resumes them. The UI shows requested IOL state and an estimated detection interval,
not a guest acknowledgment. Allow ARP/protocol convergence after carrier returns.
Unconnected ports receive no L1 messages. No instant explicit IOL down command
is claimed, and other IOL image profiles require native validation before opt-in.

Green selection appears only with both endpoint nodes running, no configured
frame loss, both requested carriers up, and no pending interval or signaling error. Active frame-loss choices
and interrupted endpoint controls use amber; keyboard focus has a neutral outline.
**Clear frame loss does not reconnect a cable**; **Reconnect does not clear frame
loss**. While unplugged, no frame-loss choice is marked as the effective mode;
the status text still lists any configured directional loss.

Interrupted cables are orange/dashed with an × marker. The tooltip and actions
show the direction or unplugged endpoint, and the map footer counts interrupted
links. The server shares these states across browser sessions. Closing the
window or refreshing the page does not restore traffic.
If an endpoint stops or exits, the dialog lists its state/error and keeps its
carrier button visible but disabled when the image supports carrier control.
The other endpoint's carrier remains independently controlled; a missing peer
can therefore stop BPDUs without lowering the surviving interface. The server
logs unexpected process exits with UTC time and available exit status.

Faults are **temporary runtime state**: they are not topology edits and are not
included in JSON, configuration-text exports or saved-state ZIP. Stopping the
whole lab or restarting the server clears them. Directional loss remains while
one node restarts if other nodes keep the lab running. An endpoint's simulated
carrier state resets when that endpoint stops; a restarted NIC starts connected.
A failed/timed-out QMP operation or failed IOL L1 signal is shown as uncertain and blocks traffic until
you successfully Reconnect the endpoint or stop it. Check the reported error before treating the cable as restored.

## Scope

Packet capture/decoding, random packet loss, latency injection, payload corruption
and moving a cable while devices run are not implemented. Stop the lab before
recabling. See [development and testing](development.md) for the opt-in carrier
and forwarding tests.
