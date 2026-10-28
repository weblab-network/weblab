# Building and arranging a topology

[Help index](README.md) · [Project overview](../README.md)

<a href="https://weblab.network/assets/internet-network-cloud.png"><img src="https://weblab.network/assets/internet-network-cloud.png" alt="Weblab topology with an Internet gateway, an HQ diagram cloud and a Switches diagram group" width="720"></a>

*Internet gateway on the left; HQ is a diagram-only cloud. The Switches rectangle groups devices visually. All nodes are stopped in this screenshot.*

## Appearance

Open **Layout → Theme** and choose **Dark**, **Light** or **System**. Dark keeps
the original palette and is the initial choice. System follows your operating
system's color preference, including changes while Weblab is open.

The choice is saved in this browser for this Weblab address and synchronized
between its tabs. It applies immediately, including while devices run and for
readers. If browser storage is unavailable, the choice lasts for the current page.
It is independent of the saved topology, exported labs and workspace layout reset.

Themes change workspace colors, including dialogs, Instructions, Agent and traffic
panels. Device icons, authored diagram colors, controls and layout are preserved.
Device CLI terminals follow the theme too, including open, hidden and floating
consoles. Light adjusts text, cursor, selection and ANSI colors for readability;
Dark restores the original terminal palette. Switching does not reconnect consoles,
clear history or change input locks.

Use **Layout → Console theme** to choose **Follow workspace**, **Dark** or
**Light** independently. Follow workspace is the default. This separate browser
preference applies to the terminal area of all your consoles: background, text,
cursor, selection and terminal scrollbar. Toolbars, tabs, menus and window controls
always match the workspace. An explicit console choice stays fixed when the
workspace or operating-system theme changes.

## Network clouds for diagrams

Use **Network cloud** in the library to represent an unmanaged network, a
provider network or a part of the topology outside the lab. It is a plain
cloud outline with its name inside, without a device card or Internet arrow.
Inspector lets you change its name, size (60–200%) and outline color, including
while other devices are running.

Use **Connect** to draw a line from a device interface to the cloud's shared
**Connection (diagram only)** point. Several lines can end on one cloud; they
appear dashed and do not forward packets between connected devices. The real
device interface still belongs to only one line. These connections have no
traffic capture, frame-loss or cable-unplug controls.

A network cloud uses no image or process and is skipped by **Start lab**.
Its appearance and connections are saved in JSON and ZIP. An Alpine PC still
needs an operational connection to a switch, router or Internet gateway to
start; a diagram line alone does not supply that connection.

For actual outbound connectivity, choose **Internet**, the separate
[NAT gateway cloud](internet.md).

## Diagram groups and notes

Add **Diagram group** from the library to draw a rectangle around a network
area, such as a site, routing domain or access network. Inspector provides a
title, plain-text notes, outline color, width and height. Put the title and
notes inside the rectangle, above it or on its right side. The rectangle sits
behind devices and cables; its empty interior remains usable for panning and
rectangle selection.

Drag its title or border to move it. **Move enclosed nodes with group** is on
by default: objects fully inside the rectangle when the drag starts move by
the same distance. Partially overlapping objects stay put. Membership is
positional, not permanent; moving a device outside the rectangle removes it
from the next group move. Nested rectangles can move with an outer rectangle.
Overlapping selections move each object only once. Turn the checkbox off for
a standalone note or to reposition the rectangle without moving devices.

Select a group to reveal its lower-right resize handle. Resizing changes only
the rectangle, never device positions. Escape cancels a move or resize. The
title and resize handle also accept arrow keys, with Shift for larger steps.
Deleting a group removes only its rectangle and notes, keeping all devices
and links. Rectangle selection selects devices/clouds; select group titles
explicitly to move several groups together with Shift/Ctrl/⌘ or Multi-select.

Groups have no interfaces, console or runtime. Their notes and settings are
preserved in JSON and ZIP. Administrators can edit and move them while devices
run; adding or deleting a group requires the lab to be stopped, like other
topology objects. Notes accept up to 2,000 characters; the map shows a compact
preview and Inspector contains the full text.

