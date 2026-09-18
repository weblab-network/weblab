"""Cisco IOS configuration capture; shared console transport lives separately."""
import re

MAX_CONFIG = 16_384
EXEC = re.compile(r'(?:^|\n)([A-Za-z0-9_.-]+(?:\([^\n]*\))?[>#])\s*$')
LOG = re.compile(rb'%[A-Z][A-Z0-9_]*-\d-[A-Z0-9_]+:|<ios-log-msg[ >]')
DIRECTIVE = re.compile(r'(?:no )?logging console(?: [A-Za-z0-9_.-]+)*\Z')
BANNERS = re.compile(r'(?ms)^(banner[ \t]+\S+[ \t]+)\^C(.*?)\^C')


def portable_banners(config):
    # The file parser treats literal ^C differently from the console display.
    def replace(match):
        body = match.group(2)
        delimiter = next((char for char in '|~#%!;' + ''.join(map(chr, range(33, 127)))
                          if char not in body), None)
        if delimiter is None:
            raise ValueError('Banner has no available printable delimiter; use a saved lab ZIP')
        return match.group(1) + delimiter + body + delimiter
    return BANNERS.sub(replace, config)


def restore_exported_logging(config, original):
    # Banner text can contain lines that look like configuration commands.
    protected = [match.span() for match in BANNERS.finditer(config)]
    matches = [match for match in re.finditer(r'(?m)^(?:no )?logging console(?: [^\n]*)?\n', config)
               if not any(start <= match.start() < end for start, end in protected)]
    if [match.group().rstrip('\n') for match in matches] != ['no logging console']:
        raise ValueError('Captured configuration does not confirm console logging suppression')
    match = matches[0]
    return config[:match.start()] + ''.join(line + '\n' for line in original) + config[match.end():]


