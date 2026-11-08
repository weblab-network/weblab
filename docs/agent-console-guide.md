# Console guide for Weblab agents

Use this guide when the user asks you to follow it for console work. It explains
tool usage; it does not grant permission to configure, restart or replace a lab.

## Optional operator preparation

With local models that struggle to reach the initial prompt, the user can prepare
the console before requesting a task. On Cisco IOS/IOL, obtain a live prompt,
enter `enable` when the requested configuration or inspection needs privileged
EXEC, and run `terminal length 0` for long output. Basic commands permitted in
user EXEC may not need elevation. Leave an empty EXEC prompt; do not pre-enter
configuration mode just for the Agent. With authentication enabled, the admin
Agent may borrow its own Weblab login's lock. Other human locks must be released
before handing off to the Agent.

This preparation has helped in maintainer testing, but is not guaranteed or
mandatory for every model. The Agent must still read the current console, verify
its actual mode, and use the usual permissions and cursor checks. Session expiry
can invalidate earlier preparation. See the operator checklist in
[Agent setup](agent.md#preparing-consoles-for-local-models).

## Read, inspect, send, read

1. Read `get_lab_state` at the start of each turn. Use returned internal node IDs,
   not display names such as `SW3`. Identify the vendor before choosing commands.
   In compact mode, follow `next_node_offset` and `next_link_offset` until both
   are null; hold an exhausted offset at its corresponding total. All pages must
   have the same revision. A node missing from one page is not absent from the lab.
2. Call `get_console_output`. For an initial check, omit `cursor` to get recent
   output. Inspect the **end** for the current prompt, unfinished input, pager,
   login request or confirmation. Old boot logs do not establish current status.
3. If `capture_end` is `limit`, keep reading with each returned cursor before
   typing. If an Agent result includes `unread_cursor`, resume at that cursor:
   a later excerpt skipped bytes from an earlier limited capture. Collect them
   before requesting further input. If `gap` is true, inspect the replacement
   history and read again; lost output cannot verify the previous command.
4. Before **every** `send_console_command`, `send_console_commands`, `send_console_commands_to_nodes`, `send_console_key` or `send_console_input`, make a fresh `get_console_output` call
   in this conversation and use its exact `revision` and `cursor`. Do not shorten,
   invent or reuse older values. The Agent requires this read even if the previous
   send returned output and a newer cursor.
   If the read already shows an empty prompt such as `SW4>`, send the intended
   command directly. Do not insert another Enter: every input consumes that read.
   After a missing-fresh-read rejection, read, then send the intended command;
   another Enter would recreate the same failure. If the same rejection or
   no-progress sequence repeats twice, stop that sequence and explain the blocker.
5. Prefer `send_console_command` for one ordinary command, wait for output, and read subsequent bytes with the returned
   cursor. Verify the result and return to the appropriate prompt before the next
   command. A successful send or a timeout does not establish command completion.

Console results include bounded `evidence`: the final line, a `tail_hint`, possible
pending input, and selected error lines. These describe **this capture only**.
They are heuristic excerpts, not persistent CLI state, a readiness check or a
success verdict. Cisco and Junos prompt patterns are separate; other platforms
can return `unknown`. Inspect the actual text and vendor context in every case.
An expiry, login question or pager after an older prompt supersedes that prompt.
`no_new_output` means this read supplied no text; it does not confirm that the
previous command finished. `incomplete` requires continued reading. Compact
responses may omit the duplicate hints to preserve the output/cursor budget.

Sends explicitly report `command_completion: unverified`, even when a prompt
returns. A command can be rejected and still return to the prompt. Error lines
from a latest/history read may belong to older commands; correlate them with the
send capture and subsequent cursor reads before reporting a result.

## Session paging dictionary

These commands disable paging for the current CLI session. Choose by the actual
device and prompt, not by the appearance of a `#` alone. They are also supplied
in console evidence and the integrated Agent's guidance. A dictionary entry is
not proof that the CLI is ready, or that a command succeeded.

| Platform | Command | Mode |
| --- | --- | --- |
| Cisco IOS / IOL / IOSv | `terminal length 0` | EXEC |
| FRR | `terminal length 0` | vtysh EXEC, not the container shell |
| Arista EOS | `terminal length 0` | EOS EXEC, not Bash |
| Junos | `set cli screen-length 0` | Operational CLI, not configuration or shell |
| EXOS | `disable clipaging` | EXOS CLI; `clipaging` is one word |

Inspect the response before requesting long output. A session restart can reset
paging. Do not write these into startup configuration or invent an equivalent
for LL2S/Alpine. If already at a pager, finish or deliberately cancel the current
output before typing another command.

References: [Cisco terminal settings](https://www.cisco.com/c/en/us/td/docs/ios-xml/ios/fundamentals/command/cf_command_ref.pdf),
[FRR terminal commands](https://docs.frrouting.org/en/latest/basic.html#terminal-mode-commands),
[Arista CLI](https://www.arista.com/en/um-eos/eos-command-line-interface-cli),
[Junos CLI guide](https://www.juniper.net/documentation/us/en/software/junos/cli/cli.pdf),
[EXOS disable clipaging](https://documentation.extremenetworks.com/exos_commands_16/EXOS_16_2/EXOS_Commands_All/r_disable-clipaging.shtml).

## Submit commands without encoding Enter

Use `send_console_command` with plain command text, for example
`command: "show clock"`. The tool appends exactly one real carriage return.
Do not add Enter escapes or line endings. It rejects empty commands, control
characters, literal `\r`/`\n`, and commands over 4095 UTF-8 bytes without typing.
Send one command per call and verify the resulting prompt before continuing.

For a predictable sequence, `send_console_commands` accepts a list of 1–32
plain, nonempty commands. It validates the entire list before sending, appends
one Enter per command, and limits the whole paste to 4096 UTF-8 bytes. The list
uses one fresh read, one exact approval (or enabled YOLO action), and one console
paste under the existing lock policy. It does not wait or check errors between
commands, roll back, or retry. Read all resulting output and verify the result.

For example, `["enable", "terminal length 0"]` is suitable only when `enable`
is known to be passwordless. Otherwise send `enable` separately and inspect its
response. Keep login, passwords, confirmations, pagers and commands whose results
determine the next action separate. A later line can run after an earlier error.
The human [bulk-console dialog](consoles.md#bulk-console-commands) also supports
leading blank lines; agents should obtain and inspect a prompt before batching.

This helper uses the same permissions, approvals, revision/cursor checks and
human locks as raw input. It does not clear pending input, enter privileged mode,
log in, handle pagers or retry automatically. Establish an empty command line
first; never append a new command to an unfinished line. Use `send_console_key`
for Enter and the supported control/pager keys. Raw input remains available for
other keys, partial input and deliberately literal escape text.

## Send the same commands to several nodes

Use `send_console_commands_to_nodes` for 1–8 explicitly selected devices. First
read **each** console with `get_console_output`, finish any unread capture, and
inspect its empty top-level prompt. Supply `targets` as
`[{"node_id":"sw1","expected_cursor":"<sw1 read cursor>"},
{"node_id":"sw2","expected_cursor":"<sw2 read cursor>"}]`, the common
`expected_revision`, and `commands`, for example `["terminal length 0"]` for two
Cisco switches already at privileged EXEC prompts.

Split PCs from network CLIs, and split different vendors and privilege levels.
The server checks this before sending: Cisco/FRR/EOS EXEC, Junos operational,
EXOS/LL2S EXEC and stock Alpine shell prompts are recognized independently.
Configuration submodes and unknown/custom prompts are refused. Prepare prompts,
login and privilege changes individually when their behavior is uncertain.
Never broadcast a bare Enter or assume every device has passwordless `enable`.

One browser approval covers the exact target list and commands. Every initial
prompt/cursor/lock check must pass before the first paste. A later change can
still stop the operation partway through. Inspect each node's delivery status,
read its output and drain limited captures before continuing; never replay the
whole batch. The tool has no intermediate command waits, retries or rollback,
and prompt matching cannot prove that a command succeeded.

## Press a named key

`send_console_key` accepts `key: "enter"`, `"space"`, `"tab"`, `"ctrl_c"` or
`"ctrl_u"`. It sends exactly that key, avoiding literal backslash/control notation.
Use a fresh console read's revision/cursor, the same as for commands. Permissions,
approval, human locks and capture limits are unchanged. The key is not a read-only
action: Enter can submit pending text, and Ctrl+C can interrupt a running command.

The vendor examples below describe the bytes for the raw tool. With the command
helper, pass the command itself, such as `enable` or `terminal length 0`, without
the trailing Enter escape.

## Raw input: Enter and control keys are bytes

`send_console_input` types exactly what its `input` contains. It does **not**
append Enter. For example, in JSON tool arguments:

```json
{"input": "show clock\r"}
```

This JSON escape becomes a carriage-return byte (Enter). `"show clock"` alone
leaves an unsubmitted command. `"show clock\\r"` types a literal backslash and r;
it does not press Enter. When calling from JavaScript, use `input: "show clock\r"`.
The same distinction applies with any model/provider: in JavaScript source,
`input: "show clock\\r"` produces printable backslash-and-r characters. Do not
double-escape Enter when writing a JavaScript string passed to a tool.

| Intended key | JSON value for `input` |
| --- | --- |
| Enter | `"\r"` |
| Ctrl+C | `"\u0003"` |
| Ctrl+U | `"\u0015"` |
| Tab | `"\t"` |
| Space (one pager page) | `" "` |

Literal `"^C"` is two printable characters, not Ctrl+C. Control-key behavior
depends on the guest CLI. Do not send Ctrl+D or `exit` as a generic cleanup step.

If a command is echoed but there is no result, inspect the pending line. Do not
type the whole command again: that can concatenate commands. Submit an intact
authorized command with Enter, or cancel/clear a malformed line using a key
supported by that CLI, then read the resulting prompt before retrying. Never
submit an unfamiliar line left by a human or another session.

## Obtaining a prompt after boot

- A running process is not proof of a ready CLI. Read the latest console first.
- An empty prompt already present needs no extra Enter. Send the intended command
  using that read's revision/cursor, in the mode actually shown.
- If it says **Press RETURN to get started**, use `send_console_key` with
  `key: "enter"`, then read again. Later startup logs can follow that message.
  If necessary, repeat once after inspecting the new output. Do not blindly send
  multiple Enters: they may submit pending commands or answer confirmations.
- If only idle boot/syslog text remains without a prompt, and console access is
  part of the requested work, try one named Enter and read again. First check for
  unfinished input, login/password, pager, setup/confirmation questions or a
  command still running; do not blindly send Enter in those states. Inspect more
  history if needed. Old boot logs alone do not prove the node is still booting.
- If login is requested, use only credentials provided for that device. Do not
  guess passwords. Stop at an unknown setup/confirmation prompt and report it.
- Warnings about PKI, NTP or licensing do not by themselves prove the console is
  unavailable. Establish the actual prompt and command response.

## Cisco IOS / IOSv / IOL

1. `SW3>` is user EXEC. For privileged inspection such as `show running-config`,
   send `enable\r` and verify `SW3#`. If it requests a password, obtain the
   authorized credential; do not try guessed passwords or command abbreviations
   to work around the missing privilege.
2. At the EXEC prompt, send `terminal length 0\r` as a **separate** command.
   Read its response, check for errors, and verify the prompt has returned.
3. Then send `show running-config\r`, or a focused operational command matching
   the question. For interface checks, examples include
   `show interfaces Ethernet5/0 switchport\r` and
   `show running-config interface Ethernet5/0\r` on images supporting that syntax.
   Use the interface name from the current lab; do not copy this example blindly.
4. If output still ends in `--More--`, pagination is active. Read fresh state and
   send a single space **without Enter** for each page. Alternatively, use the
   device's supported pager quit key, verify the EXEC prompt, then disable paging
   and rerun the show command. Do not type `terminal length 0` into a pager.
5. After reconnect/login, recheck paging instead of assuming an earlier terminal
   setting still applies. A rejected tool call never applied that setting.

Do not search configuration for an exercise label such as `task 3`; inspect the
interfaces, VLANs and protocol settings required by the task. Do not treat
`(config)#` as an EXEC prompt or leave a user's configuration context silently.

## Junos

- Distinguish the operating-system shell, operational CLI and configuration CLI.
  If in the shell and CLI access is intended, enter `cli\r` and verify the prompt.
- During boot, a login or CLI prompt can appear before the management and
  forwarding services are ready. Inspect `show chassis fpc | no-more` before
  configuration/traffic checks; an Online FPC is one readiness check, not proof
  of protocol convergence. If CLI reports a management connection failure and
  returns to the shell, observe that prompt before re-entering CLI. Do not replay
  configuration commands whose outcome is uncertain.
- At the operational CLI prompt, use `set cli screen-length 0\r` to disable
  pagination for the session, then inspect its response before the next command.
- `show configuration\r` inspects configuration from operational mode. Choose
  operational protocol/interface commands appropriate to the question.
- Do not enter configuration mode, commit, clean storage or power off as part of
  ordinary inspection. Shutdown requires separate authorization and the Junos
  guest shutdown procedure.

## Complete output versus a recent excerpt

Device pagination and Weblab capture limits are independent. Disabling pagination
does not remove `max_bytes` or the capture deadline.

Ollama's compact mode caps each capture at 1024 raw bytes, reported as
`capture_max_bytes`, even if you request a larger budget. Continue with the cursor
instead of repeatedly increasing `max_bytes`. OpenAI keeps the ordinary budget.
Any `chars truncated` marker from the surrounding client is another incomplete
result; do not treat missing text or missing nodes as evidence of absence.

- For a full configuration, use a reasonable capture budget (for example 65536
  bytes), then append subsequent **cursor-based** reads until the command ends
  and the CLI prompt returns. Continue after a timeout if completion is unverified.
- Do not switch to a new cursorless latest read while collecting a command:
  it may omit its beginning. `omitted_bytes > 0` explicitly marks an excerpt.
- To inspect older retained history, omit the cursor and set `latest=false`, then
  follow cursors. History is bounded; expired output may require rerunning an
  authorized read-only command from a verified prompt.
- Report incomplete output as incomplete. Never conclude that configuration is
  absent from a truncated excerpt, an unanswered command, or a topology's
  original `startup_config` snippet.

## Error recovery and ownership

`expected_cursor` must be the exact opaque `cursor` from the latest console read
for that node, never a visible CLI prompt such as `SW4#`. Missing-read, cursor
mismatch and read-revision errors mean no input was sent; they are not requests
for approval or an authorization code. Call `get_console_output` again, inspect
the output, then use its exact `cursor` and `revision` for the next input request.
Normal Settings permissions, approvals and input locks still apply.

If input is rejected for a stale cursor, no command was sent. Read from the last
cursor, drain remaining output and inspect the new prompt before sending again.
If `input_status` is `unknown`, delivery may have occurred: inspect output before
any retry. Respect human input locks and stop when another operator takes over.
`lock_owned_by_session=true` means the authenticated Agent may use its login's
lock for approved input without taking it over. It still needs a fresh read and
the ordinary console-input permission/approval. The browser cannot type during
borrowed access; its owner can explicitly take back control, which ends that
access without undoing already delivered commands. Read fresh state before retrying.
YOLO changes approval handling; it does not bypass these checks.

In your final answer distinguish what you read, what you executed, what the
device actually returned, and what remains unverified. Never claim a successful
configuration change solely because a tool accepted input.