## Create your first lab

A fresh data directory starts empty. Upload suitable router and switch images,
then choose **Create a starter topology**, or add devices yourself. The built-in
starter uses installed images; the [starter JSON](../examples/starter.json) uses
explicit filenames that you may need to adjust.

1. Drag a device from the palette onto the canvas, or click its palette item.
2. Select it, choose an image and resource settings in **Inspector**, then
   **Apply settings**. See [device profiles](devices.md) for port conventions.
3. Choose **Connect**, select two nodes, then choose an unused interface at each
   end. Each physical interface can belong to only one cable.
4. Click **Start lab**, or start individual nodes in the inspector. A PC starts
   after its connected router/switch.
5. Click a running node to open its [console](consoles.md). **Running** means the
   process started, not that the guest finished booting.

Configure guest interfaces normally and save before stopping. The
[starter exercise](../examples/starter.md) walks through a router, switch and PC.
The [OSPF exercise](../examples/ospf-practice.md) provides a routing baseline.

## Editing and saving

Topology names and node positions save automatically. Nodes, images, interface
allocations and cables require the whole lab to be stopped before structural
changes. Node names and positions remain editable while running.

A stopped PC's IPv4 address/prefix and gateway can be edited even while other
nodes run. Those values are applied each time that PC starts. A PC has one `eth0`
interface; increasing its displayed resource values does not add more ports.