class CiscoIOS:
    """Live running configuration only; never save it or fall back to startup state."""

    def read_prompt(self, console, prompt=None, timeout=20, config_mode=False, after=None, hostname=None):
        fallback = None
        if prompt is not None:
            fallback = re.compile('(' + re.escape(prompt) + r')\s*$')
        elif hostname is not None:
            fallback = re.compile('(' + re.escape(hostname) + r'(?:\(config\))?#)\s*$')
        output, found = console.read_until(EXEC, timeout=timeout, pager=b'--More--',
                                          after=after, interrupted=LOG, interrupted_prompt=fallback)
        if not found.endswith('#') or ('(' in found and not config_mode):
            raise ValueError('Leave this console at privileged EXEC (#), outside configuration mode, then export again')
        if prompt is not None and found != prompt:
            raise ValueError('Console prompt changed during capture')
        return output, found

    @staticmethod
    def send(console, data):
        if not console.lock or not console.lock['mine']:
            raise ValueError('Another station took over input; configuration capture stopped')
        console.send(data)

    def command(self, console, command, prompt, *, config_mode=False, timeout=20):
        self.send(console, command.encode('ascii') + b'\r')
        output, _ = self.read_prompt(console, prompt, timeout, config_mode, command.encode('ascii'))
        if re.search(r'(?m)^%\s*(?:Invalid|Error|Incomplete|Ambiguous|Authorization|Access denied)', output, re.I):
            raise ValueError(f'Device rejected export command: {command}')
        return output

    def query_lines(self, console, command, prompt):
        for _ in range(3):
            output = self.command(console, command, prompt)
            # Ignore earlier prompts from a cancelled command, then require a
            # clean response. Retry queries when an already-queued log arrives.
            _, echo, output = output.partition(command)
            if echo and not LOG.search(console.last_output):
                return [line for line in output.strip('\r\n').splitlines() if line.strip()]
        raise ValueError('Console output was interrupted; wait for a quiet prompt and export again')

    def settings(self, console, prompt):
        lines = []
        # Separate short queries avoid CLI help characters and echoed-line wrapping.
        for command in ('show run | inc ^logging console', 'show run | inc ^no logging console'):
            lines.extend(self.query_lines(console, command, prompt))
        if any(not DIRECTIVE.fullmatch(line) for line in lines) or len(set(lines)) != len(lines):
            raise ValueError('Cannot determine the original console logging settings safely')
        return tuple(lines)

    def enabled(self, console, prompt):
        lines = self.query_lines(console, 'show log | inc ^ *Console logging:', prompt)
        if len(lines) != 1:
            raise ValueError('Cannot determine whether console logging is enabled')
        match = re.fullmatch(r'\s*Console logging: (disabled|level [a-z]+)(?:,.*)?', lines[0])
        if not match:
            raise ValueError('Unsupported console logging status')
        return match.group(1) != 'disabled'

    def configure(self, console, prompt, commands):
        config_prompt = prompt[:-1] + '(config)#'
        self.command(console, 'configure terminal', config_prompt, config_mode=True)
        for command in commands:
            self.command(console, command, config_prompt, config_mode=True)
        self.command(console, 'end', prompt)

    def restore(self, console, prompt, original):
        # q exits an IOS pager; Ctrl-U clears it if we are already at a prompt.
        # Do not send Ctrl-C/Ctrl-Z: some IOL PTYs interpret these as host signals.
        self.send(console, b'q\x15\r')
        _, current = self.read_prompt(console, config_mode=True, hostname=prompt[:-1])
        if current == prompt[:-1] + '(config)#':
            self.command(console, 'end', prompt)
        elif current != prompt:
            raise ValueError('Cannot safely leave the current console mode')
        self.command(console, 'show clock', prompt)
        if self.settings(console, prompt) != original or not self.enabled(console, prompt):
            self.configure(console, prompt, ('default logging console', *original))
        # Verification can encounter the device's own logging-restored notice.
        for attempt in range(2):
            try:
                if self.settings(console, prompt) != original or not self.enabled(console, prompt):
                    raise ValueError('Original console logging settings did not return')
                return
            except (ValueError, OSError):
                if attempt:
                    raise
                self.send(console, b'\x15\r')
                self.read_prompt(console, prompt)

    def capture(self, console):
        for attempt in range(3):
            self.send(console, b'\x15\r')
            try:
                _, prompt = self.read_prompt(console, timeout=5)
                break
            except (ValueError, OSError):
                if attempt == 2 or not LOG.search(getattr(console, 'last_output', b'')):
                    raise
        original = self.settings(console, prompt)
        enabled = self.enabled(console, prompt)
        attempted_change = False
        failure = None
        result = None
        try:
            if enabled:
                attempted_change = True  # Include uncertain command-delivery failures.
                self.configure(console, prompt, ('no logging console',))
                if self.enabled(console, prompt):
                    raise ValueError('Console logging could not be suppressed; no configuration exported')
            # A logging-change notice may already be queued when suppression
            # completes. Discard the entire contaminated response and retry.
            for _ in range(3):
                output = self.command(console, 'show running-config', prompt, timeout=30)
                if not LOG.search(console.last_output):
                    break
            else:
                raise ValueError('Configuration output contains a log message; no configuration exported')
            header = re.search(r'Current configuration\s*:\s*\d+\s+bytes[^\n]*\n', output, re.I)
            if not header:
                raise ValueError('Device did not return a running configuration; check EXEC privileges')
            result = output[header.end():].strip() + '\n'
            if not re.search(r'(?:^|\n)[ \t]*end\s*\n\Z', result):
                raise ValueError('Configuration capture was incomplete; no file exported')
            if enabled:
                result = restore_exported_logging(result, original)
            result = portable_banners(result)
            if len(result.encode()) > MAX_CONFIG:
                raise ValueError('Running configuration exceeds the 16 KiB initial-config limit; use a saved lab ZIP instead')
            if any(ord(char) < 32 and char not in '\n\t' for char in result) or '\x7f' in result:
                raise ValueError('Configuration capture contains unexpected terminal controls')
        except (ValueError, OSError) as error:
            failure = error
        finally:
            if attempted_change:
                try:
                    self.restore(console, prompt, original)
                except (ValueError, OSError) as error:
                    recovery = '\n'.join(('configure terminal', 'default logging console', *original, 'end'))
                    detail = f'{failure}. ' if failure else ''
                    raise ValueError(detail + 'Console logging restoration could not be verified; '
                                     'logging may still be disabled. No file exported. '
                                     'At this device’s privileged (#) prompt, restore with:\n' + recovery +
                                     f'\nRestoration error: {error}') from error
        if failure:
            raise failure
        return result
