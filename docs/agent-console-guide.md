# Console guide for Weblab agents

Use this guide when the user asks you to follow it for console work. It explains
tool usage; it does not grant permission to configure, restart or replace a lab.

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
   typing. If `gap` is true, inspect the replacement history and read again.
4. Before **every** `send_console_command` or `send_console_input`, make a fresh `get_console_output` call
   in this conversation and use its exact `revision` and `cursor`. Do not shorten,
   invent or reuse older values. The Agent requires this read even if the previous
   send returned output and a newer cursor.
5. Prefer `send_console_command` for one ordinary command, wait for output, and read subsequent bytes with the returned
   cursor. Verify the result and return to the appropriate prompt before the next
   command. A successful send or a timeout does not establish command completion.

## Submit commands without encoding Enter

Use `send_console_command` with plain command text, for example
`command: "show clock"`. The tool appends exactly one real carriage return.
Do not add Enter escapes or line endings. It rejects empty commands, control
characters, literal `\r`/`\n`, and commands over 4095 UTF-8 bytes without typing.
Send one command per call and verify the resulting prompt before continuing.

This helper uses the same permissions, approvals, revision/cursor checks and
human locks as raw input. It does not clear pending input, enter privileged mode,
log in, handle pagers or retry automatically. Establish an empty command line
first; never append a new command to an unfinished line. Use the raw tool for
bare Enter, control keys, pager input or deliberately literal escape text.

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
- If it says **Press RETURN to get started**, send one Enter, then read again.
  If necessary, repeat once after inspecting the new output. Do not blindly send
  multiple Enters: they may submit pending commands or answer confirmations.
- With no prompt and no new output, inspect a larger recent window for a login,
  pager or startup question. An idle read alone does not prove the node is booting.
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
YOLO changes approval handling; it does not bypass these checks.

In your final answer distinguish what you read, what you executed, what the
device actually returned, and what remains unverified. Never claim a successful
configuration change solely because a tool accepted input.