Internet gateway firewall rules can be applied live through the cloud's settings
window. Its address, return routes and Docker uplink subnet require only that
Internet node to be stopped. Host port forwards also require that Internet node
to be stopped before editing. See [Internet settings](internet.md#outbound-firewall-rules).

Several cables between the same pair of nodes fan out into separate curves.
To add another, select the same pair in Connect mode and choose unused ports.
While stopped, clicking a cable opens a deletion confirmation. While running,
it opens [link fault controls](link-actions.md). Device deletion also asks for
confirmation and reports its attached cables.

Use [Import/Export](backups.md) to move between labs. One topology is active per
data directory. JSON imports keep matching saved node storage; ZIP restores
replace the full node-storage tree.

## Panels, zoom and fullscreen

**Devices** and **Inspector** show/hide the side panels. Wide-screen choices are
remembered in this browser. In portrait view or at widths of 1000px or less, both
panels start closed and open one at a time as drawers. Close a drawer with its
arrow, Escape, or a tap outside it. Opening a console closes the drawer.

The map supports 20–200% zoom. Use **− / +**, click the percentage to reset to
100%, or choose **Fit** to frame the topology. Ctrl+wheel (⌘+wheel on macOS) zooms
around the pointer. Drag empty canvas space to pan; ordinary scrolling scrolls
the map. Zoom and pan do not change saved node coordinates or console text size.

Names and interface labels remain readable when zoomed out. Device-type labels
hide below 75% zoom. Label spacing adjusts around nodes and crowded labels may
use a short guide line to their cable.

**Fullscreen**, beside the zoom controls, expands the entire workspace, including
consoles and instructions. Escape or browser Back can leave fullscreen. Unsupported
browsers show a disabled button. Mobile layouts adapt to the visible viewport
when the software keyboard opens; behavior still depends on the browser.

## Workspace layouts

Use **Layout** in the topology toolbar to choose a browser-local workspace:

- **Build** keeps the device library and inspector beside the map, with the
  grouped consoles below it.
- **Side by side** keeps the topology on the left and a full-height tool pane on
  the right. Choose **Consoles**, **Agent** or **Instructions** in that pane.
- **Split workspace** keeps the topology full height on the left, with Agent or
  Instructions above the grouped consoles on the right. Instructions remains
  available when the optional Agent is disabled.
- **Free layout** returns to the existing floating-window controls. **Tile
  floating windows** arranges visible windows automatically.

Drag the dividers to adjust the split. Keyboard users can focus a divider and
use arrow keys (Shift for larger steps), or Home/End for its limits. In the
working layouts, Devices and Inspector open as temporary drawers without taking
width away from the map. Escape or the backdrop closes a drawer.

Opening another console adds or selects its tab in the console area. In Side by
side, this selects the Consoles tool; in Split workspace, the upper tool remains
visible. Individual console tabs can still float, and Agent/Instructions have a
**Float** control. Select the tool again to return it to its assigned pane.
Choosing a preset brings existing console windows back into the grouped pane.

At widths of 1000px or less, or in portrait orientation, the map and tool area
stack vertically and the tools share tabs. Returning to a wider view restores
the chosen desktop arrangement. Layout changes reuse console connections,
scrollback and locks, and preserve Agent conversation state and unsent drafts.

The preset, selected tools and split ratios are remembered in this browser.
They do not change topology positions, permissions or lab exports. **Reset
layout** restores Build and the default split sizes. Floating-window positions
remain temporary and are not remembered on reload.

## Float the topology

**Float map** moves the live topology into a movable, resizable window. **Dock**
returns it to the workspace; **Hide (−)** keeps its state. Reopen it using
**Show topology window** or **Map** in a console's Actions menu.

The title and controls share one horizontally scrolling toolbar. Move and Hide
stay fixed at the edges. Devices/Inspector buttons are hidden while floating.
Dragging the title/grip moves the window; dragging the corner resizes it. These
handles support keyboard arrows, with Shift for larger steps. Maximize fills the
visible viewport; Restore returns to its previous size.

Clicking a node respects the chosen [console layout](consoles.md). Individual
floating consoles open without also floating the shared panel. Grouped tabs
stay together. Explicit docking remains respected.

## Arrange windows

**Layout → Tile floating windows** on the map or **Arrange** in a console tiles the topology,
visible console windows and visible instructions across the viewport. A visible
docked console panel floats as a group; detach its tabs first to get one tile
per console. Hidden consoles stay hidden, and Arrange brings the topology back.

Arrangement restores maximized windows, fits the map to its tile and refits
terminals. If the viewport cannot hold the minimum window sizes, it cascades
bounded windows instead of creating unusably small tiles. Bring a covered window
forward using its tab/node or the Map/Instructions controls. Font settings,
connections and input locks survive layout changes.

Swipe horizontally or scroll the mouse wheel over any toolbar to reach controls
in a narrow window. Dropdowns retain their own vertical scrolling. Window positions
belong to this browser page and reset on reload; they are not saved in lab exports.


## Moving several nodes together

Shift-, Ctrl-, or ⌘-click nodes to add or remove them from the selection.
On a touch screen, enable **Multi-select** in the topology toolbar and tap nodes.
These selection clicks do not open consoles. Drag any selected node to move the
whole group; spacing is preserved, including at the edge of the canvas. This
works while devices are running and in the floating map.

Turn Multi-select off for ordinary console clicks. A normal node click selects
that node alone; clicking blank canvas or pressing Escape clears the selection.
In **Multi-select**, drag empty canvas space to draw a selection rectangle.
Nodes touched by the rectangle are selected; hold Shift/Ctrl/⌘ while dragging
to add to the existing selection. Escape during the drag cancels it and restores
the previous selection. Shift/Ctrl/⌘-drag also works in normal Select mode.
Without Multi-select or a modifier, blank-canvas dragging pans. The inspector edits only the last selected
node, not the group. Selection is local to your browser; positions save together.

## Map workspace

Choose **Layout → Map workspace** for a large topology. The map fills the
workspace below one compact toolbar. **Agent** (when available) and
**Instructions** open floating windows; consoles open in a floating tabbed
window, with individual tabs still detachable. Traffic monitors also float.
**Devices** and **Inspector** open overlays without reducing the map size.

The **Lab** menu keeps the topology name, save status, Start/Stop, Import/Export,
Storage and account controls accessible. The toolbar scrolls on narrow screens.
Choose another layout to restore the usual header; docking the console group
switches to Free layout. This preference is remembered in this browser and
changes no topology, terminal connections or Agent permissions.
