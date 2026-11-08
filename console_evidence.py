"""Bounded hints about ONE console capture, never command success or authorization.

These are deliberately not a terminal emulator or a persistent CLI state machine.
Unknown output stays unknown. Bulk input has a separate, conservative prompt
gate; no hint authorizes input or establishes command success.
"""
import re

import frr
import ll2s_device
import vios


PAGING_COMMANDS = {
    'cisco_ios': ('terminal length 0', 'EXEC'),
    'frr': ('terminal length 0', 'vtysh EXEC, not the container shell'),
    'veos': ('terminal length 0', 'EOS EXEC, not Bash'),
    'junos': ('set cli screen-length 0', 'Junos operational CLI, not configuration or shell'),
    'exos': ('disable clipaging', 'EXOS CLI'),
}


def platform(node):
    if node.get('type') == 'pc':
        return 'alpine'
    if ll2s_device.is_ll2s(node):
        return 'll2s'
    if frr.is_frr(node):
        return 'frr'
    if vios.is_junos(node):
        return 'junos'
    if vios.is_exos(node):
        return 'exos'
    if vios.is_veos(node):
        return 'veos'
    if node.get('image', '').endswith(('.bin', '.qcow2')):
        return 'cisco_ios'
    return 'unknown'


def cisco_tail(line):
    """Recognize a Cisco prompt only at the capture's final nonempty line."""
    if re.search(r'--More--\s*$', line, re.I):
        return 'pager', None
    if re.search(r'Press RETURN to get started[.!]?\s*$', line, re.I):
        return 'press_return', None
    if re.fullmatch(r'(?:%\s*)?(?:Session expired|.* con\d+ is now available)[.!]?', line, re.I):
        return 'session_ended', None
    match = re.fullmatch(r'[\w.:/-]{1,80}(\([\w.:/-]{1,64}\))?([>#])(.*)', line)
    if match:
        mode, end, pending = match.groups()
        if mode and not mode.startswith('(config'):
            return 'unknown', None
        hint = ('config' if mode else 'privileged_exec' if end == '#' else 'user_exec')
        return hint, bool(pending.strip())
    return 'unknown', None


def junos_tail(line, lines):
    # Operational and configuration syntax are handled independently of Cisco.
    match = re.fullmatch(r'[\w.-]+@[\w.-]+([>#%])(.*)', line)
    if match:
        end, pending = match.groups()
        if end == '>':
            return 'operational', bool(pending.strip())
        if end == '%':
            return 'shell', bool(pending.strip())
        if len(lines) > 1 and re.fullmatch(r'\[edit(?: .*)?\]', lines[-2]):
            return 'config', bool(pending.strip())
        # A hash alone is ambiguous between shell and configuration contexts.
    if re.search(r'---\(more(?: \d+%)?\)---\s*$', line, re.I):
        return 'pager', None
    return 'unknown', None


def bulk_prompt(node, result):
    """Return (family, mode) only for a complete, empty top-level prompt.

    This additional refusal gate is not authentication or a terminal emulator.
    Keep vendor grammars independent; configuration submodes and custom/unknown
    prompts require individual inspection/input instead of a broadcast.
    """
    if (result.get('capture_end') != 'timeout' or result.get('gap') or
            result.get('error') or result.get('unread_cursor')):
        return None
    lines = result.get('output', '').rstrip('\n').split('\n')
    line = lines[-1].strip()
    family = platform(node)
    mode = None
    if family == 'cisco_ios':
        hint, pending = cisco_tail(line)
        if pending is False and hint in ('user_exec', 'privileged_exec'):
            mode = hint
    elif family == 'junos':
        hint, pending = junos_tail(line, lines)
        if hint == 'operational' and pending is False:
            mode = 'operational'
    elif family == 'frr':
        # vtysh hostname prompt; excludes Bash's bash-X.Y# and shell paths.
        if re.fullmatch(r'[A-Za-z0-9_][\w.-]{0,79}[>#]', line) and not line.startswith('bash-'):
            mode = 'privileged_exec' if line.endswith('#') else 'user_exec'
    elif family == 'veos':
        # EOS EXEC; Bash prompts contain brackets, user@host or a path.
        if re.fullmatch(r'[A-Za-z0-9_][\w.-]{0,79}[>#]', line) and not line.startswith('bash-'):
            mode = 'privileged_exec' if line.endswith('#') else 'user_exec'
    elif family == 'll2s':
        if re.fullmatch(r'[A-Za-z0-9_][\w.-]{0,79}#', line):
            mode = 'exec'
    elif family == 'exos':
        # EXOS includes a command counter and optionally an unsaved-change '*'.
        if re.fullmatch(r'(?:\*\s*)?[A-Za-z0-9_][\w.-]{0,79}\.\d+\s+#', line):
            mode = 'exec'
    elif family == 'alpine':
        # Alpine ash: / # (non-login shell) or hostname:directory#.
        if (re.fullmatch(r'(?:[\w.-]+@)?[\w.-]+:(?:~|/)[\w./~-]*[#$]', line) or
                re.fullmatch(r'/[\w./~-]* [#$]', line)):
            mode = 'root_shell' if line.endswith('#') else 'user_shell'
    return (family, mode) if mode else None


