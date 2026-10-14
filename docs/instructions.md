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
