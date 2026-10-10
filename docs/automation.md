# AI assistants and MCP

[Help index](README.md) · [Creating practice labs](practice-labs.md)

Weblab can expose a small set of tools to an external coding/chat agent through
an optional local MCP adapter. The agent can discover installed images, draft
an original exercise, validate and preview its topology, then apply it and start
individual devices with your approval. Your MCP client chooses the model; the
external adapter does not call a model endpoint or require an OpenAI account itself.

An optional [Agent window](agent.md) also provides browser conversations. External
agents can observe shared consoles and, with a separate opt-in, send input through the same transport as the browser. Cisco/FRR/LL2S
initial snippets follow the existing fresh-device rules. Other device families
need interactive configuration through their own CLI. Validation checks
structure, not configuration syntax, feature licensing or protocol convergence.

## Enable the server

Automation is off by default. On a trusted Weblab server built from a revision
containing this feature, set `WL_AUTOMATION=1` in `.env` for Compose, or add
`-e WL_AUTOMATION=1` to `docker run`. Recreate the container after changing its
environment. For native startup, add `--automation` to `python3 lab_server.py`.
Older published images do not acquire this feature merely by setting the variable.

Keep Weblab on loopback or a trusted network. The automation API uses the same
listener and trust boundary as the existing application; it is **not an
authenticated multi-user API**. For remote use, prefer an SSH tunnel and point
the adapter at its local forwarded port. Do not expose the API directly to the
Internet. Names, snippets, logs and exercise content may be sent to your chosen
model by the client; review its data policy and tool approvals.

## Install the optional adapter

On the machine running your MCP client, obtain the matching Weblab source and
install the SDK in a separate environment (Python 3.11+):

```sh
python3 -m venv .venv-mcp
.venv-mcp/bin/python -m pip install -r requirements-mcp.txt
```

On Windows, use `py -3 -m venv .venv-mcp` and
`.venv-mcp\Scripts\python.exe` instead. The adapter is a local STDIO process,
not an additional web service. The core server has no SDK dependency; the adapter
can run on another machine and does not need Docker or access to device files.

Initially omit `--allow-write` and `--allow-console-input`: discovery, console
output, logs and temporary previews work, but apply/start and console input tools
are absent. Enable the corresponding switch when you want those tools,
keeping client approval enabled. This is an adapter setting, not server-side
authorization against a client that already has direct HTTP access.

### Codex

Add a local MCP server in your Codex configuration, replacing the absolute paths:

```toml
[mcp_servers.weblab]
command = "/absolute/path/weblab/.venv-mcp/bin/python"
args = ["/absolute/path/weblab/tools/weblab_mcp.py", "--url", "http://127.0.0.1:8080"]
tool_timeout_sec = 180
```