def summarize(node, result):
    output = result.get('output', '')
    lines = output.rstrip('\n').split('\n') if output else []
    line = lines[-1].strip() if lines else ''
    family = platform(node)
    hint, pending = 'unknown', None
    # Never lift a prompt out of older lines, or classify a cut-off line as ready.
    if result.get('capture_end') == 'limit' or result.get('gap') or result.get('error'):
        hint = 'incomplete'
    elif result.get('capture_end') in ('disconnected', 'lock_lost'):
        hint = 'unavailable'
    elif not output:
        hint = 'no_new_output'
    elif re.fullmatch(r'(?:\S+\s+)?(?:login|username):\s*', line, re.I):
        hint = 'login'
    elif re.fullmatch(r'(?:\S+\s+)?password:\s*', line, re.I):
        hint = 'password'
    elif re.search(r'\[(?:yes/no|y/n|confirm)\][?:]?\s*$', line, re.I):
        hint = 'confirmation'
    elif family == 'cisco_ios':
        hint, pending = cisco_tail(line)
    elif family == 'junos':
        hint, pending = junos_tail(line, lines)
    errors = []
    if family == 'cisco_ios':
        errors = [s[:160] for s in lines if re.match(
            r'^%\s*(?:Invalid input|Ambiguous command|Incomplete command|Unknown command|'
            r'Unrecognized command|Bad secrets|Access denied|Authorization failed|Error)', s, re.I)][-2:]
    elif family == 'junos':
        errors = [s[:160] for s in lines if re.match(r'^(?:error:|syntax error\b)', s, re.I)][-2:]
    paging = PAGING_COMMANDS.get(family)
    return {'scope': 'this_capture_only', 'platform': family, 'tail_hint': hint,
            'tail': line[-160:], 'pending_input': pending, 'error_lines': errors,
            'paging': ({'disable_command': paging[0], 'mode': paging[1],
                        'scope': 'current CLI session; inspect prompt before using'} if paging else None),
            'notice': 'Heuristic excerpts, not live CLI state or command success. '
                      'History may be stale; inspect output order. No error marker does not prove success.'}


def next_action(result, evidence):
    """Contextual advice only; never sends input or changes permission/guards."""
    fallback = result.get('next_action', '')
    hint = evidence['tail_hint']
    if hint in ('user_exec', 'privileged_exec', 'config', 'operational') and evidence.get('pending_input') is False:
        return ('An empty CLI prompt is already visible. After the required fresh read, send the intended '
                'command directly in this mode; do not press Enter again just to obtain a prompt. '
                'Each input, including Enter, consumes that read. A send result still requires a new '
                'get_console_output before the next input. ' + fallback)
    if hint == 'pager':
        return ('A pager is waiting; this is not complete command output. If full output was requested, '
                'read fresh state and use an appropriate pager key (space on this CLI), then continue '
                'until the final prompt. Finding the requested fields does not finish a full capture. '
                'Do not type a command into the pager. ' + fallback)
    lines = result.get('output', '').splitlines()
    log_tail = (evidence['platform'] == 'cisco_ios' and hint == 'unknown' and lines
                and result.get('capture_end') == 'timeout'
                and re.search(r'%[A-Z][A-Z0-9_]*-\d-[A-Z0-9_]+:', lines[-1]))
    if hint in ('press_return', 'session_ended') or log_tail:
        return ('For requested console work, idle boot/syslog text without a prompt may be waiting for Enter. '
                'After checking for unfinished input, login/password or confirmation questions and an active '
                'command, use send_console_key with key="enter" once, then read again. Old logs alone do '
                'not mean it is still booting. Do not invent command results from these logs. ' + fallback)
    return fallback
