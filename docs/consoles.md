# Consoles, shared sessions and floating windows

[Help index](README.md) · [Project overview](../README.md)

## Opening and closing consoles

Click a running node to open its console. Click it again to select the existing
tab or bring its floating window forward. Each tab keeps its own connection and
up to 10,000 lines of local scrollback. Background consoles keep receiving output.
**Reconnect** reconnects the selected console; **Clear** clears its displayed text.

**Hide (−)** keeps the connection alive. A tab's **×** closes that connection and
discards local history. Stopping a device retains its console history; restarting
reconnects automatically. Removing a device closes its console. The tab strip
supports Left/Right, Home/End and Delete when focused.

## Controls, text size and window layout

The **Actions** menu beside the font controls contains Float/Dock, Float all/
Dock all, Map, Arrange, Reconnect, Clear, Expand/Maximize and Hide. The menu supports touch, ordinary keyboard tab
navigation and Escape to close; it stays within the available viewport.

Console panes narrower than 850 pixels use compact status/lock controls, even on
a wide desktop screen. The link icon means connected; a crossed link means not
connected. An eye indicates a read-only console locked by another station. The
padlock controls input locking: open means unlocked, closed means locked, and
green indicates this station holds the lock. Full status remains available in
tooltips and screen-reader labels; wider panes display the explanatory text.

Topology and console toolbars stay on one line whether docked or floating.
Swipe horizontally or use the thin scrollbar to reach controls in narrow panes.
Move floating windows by their title or grip; swiping the rest of the toolbar
scrolls its controls. Open menus follow their button when the strip scrolls.

