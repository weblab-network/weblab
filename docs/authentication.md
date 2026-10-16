# Authentication and roles

[Help index](README.md) · [Installation](installation.md)

Available starting with [v0.7.0](releases/v0.7.0.md). Earlier images have no Weblab login.

Authentication is optional and off by default (`WL_AUTH=0`). Enable it for a
shared, trusted-group workspace. All accounts see the **same lab and console
output**, including commands and output from other stations. This is not tenant
isolation or a sandbox for untrusted device images or console operators.

## Account permissions

| Account | Role | Permissions |
| --- | --- | --- |
| `admin` | Administrator | Edit topology, upload images, import/export, start/stop devices, operate link faults, capture link traffic, type in consoles, publish instructions, view activity records and use the optional Agent |
| `user` | Writer | View the lab, type in consoles, take console input locks, start/stop nodes or the lab, operate link faults, capture link traffic |
| `guest` | Reader | View topology, console output, launcher logs and storage; arrange local windows, pan and zoom |

Writers cannot change node settings, names, placement or cables, upload images,
import/export labs, or replace instructions. Readers cannot type in consoles,
take input locks, start/stop nodes or manipulate links. Permissions are enforced
on the HTTP API and console transport, not just by disabling buttons.

Traffic captures and PCAP downloads are available to admin and writer. Each
capture belongs to the exact login session that started it, and logout discards
its buffers. Reader capture access is not enabled. See [traffic monitoring](link-actions.md#monitor-traffic).

## Console ownership and approval

An administrator can **Take over** a writer-held input lock immediately, without
a confirmation dialog. The writer becomes read-only and continues seeing output.
For a console held by another admin station, the usual takeover confirmation
remains. Writer-to-writer takeover also retains that confirmation.

A writer sees **Request control** when an admin holds the console. The request
opens an **Allow console control?** dialog at the owning admin station, including
when its console is hidden or in the background. **Allow control** transfers the
lock to that exact requesting connection; **Deny** or Escape leaves it with admin.
Another admin station cannot approve on the owner's behalf. The status tooltip
identifies the owner's role and console connection (station); these are shared
accounts, not individual operator identities.

While waiting, the writer remains read-only and can **Cancel request**. There is
one pending request per console, expiring after 60 seconds. A second requester
gets a busy message. Disconnect, logout/session expiry, device stop, or ownership
change cancels the affected request. Reconnecting never inherits a request or a
lock. If another dialog is open, the approval dialog waits for it to close; the
server expiry still applies. Requests for different consoles are shown in turn.

Readers cannot request or hold locks. Approval does not change account roles or
grant any other permissions. The server enforces this policy even for modified
clients; previously sent device commands cannot be undone by changing ownership.
With authentication disabled, existing shared-console takeover behavior remains.

## Optional administrator Agent

With `WL_AUTH=1`, the optional **Agent is available only to admin**. Include the
Agent Compose override as described in [Agent setup](agent.md). Without that
override the Agent stays absent. Writer/reader Agent access and external MCP
remain disabled; there is no delegated writer Agent setting yet.

Admin can use the Agent to prepare exercises, configure devices and verify lab
tasks while other stations use the shared lab. Lab changes and console input
still require the corresponding Agent settings and approvals described below.

Each Agent session belongs to the exact Weblab login that created it, including
its history, attachments, settings, approvals and OpenAI sign-in. Another login
as the shared `admin` account cannot reuse it. Refreshes and server restarts keep
it accessible while both the Weblab login and Agent session remain valid. Browser
remembering does not extend the Weblab login's lifetime.

**Weblab logout, login replacement or expiry ends that login's Agent sessions.**
Further tool/provider requests are denied, pending approvals are cancelled and
the companion is stopped and its session files deleted, including history,
attachments and OpenAI sign-in storage. Export conversations you want to retain
before logging out. Cleanup retries if the companion is unavailable; access
remains revoked meanwhile. Password changes revoke the affected account's logins
on restart and trigger the same cleanup. Already accepted device operations may
finish and cannot be undone by logout. Other admin logins remain independent.

Turning authentication on does not adopt earlier anonymous Agent sessions;
turning it off does not expose login-bound sessions. Such incompatible sessions
are revoked and cleaned up. `WL_AUTH=0` otherwise retains existing Agent behavior.
The usual separate lab-change/console-input settings, exact approvals, YOLO
scope, revision/cursor checks and human console locks still apply to admin.
Agent input does not inherit admin's ability to take over a human-held lock.
It can borrow a lock held by **the same Weblab login**, while the browser keeps
ownership. Another browser logged in separately as `admin` has a different login
and its locks remain protected. While the Agent borrows it, your console input is
temporarily disabled. **Take back control** ends borrowed access and restores
typing while keeping your lock. Commands already delivered are not undone.
Unlocking, disconnecting or handing off the lock also ends borrowed access; the
Agent must read fresh console state before retrying. Typing resumes automatically
when the Agent's console operation ends. This does not enable writer Agent access.

After upgrading this console transport, restart affected nodes when convenient
so their wrappers pick up the change. Older running wrappers continue to refuse
Agent input through a human lock.

External MCP still needs a separate authenticated adapter. Do not enable another
unauthenticated listener pointing at the same lab to work around this boundary.

## Instructions and remaining limits

Administrators can preview and [publish instructions](instructions.md) for every
station. Writers/readers receive the shared document but cannot replace it.
Restricted reader commands, delegated Agent access, external MCP credentials, OTP and
individual account management are future stages. There is no `show`/`ping`
reader command allowlist yet: reader consoles are entirely view-only.

Export permissions control Weblab's export operations and UI. Anyone allowed to
view topology or console output can still copy that visible information.

## Development status and roadmap

**AAA (authentication, authorization and accounting) is under active development
and is being delivered in stages.** The v0.7.0 scope is three fixed shared
accounts, role enforcement, console ownership, shared published instructions,
optional admin Agent access and basic activity records. Multiple browsers can
work together today, but the shared `user` account does not identify individual
participants.

Planned later stages, not included in this release:

- Multiple named users, including distinct writers for team exercises.
- A live administrator control panel for users, sessions and access management.
- Per-user node assignments and corresponding instructions, allowing team
  members to work on different devices in the same exercise.
- Administrator-controlled writer Agent access within each user's permissions.
- Optional OTP, restricted reader commands and more detailed activity attribution.
- Remote test workflows: single-use invitations, timed attempts, submission for
  review, and admin controls to make a participant view-only or end their session.

These are roadmap directions; delivery order and release dates are not committed.
Current activity records do not include human console command history.

## Setup

Generate three distinct random passwords **locally**, not while building an image:

```sh
python3 web_auth.py generate
```

Copy the generated `WL_AUTH=1` and `WL_AUTH_ADMIN_PASSWORD`,
`WL_AUTH_USER_PASSWORD`, `WL_AUTH_GUEST_PASSWORD` assignments into your private
`.env`, replacing existing assignments. Passwords must be different:

- `admin`: 16–256 characters.
- `user` and `guest`: 6–256 characters, including at least one ASCII letter
  (`A–Z` or `a–z`) and one digit (`0–9`). Punctuation is allowed too.

Existing saved passwords remain valid unchanged; new or changed user/guest
passwords follow the letter/digit rule. The generator still produces long random
passwords. Its output is sensitive; do not include it in logs, issues or source
control. Protect `.env` with `chmod 600 .env`.

Both source and prebuilt Compose definitions pass these variables to the server.
Recreate the lab container using your existing Compose files and mounts; build
updated source when necessary. Shut down Junos gracefully before any container
recreation. Include the optional Agent override only when admin needs Agent access.
Use a build containing this feature; older released images do not implement it.

Native starts read environment variables, not `.env` automatically. Export the
same variables into the server process environment before starting it. Missing
or invalid credentials with `WL_AUTH=1` cause startup to fail; there is no default
password or fallback to anonymous access.

Open the workspace and sign in with `admin`, `user` or `guest`. The login dialog
appears before lab data loads. **Log out** is in the top bar. A session lasts up
to 12 hours; without Remember, its cookie is a browser-session cookie (some
browsers restore these when restoring tabs). **Remember this browser** sets a
14-day cookie and server expiry. Logging out revokes that session and disconnects
its open consoles. Changing a fixed account password in the environment and
restarting revokes that account's sessions. Sessions otherwise survive a restart.

Each account is shared by everyone given its password; it does not identify an
individual operator.

## Login delay and account recovery

After three failed passwords for an account or source IP, subsequent checks wait
3–5 seconds **on the server before verifying the password**, including a correct
password. The login dialog displays a spinner while waiting. Only one login
check per account runs at once; additional requests receive a busy response.
Signed-in sessions and other accounts remain usable during the delay.

A successful login clears the account's and source's failure counters. Counters
also expire after 15 minutes without a failure, unless the account has locked.
The additional limits of 10 attempts per account and 30 per source per minute
still apply; rate-limited requests do not verify passwords or count as failures.
Source means the server's connecting peer; users behind one reverse proxy may
share its source limit.

**13 consecutive failed passwords lock that account**: three initial failures
and ten further delayed failures. Lockout survives ordinary server/container
restarts and inactivity. Even a correct password cannot sign in while locked;
already signed-in sessions remain usable. Someone who knows an account name can
deliberately trigger its lockout, so keep host access available for recovery.

To unlock an account, change its corresponding reset marker in the private
`.env`, then **recreate** the lab service using the same Compose files and mounts:

```dotenv
WL_AUTH_USER_RESET=reset-1
```

Use `WL_AUTH_ADMIN_RESET` or `WL_AUTH_GUEST_RESET` for the other accounts. Choose
a new marker each time (up to 128 characters); it is not a password. Keep it in
`.env` afterwards: any change, including removal, resets that account. Keeping
the same marker across restarts does not unlock it again. A plain `docker restart`
does not reload `.env`. Shut down Junos gracefully before service recreation.

Changing the account password also unlocks it and revokes its existing sessions.
A reset marker preserves those sessions. Neither reset clears the shared source
IP throttle, so a recently limited source may still need to wait before signing in.

## Deployment and state

For access beyond localhost/SSH forwarding, use HTTPS at a trusted reverse proxy
and set `WL_AUTH_COOKIE_SECURE=1`. The proxy must preserve the external `Host`
and support WebSocket upgrades; Weblab rejects cross-origin requests. Do not
expose an HTTP bypass around that proxy. Plain HTTP transmits credentials and
session cookies without encryption. Auth does not protect the Docker socket,
local wrapper ports or files from users who already control the server host.

Session cookies are HttpOnly and SameSite=Strict. Mutations also require a
session CSRF token. Sessions use random opaque tokens, stored only as hashes in
the database; passwords use salted PBKDF2-HMAC-SHA256 hashes. SQLite state is at
`<lab-data>/.auth/auth.sqlite3` with private filesystem permissions. Treat the
whole `.auth` directory as sensitive and back it up separately only if needed.

Topology JSON, saved-state ZIP exports and ZIP restore **do not contain or
replace accounts or login sessions**. Auth data and `.env` must not be copied
into container images or public artifacts. Container environment inspection is
available to the host administrator; `.env` credentials are not a secret store
against that administrator.

Raw console access remains powerful: an authorized writer can run device CLI or
PC shell commands, not just safe inspection commands. Reader command restrictions
will require a separate, deliberate execution policy; filtering keystroke
prefixes would not provide reliable command authorization.

## Activity records

With authentication enabled, admin can open **Activity records** using the small
clock button beside Log out. Filter by account or by Browser/Agent tools; Refresh
loads current results, and Older records pages back through history. Writer and
reader cannot retrieve this history, including through the API. Authentication-off
workspaces do not collect these records.

Records contain time, account/role, a non-secret login-session reference, source,
operation, an existing node/link ID when applicable, and result. Two logins using
the same fixed account have distinct session references; they still do not
identify individual people. Failed sign-ins show only a known claimed account,
with no authenticated session. Unknown account names are omitted.

Covered operations are sign-in/out attempts, topology and published-instructions
updates, node/lab start/stop, link faults, image uploads, imports/exports, Agent
session/settings/turn/approval/history changes and Agent preview/apply/start/input
tool calls. Routine reads and polling are omitted. Browser Agent controls and the
Agent's tool operations appear separately, correlated by Weblab login session.
An approval request being accepted by the API does not itself mean its operation
ran. Console lock handoffs, human keystrokes/commands, background lifecycle events
and changes performed outside Weblab are not recorded in this first slice.

Results distinguish **HTTP OK**, **tool OK**, **input sent**, **denied**, **failed**,
**uncertain** and **unfinished**. HTTP OK means the server returned a successful
response; tool OK means the tool returned successfully. Neither verifies guest
configuration or connectivity. Input sent confirms delivery status only. An
unfinished operation may still be running or may have been interrupted; refresh
to check its current record. Failed operations may have partial effects.

Passwords, bearer/session tokens, CSRF values, provider credentials, prompts,
attachments, document/configuration contents, raw console input/output, arbitrary
filenames and error messages are not copied into activity records. Existing
console recordings and Agent conversations retain their separate behavior.

Records persist in the private authentication SQLite database across restarts.
Keep at most **10,000 records or 30 days**, whichever is reached first; cleanup
runs on startup, writes and reads. Topology/ZIP exports exclude activity, and
imports preserve it. There is no clear/export activity button in this first
version. This is bounded operational history, not a tamper-proof audit service.
If recording fails, lab operations and logout continue; a server message and the
Activity viewer flag possible gaps until restart. A crash or unavailable storage
can leave missing or unfinished records.

Console recordings and launcher logs remain shared device output, not a per-user
command audit. Current lock metadata identifies the owner now, not the author of
past output. The Agent cannot split human history into admin/writer commands and
has no activity-query MCP tool yet. Named-user console attribution and filtered
assessment evidence remain future work.
