# Building and arranging a topology

[Help index](README.md) · [Project overview](../README.md)

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

**Arrange windows** on the map or **Arrange** in a console tiles the topology,
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
