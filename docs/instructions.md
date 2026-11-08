# Reading and publishing lab instructions

[Help index](README.md) · [Project overview](../README.md)

Click **Instructions** beside **Start lab** to open the reading window inside the
workspace, including fullscreen. Admin (or any station with authentication off)
can use **Open file** to preview a UTF-8 `.md`, `.markdown` or `.txt` exercise file
(up to 512 KiB). Writers/readers view the server-published document.
**Open file** replaces the local preview;
**Hide** keeps it available. Reopen it with the main Instructions button, the
floating topology toolbar, or a console's **Actions → Instructions** shortcut.

In a [workspace preset](topology.md#workspace-layouts), the **Instructions** tab
opens the docked reader. If no file is loaded, it displays a short message;
choose **Open file** to select an exercise. Opening an empty pane does not fetch
instructions from the topology or Agent conversation. It does fetch the exercise
explicitly published on this Weblab server, when one is available.

## Publish an exercise to all stations

An administrator chooses **Open file** to read a local preview, then **Publish**
to share it. Opening a file alone does not change anyone else's instructions.
The toolbar distinguishes **Local preview · not shared** from **Published · shared
with all stations**. **View published** discards the preview and returns to the
server's document. **Unpublish** removes the shared document after confirmation.

With authentication enabled, only admin can preview/publish/unpublish. Writers
and readers automatically receive the published exercise, including after refresh
or opening a new browser. Connected workspaces check for updates about every
2.5 seconds, even when the pane is hidden. Publishing does not force their pane
open. Routine checks preserve scroll position; a changed document starts at its
beginning. Text size and window arrangement remain each browser's preferences.

Concurrent admin edits are protected: a preview retains the server revision it
was opened against. If another admin publishes, your preview stays visible and
is marked as outdated; publishing it is rejected. Use **View published** to
review the new version, then reopen your file if you still want to replace it.
If the server becomes unavailable, the reader labels its last displayed document
accordingly. It does not claim that the document is current.

Without authentication, Weblab retains its trusted-workspace behavior: any
station may publish. Local-only reading still works without publishing.

## Resetting a lab

Both **Reset without export** and **Reset lab after export** offer an unchecked
**Preserve Instructions** checkbox in the final confirmation. A successful reset
clears the shared document and local previews by default, including popped-out
readers on other connected stations. Offline tabs clear their old preview on
their next successful sync. Check the box to retain Instructions for the next lab.
Cancelling or failing the reset keeps the document. Your original files are
never deleted; JSON/ZIP exports do not include Instructions. Ordinary topology
and saved-lab imports still leave Instructions unchanged.

## Storage

Published instructions are stored atomically in `<lab-data>/instructions.json`
and survive server/container restarts when the data directory is retained. They
are separate workspace content: topology JSON and saved ZIP exports **do not
include them**, and importing either format **does not replace or clear them**.
After importing a different exercise, publish its companion document explicitly.
Keep the original file for backup/transfer; do not edit the server file while
Weblab is running. There is no version-history browser in this first release.

## Reading and window controls

Drag its handle/title or resize from its corner; those handles also support arrow
keys. Use **A− / A+** for 12–24px text, the size button to reset to 15px, and the
maximize button to fill the visible screen. **Arrange windows** includes visible
instructions alongside the topology and consoles. Narrow toolbars scroll sideways.
In **Split workspace** and **Side by side**, Instructions controls occupy their
own row below the workspace tabs. Use the mouse wheel over the toolbar to reach
controls that do not fit the pane's width.

Use **Pop out instructions** (overlapping windows, beside the fullscreen control) to move the
reader into a separate browser window. The workspace pane hides once the reader
loads. The document, text size and scroll position carry over; published updates
continue to arrive through the workspace. **Return to workspace**, or closing the
reader, restores the pane at your reading position. If the browser blocks the
popup, the original pane stays available.

The detached reader includes text-size controls and **Fullscreen** (⛶). The same
fullscreen control is available in the workspace's Instructions toolbar, even
when docked; press **Escape** to leave fullscreen. The browser decides whether
the pop-out opens as a window or tab and how much browser chrome remains visible.
A normal popup cannot guarantee a hidden address bar.

Keep the original workspace open. Reloading it, closing it or logging out ends
the detached reader. Return to the workspace to open another file or publish;
the pop-out is a reader, not another publishing station. It keeps no separate
stored document copy. Standard browser selection/copy works there; the Workspace
clipboard controls remain in the main workspace.

Markdown renders headings, emphasis, lists, read-only task checkboxes, tables,
quotes, and fenced configuration/code blocks. Text files preserve their contents
and indentation. Heading links scroll within the document; HTTP(S)/email links
open separately. Raw HTML is displayed as text, images appear as text placeholders,
and relative file links require opening the referenced file yourself. Mermaid and
other Markdown extensions are shown as code, not rendered diagrams.

Local previews are uploaded only when **Publish** is clicked. Without
authentication, an unpublished local file is remembered in this tab's session
storage across refreshes when storage is available. Authenticated tabs do not
retain private preview content across reloads/account changes; reopen the file
if needed. Published documents are fetched from the server. Text size and
open/hidden state are remembered locally in either mode.

Rendering uses the locally bundled [markdown-it](https://github.com/markdown-it/markdown-it)
15.0.2 with HTML and automatic linkification disabled. Its MIT license ships in
`web/vendor/markdown-it-LICENSE`; `fetch_assets.py` restores the pinned assets.
No browser extension, CDN connection or embedded browser is needed at runtime.

## Copy commands into consoles

Open **Clipboard** in the Instructions toolbar and enable copy-on-selection.
Select a command, then right-click inside the desired terminal to paste it.
Use Workspace clipboard on plain HTTP, or System clipboard where the browser
allows it. Right-click inside Instructions keeps the normal browser menu.
See [clipboard behavior](consoles.md#clipboard).

## Multi-file documents

The built-in viewer displays one document at a time. Relative Markdown links do
not load other local files automatically; use **Open file** to select the next
chapter. This help directory is designed for GitHub or a Markdown reader that
supports relative links. You can also open any individual help page in Instructions.