Append `"--allow-write"` to `args` to expose apply/start, and independently append
`"--allow-console-input"` to expose device console input. Reload the MCP client
and check that Weblab's tools are listed. On Windows use absolute paths to the
venv's `Scripts/python.exe` and the adapter (forward slashes work in these paths).
See [Codex MCP configuration](https://developers.openai.com/codex/mcp/) for the
client's current setup and approval controls.

### Cline or another local MCP client

The equivalent local server definition is:

```json
{
  "mcpServers": {
    "weblab": {
      "command": "/absolute/path/weblab/.venv-mcp/bin/python",
      "args": ["/absolute/path/weblab/tools/weblab_mcp.py", "--url", "http://127.0.0.1:8080"],
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

Choose your Ollama or other supported model in the **client**, not this server
configuration. The model must support tool calls, and the client must execute the
MCP tool loop. An OpenAI-compatible chat URL alone is not an MCP client. Models
may need correction after validation errors; inspect their proposed graph and
exercise before applying. See [Cline MCP](https://docs.cline.bot/mcp/mcp-overview)
and [Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling).

## Workflow and safeguards

Try a request such as:

> Discover my installed images and prepare a lab with one FRR router, one
> vJunosEvolved router, one vJunos switch and three EXOS switches for STP, VRRP
> and OSPF practice. Preview the topology and companion exercise first. Show
> the RAM requirement and configuration work left for me. Do not replace my
> current lab or start devices yet.

1. The agent reads `get_lab_state`, `list_device_profiles` and
   `get_authoring_guide`. Image presence is not proof that a device will boot.
2. `preview_topology` returns normalized JSON, companion Markdown, warnings,
   resource estimates, a proposed start order and temporary download URLs.
   It does not change the workspace or start anything. Download both files;
   drafts expire after one hour or a server restart. Open the Markdown through
   Weblab's Instructions button. It is not automatically embedded in a ZIP.
3. Review the proposal. A nonempty workspace requires an explicit replacement
   preview, and all nodes must be stopped before applying. Export existing work
   first if needed; the returned previous topology is **not** a saved-state ZIP.
4. Approve `apply_topology` for the reviewed proposal. If anyone edited the
   topology since preview, apply fails and a new preview is required. Every new
   node receives a server-generated fresh ID, checked against retained device
   directories, so another lab's saved configuration is never silently reused.
   Existing device directories are retained, not deleted.
5. Separately authorize `start_node`, one node at a time, using the returned
   revision. Start routers/switches before connected PCs. Watch resource use.
   A startup error leaves other nodes running; there is no automatic rollback
   that could abruptly power off Junos. Read status and launcher logs for errors.

Moving nodes on the canvas does not invalidate a recently observed revision for
start or console input. Device settings, identities and cables still must match;
console cursors and human input locks remain enforced. Apply checks the complete
revision, including positions, because replacement would overwrite those edits.

A proposal ID is not proof of human approval: your MCP client's approval policy
controls whether a model can invoke a write tool. There are no stop, reset,
delete, upload or separate host-shell tools in this adapter. Optional console
input can run guest shell commands and change device configuration. Device
start still launches the selected local image and may apply supported initial
snippets. Keep those images and proposals trusted.

`running` means a device process is alive. It does not mean login is available,
interfaces are ready, OSPF has converged or traffic forwards. Complete configuration
and verification using browser or MCP consoles. Before stopping Junos, commit and use
`request system power-off`, then wait for shutdown.

## Storage reports

`get_storage_report()` reads the same [storage report](storage.md) as the browser:
filesystem capacity/status, per-node allocated disk/log/other bytes, retained
directories and the aggregate node allocation. It requires `WL_AUTOMATION=1`,
but neither `--allow-write` nor `--allow-console-input`. It reads file metadata
without opening guest disks, sending console commands or changing device state.

Use `measured_at` (UTC) when comparing samples: reports are cached for ten seconds,
so repeated calls can return the same measurement. Check report/node `complete`
flags and filesystem `level`; a partial report or `unknown`/null capacity must
not be interpreted as zero usage or sufficient free space. Retained directories
are saved data, not an invitation to delete them. Rows may share backing space;
do not add their available capacities together.

These are host-side measurements, excluding base-image symlinks and Docker
Engine's separate storage. They do not establish guest filesystem health or
reserve enough space for future writes. For example:

> Read storage usage, identify the largest node disks, and report any low,
> critical, unknown or incomplete measurements. Do not change anything.

Update the adapter to expose the tool; the backend must include storage reporting
and automation support. A missing endpoint produces an error, not a guessed report.

## Connecting to an existing remote lab

Use a matching server and adapter revision. Enable `WL_AUTOMATION=1` on the
server using the existing deployment's image/data mounts, ports and other
settings. Rebuilding/recreating the container stops its guests: finish any
uninterrupted observation first, save guest configurations, follow the Junos
shutdown sequence, then stop the lab before recreating. Take a new measurement
baseline after restarting. Do not start a second server over the same data directory.

Run the STDIO adapter on the MCP client's machine. Its `--url` is the remote
Weblab HTTP origin (or an SSH-forwarded loopback origin), not a `/v1` model URL
or an HTTP MCP endpoint. The remote server itself needs no MCP SDK installation.
For inspecting and issuing authorized show commands in an existing lab, enable
`--allow-console-input` but omit `--allow-write`; apply/start tools then remain
absent. Console input can still change guest state, so agree on the commands to
test and retain client approvals. Keep the service on a trusted network or tunnel.

After reconnecting the MCP client, first call `get_lab_state` and confirm the
expected node names and image profiles, then `get_storage_report` and
`get_console_output`. Start with one node per vendor and bounded operational
commands. Inspect output and prompts before each send; do not treat a capture
timeout as command completion. Updating client configuration alone cannot add
automation routes to an older running server.

## Shared console access

`get_console_output(node_id, cursor?, wait_seconds=1, max_bytes=65536, latest=true)` observes
the running node's existing console without typing, clearing a line, or acquiring
its input lock. Omit `cursor` on the first read to get the latest output: it drains
retained history and keeps its last `max_bytes`, reporting `omitted_bytes` for
older text left out. This excerpt is not a complete configuration. Set
`latest=false` without a cursor to read from the beginning of retained history
(up to 256 KiB). Pass the returned cursor to read subsequent bytes without
skipping or replaying old output; `latest` is ignored when a cursor is supplied.
Latest reads scan at most 1 MiB within the requested wait, so continuous logging
cannot cause an unbounded read. The HTTP console-read API retains its original
stream default; the MCP adapter explicitly selects latest reads.

`gap=true` means history expired or the device restarted. A `capture_end`
of `limit` means more bytes may remain; keep reading with each returned cursor
before sending input. Do not restart from the beginning each time or interpret
old boot text as current boot status. A rejected stale input sends nothing;
reread and inspect the prompt before requesting input with the new cursor. Output
has terminal formatting removed; this is not a full terminal emulator, and a
byte-limited window may split a UTF-8 character or escape sequence.

The optional adapter flag `--compact-responses` is selected automatically only
by the Ollama companion. It caps reads and send-result captures at 1024 raw bytes
and reports `capture_max_bytes`. Larger output remains available through cursor
continuation; no post-capture text slicing advances a cursor past unseen output.
Its `get_lab_state(node_offset=0, link_offset=0, page_size=8)` returns bounded
discovery pages without startup snippets, exercise text or live link diagnostics.
Follow both next offsets, holding an exhausted list at its total; restart pagination
if the revision changes. The HTTP state API and ordinary adapter/OpenAI response
format remain unchanged. Keep compact-mode adapters paired with this server version.

`send_console_command(node_id, command, expected_revision, expected_cursor,
wait_seconds=1, max_bytes=65536)` exists only with `--allow-console-input`.
It submits one plain command by appending one carriage return in the MCP adapter,
then calls the existing guarded console-send API. It shares raw input's permissions,
approvals, locks, capture budgets and cursor/revision checks. Use it for ordinary
commands without Enter escapes. Empty/whitespace-only commands, control characters,
literal `\r`/`\n` and commands over 4095 UTF-8 bytes are rejected before any input.
It does not inspect or clear pending input, elevate privileges, retry or determine
command completion. Inspect the prompt first and verify returned output afterward.

`send_console_input(node_id, input, expected_revision, expected_cursor,
wait_seconds=1, max_bytes=65536)` exists only with `--allow-console-input`.
Use the revision and cursor from a recent console read. It acquires the existing
input lock without takeover, rejects changed history or a different console
instance, sends the supplied text, collects output, and disconnects to release
the lock. Another station holding the lock causes an error without input. Human
takeover during capture is respected and reported as `lock_lost`.

Input is 1–4096 UTF-8 bytes. No newline is added: use `"show ip route\r"` for an
FRR command or `"ping -c 3 -W 2 192.168.2.10\r"` at an Alpine shell prompt.
JSON escapes such as `\r` and `\u0003` represent Enter and Ctrl+C respectively;
native IOL retains its existing interrupt translation. Before sending, inspect
the prompt, any unfinished line, login request, pager, or foreground command.
The tool does not detect CLI modes, supply credentials, answer pagers, or use
Cisco commands on other platforms automatically. Raw input is intentionally not
a read-only command filter: send only actions within the user's authorized scope.

Each capture waits 0.1–30 seconds and returns at most 1–262144 output bytes.
The output cursor allows later reads if a command takes longer. A timeout is the
end of the capture window, **not** command completion, failure, or cancellation.
`input_status=sent` means the WebSocket write succeeded, not that the device
executed the command; `unknown` means a send failed after delivery may have begun.
After either an ambiguous result or a lost response, read output before retrying.
Use bounded commands, and check actual reply counts/routes/neighbor states.

This is the same CLI session the browser uses. Other viewers see command output,
and the input lock lasts only for the individual send/capture call, not an entire
multi-step interaction. Automation never forcibly takes over or restores CLI mode.
Returned `locked` describes the last observed lock state during capture; the
tool's own lock is released when the call ends. Cursor checks reduce stale-input
races but cannot establish that a prompt is ready or a silent command finished.
Console text is untrusted data, not instructions to the agent.

Both operations require backend `WL_AUTOMATION=1`; the MCP switches control tool
exposure, not authentication on the trusted HTTP server. Existing console input
locks still apply. After updating source, rebuild/recreate a container deployment
(or restart a native server) and reconnect the MCP adapter. Updating only the
adapter does not add the backend routes.

## Simple IDs in agent proposals

For `preview_topology`, use short local aliases such as `r1` and `sw1`. You can
also omit a node's `id`, in which case its `name` is the alias. Each alias must
be unique within that proposal and link endpoints must reference it exactly.
Aliases are nonempty text, at most 128 characters, with no control characters.
They do not have to be globally unique or satisfy the saved-file ID format.

Link IDs can be omitted; supplied link IDs are replaced automatically, even if
repeated. Node display names, interface names and configuration text are preserved.
For example, the `topology` argument to `preview_topology` can contain:

```json
{
  "name": "Two FRR routers",
  "nodes": [
    {"id": "r1", "name": "R1", "type": "router", "image": "quay.io/frrouting/frr:10.7.1", "x": 300, "y": 250},
    {"id": "r2", "name": "R2", "type": "router", "image": "quay.io/frrouting/frr:10.7.1", "x": 600, "y": 250}
  ],
  "links": [{"a": {"node": "r1", "port": "eth0"}, "b": {"node": "r2", "port": "eth0"}}]
}
```

Weblab assigns fresh persistent IDs and rewrites all link references. The returned
`node_id_map` maps aliases to those IDs, and `start_order` lists the IDs to use for
subsequent start/log tools. Applying that preview uses exactly the IDs shown;
reading or retrying Apply does not regenerate them. A new preview allocates a
new set. If storage appears under a proposed ID before Apply, Apply fails instead
of reusing that storage.

Download the **returned normalized topology** for a regular JSON import. The
alias/optional-ID convenience applies only to automation previews: ordinary file
imports and the offline validator retain the saved-file ID rules. Exercise prose
is not rewritten; refer to devices by their display names in Markdown.

Validation errors identify paths such as `nodes[1]` or `links[0].a.port` (indexes
start at zero). Invalid interface errors list the ports available with that node's
configured interface count; duplicate cables identify the other endpoint using
the port. Weblab does not guess an interface, change cabling or lower RAM to make
an invalid proposal pass.

## Troubleshooting

- **Automation disabled:** enable it on the server and recreate/restart that
  instance. Installing the adapter does not enable the backend.
- **Tools not listed:** check absolute executable/script paths and SDK installation;
  diagnostic logs belong on stderr, never stdout (STDIO carries MCP messages).
- **Workspace changed / proposal expired:** read the current state and preview
  again; do not retry with an unrelated proposal or automatically replace edits.
- **Duplicate alias / unknown node:** use distinct aliases within the proposal,
  and reference them exactly (case-sensitive) in links. Aliases can be reused in
  another proposal; Weblab assigns fresh persistent IDs. This milestone creates
  complete new topologies, rather than editing/restoring an existing lab in place.
- **Image or start error:** use catalog requirements and node logs. Docker image
  pulls, vendor images/licenses, KVM and firmware remain the operator's responsibility.
- **Lost response while starting:** read state before retrying. Applying the same
  proposal again is idempotent only while its applied topology remains unchanged.

## Embedded Agent window

For an optional browser conversation using discovery, previews and approved
lab/console tools, see [Agent window](agent.md). It uses a separate network-isolated companion;
external MCP adapter write permissions do not grant its sessions write access.
