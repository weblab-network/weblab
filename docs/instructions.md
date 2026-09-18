# Reading lab instructions

[Help index](README.md) · [Project overview](../README.md)

Click **Instructions** beside **Start lab** and select a UTF-8 `.md`, `.markdown`
or `.txt` exercise file (up to 512 KiB). It opens in a floating reading window
inside the workspace, including fullscreen. **Open file** replaces the document;
**Hide** keeps it available. Reopen it with the main Instructions button, the
floating topology toolbar, or a console's **Actions → Instructions** shortcut.

Drag its handle/title or resize from its corner; those handles also support arrow
keys. Use **A− / A+** for 12–24px text, the size button to reset to 15px, and the
maximize button to fill the visible screen. **Arrange windows** includes visible
instructions alongside the topology and consoles. Narrow toolbars scroll sideways.

Markdown renders headings, emphasis, lists, read-only task checkboxes, tables,
quotes, and fenced configuration/code blocks. Text files preserve their contents
and indentation. Heading links scroll within the document; HTTP(S)/email links
open separately. Raw HTML is displayed as text, images appear as text placeholders,
and relative file links require opening the referenced file yourself. Mermaid and
other Markdown extensions are shown as code, not rendered diagrams.

The file is read locally, never uploaded or included in topology JSON/saved ZIP.
Its content, text size and open/hidden state are remembered in this browser tab's
session storage across refreshes when storage is available. It is independent of
the selected lab and is not synchronized to other workstations. Closing the tab
normally ends that session (browser session restore may retain it).

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

The built-in viewer opens one local file at a time. Relative Markdown links do
not load other local files automatically; use **Open file** to select the next
chapter. This help directory is designed for GitHub or a Markdown reader that
supports relative links. You can also open any individual help page in Instructions.