All four arrow keys are directly on the toolbar for one-tap cursor movement and
history navigation. They follow the same input-lock rules as other console keys.
The docked workspace also resizes above the on-screen keyboard, including in
fullscreen. Its displayed console height is limited to the available space; a
manually chosen height is restored when the keyboard closes. Chrome is asked to
resize page content via `interactive-widget=resizes-content`, with visual-viewport
sizing as a fallback. Pinch zoom does not trigger this workspace reflow. See
[Chrome viewport behavior](https://developer.chrome.com/blog/viewport-resize-behavior).

Touching Actions/Keys and selecting Arrange preserves terminal focus, allowing
the on-screen keyboard to remain open while arranging within the available
visual viewport. Mouse and keyboard menu navigation retain ordinary focus behavior.

For tablet/phone keyboards, **Tab** is directly available for completion, and
**Ctrl+C** is directly available in the toolbar to
interrupt a command such as an Alpine ping. **Keys** offers Ctrl+A/E (line start/
end), Ctrl+W (delete word), Ctrl+U, Ctrl+L, Ctrl+D, Ctrl+Z, Ctrl+Shift+6 (Cisco
interrupt), Tab, Esc and arrows. These send terminal key sequences to that console;
the device/application determines their behavior. Ctrl+D can log out and Ctrl+Z
can suspend a shell job. These are explicit shortcuts, not a sticky Ctrl/Alt
modifier. They are available on desktop too, without trying to infer whether a
virtual keyboard is present. Locked observers and disconnected/stopped sessions
cannot send these keys. Pressing a key restores terminal focus.

Use **A−** and **A+** in the console toolbar to change text size from 8 to 28 px.
Click the displayed size to reset to 12 px. This preference applies to all device
console tabs and is saved in this browser; it does not change topology labels or
another workstation's settings. Changing size refits the terminal without
reconnecting or clearing its history.

Drag the divider along the top of the docked console panel to change its height.
The divider also supports Up/Down arrows, Shift for larger steps, and Home/End
for its minimum/maximum height when keyboard-focused.

Choose **Actions → Float** to move the whole tabbed console panel into a floating window
inside the page. Drag its grip/title area to move it, or its lower-right corner
to resize it. Choose Expand/Maximize or Restore in Actions to change its size.
**Dock** puts it back below the topology. The floating panel is non-modal, so you
can still interact with the map. It uses the same tabs, terminal objects and
WebSocket connections, preserving local history and console input locks.

For **several independent windows**, use **Float tab** or the small ↗ button on
a device's tab. Each detached console has its own move/resize/maximize controls,
input lock/takeover, Clear and Reconnect buttons. Its **Dock** button returns it
to the tabbed panel. Float tab detaches one device; **Float** still moves the
whole tabbed panel, and both layouts can coexist.

Manually choosing **Float** keeps new consoles as tabs in the floating panel,
including when opened from the floating topology. **Float tab** or a tab's ↗
makes new consoles start in individual floating windows. **Dock** switches new consoles
back to the tabbed panel. Existing sessions keep their placement when reopened;
changing this preference does not rearrange them. The preference lasts until
page reload. Floating the map or using Arrange does not itself select this mode.

**Float all** detaches and shows every currently open console, including hidden
sessions, and makes new consoles float. **Dock all** brings them together in the
panel below the topology and makes new consoles open there, even while the map
floats. These actions preserve connections, history and input locks; they do not
open consoles for other nodes or change the topology window. Use Arrange afterward
to tile the floating windows if desired.

Detached tabs stay in the strip as window shortcuts (▣); selecting a tab or its
topology node brings that window forward, reopening it if hidden. Clicking or
focusing a window brings it to the front. Background consoles keep receiving
output, and input goes only to the terminal you focus. The Hide action preserves
the connection; the tab's × closes that session and its window. Font size remains
a shared preference across all console windows. If every console is detached,
the docked panel reduces to controls and the tab strip.

The move and resize handles support arrow keys (Shift for larger steps), as well
as mouse and touch dragging. Floating bounds adapt to screen/viewport changes.
The Hide action keeps sessions and the selected layout alive; click a node to
show the panel again. Reload starts docked; window placement and local terminal history are not restored.
Font and clipboard preferences remain saved in this browser. No separate browser window opens.

## Clipboard

**Actions → Clipboard** enables optional **Copy on selection / right-click
paste** for terminal text areas, in docked tabs and floating windows. Copy on
selection also applies to Markdown and text in **Instructions**; its toolbar has
the same **Clipboard** preferences button. The topology and other page areas are
excluded. Selections spanning Instructions and other areas are also excluded.
Right-click in Instructions keeps the browser menu; paste targets terminals.
The preference starts
off. Choose **System clipboard** on a secure connection (normally HTTPS; browser
permissions still apply), or **Workspace clipboard** to copy between terminals
in this page without changing the OS clipboard. Plain LAN HTTP uses workspace
mode. Select a command in Instructions and right-click in a terminal to paste it.
The menu shows the active mode. A failed system clipboard request reports
the failure; it does not silently paste an older workspace value.

Selection copies text; right-click pastes into the clicked terminal using its
normal newline/bracketed-paste handling. Shift+right-click, native touch menus,
and keyboard copy/paste retain their normal behavior. Input locks still apply,
including when a lock changes during a clipboard permission prompt. Clipboard
preferences persist in this browser; workspace text stays only in page memory
and is cleared by reload. External application text remains available through
normal browser Paste/keyboard shortcuts on HTTP. Automatic clipboard operations
are limited to 1 MB of UTF-8 text each.

## Sharing, input locks and reconnecting

The wrapper keeps the latest 256 KiB of console output per running device, even when nobody is connected. A new station receives that recent output, then the live stream. Reconnecting an existing workspace uses a stream cursor to receive only missed bytes, preserving its local scrollback without replay duplicates. If the device restarted or the missed output exceeds the retained history, a message marks the gap before replaying the available output. Reloading the page creates a new terminal from the retained output; it does not transfer another browser's complete 10,000-line scrollback, selection or scroll position. Output may wrap differently on screens of different widths.

**Lock input** follows the active console tab. Check it to let only this browser connection type; every other station keeps receiving output and shows **Read-only · locked elsewhere**. A lock symbol on the tab also shows that input is locked. Uncheck it from the owning station to restore shared input.

An observing station can choose **Take over**, then confirm. The previous owner immediately becomes read-only and receives a notice. Cancel or Escape leaves ownership unchanged. If ownership changes while the confirmation is open, the request is rejected and the station must try again against the current lock. Each device has its own lock; changing tabs or hiding the pane keeps it held. Closing the console tab, disconnecting/reconnecting, closing the page, or stopping the device releases it. A lost connection releases it once detected (heartbeat timeout is about 90 seconds). Reconnecting does not automatically reclaim a lock.

This is coordination for a trusted lab, not authentication: any connected station can confirm a takeover. The wrapper enforces the lock for both current and legacy clients, so bypassing the disabled terminal input does not bypass ownership. Acquiring/taking over a lock discards input still waiting in the wrapper's PTY queue; commands already delivered to the device are not undone. All stations still share one device CLI session.

Each wrapper accepts up to 32 connections, with bounded output queues and heartbeat cleanup. Slow or unreachable stations are disconnected individually and can reconnect; other stations and the PTY continue running. Input is queued to preserve partial PTY writes. This applies to IOL, IOSv and Alpine consoles because they use the same wrapper. Raw WebSocket clients remain compatible; clients negotiating `netlab.console.v1` or `netlab.console.v2` use cursor-based resume. The workspace uses v2: binary frames carry keyboard input and text frames carry lock controls/state. Legacy clients remain viewable but cannot write while another connection holds a lock. Sharing does not create separate CLI logins or shells.

See [topology and arrangement](topology.md#arrange-windows) for tiling the map,
consoles and instructions together, and [troubleshooting](troubleshooting.md)
for connection or keyboard problems.
