#!/usr/bin/env perl

# Run a console application on a Linux PTY and expose it over WebSocket.
#
#   wrapper-ws.pl -m IMAGE -p PORT -- [IMAGE OPTIONS] APPLICATION_ID
#
# Server-to-client console data uses binary WebSocket frames. Client-to-server
# raw/v1 text and binary frames are console input. In netlab.console.v2,
# binary frames are input and JSON text frames control the shared input lock.

use strict;
use warnings;
use bytes;

use Digest::SHA qw(sha1);
use Errno qw(EAGAIN EWOULDBLOCK EIO EINTR);
use Fcntl qw(F_GETFL F_SETFL O_NONBLOCK O_NOCTTY O_RDWR O_WRONLY O_APPEND O_CREAT O_NOFOLLOW);
use File::Spec ();
use Getopt::Long qw(GetOptionsFromArray);
use IO::Select ();
use IO::Socket::INET ();
use JSON::PP qw(encode_json decode_json);
use MIME::Base64 qw(decode_base64 encode_base64);
use POSIX qw(WNOHANG dup2 setsid strftime);

use constant {
    # Linux asm-generic ioctl values. IOL itself is Linux-only.
    TIOCGPTN   => 0x80045430,
    TIOCSPTLCK => 0x40045431,
    TIOCSCTTY  => 0x0000540e,

    WS_CONTINUATION => 0x0,
    WS_TEXT         => 0x1,
    WS_BINARY       => 0x2,
    WS_CLOSE        => 0x8,
    WS_PING         => 0x9,
    WS_PONG         => 0xa,
};

my $WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';

sub usage {
    my ($exit_code, $message) = @_;
    print STDERR "wrapper-ws.pl: $message\n" if defined $message;
    print STDERR <<'USAGE';
Usage: wrapper-ws.pl [-v] -m IMAGE -p PORT [--bind ADDRESS]
                     [--path /PATH] [--backlog-bytes N] [--iol-console]
                     [--max-frame-bytes N] [--exit-output-bytes N]
                     [--transcript FILE] [--transcript-bytes N] -- [IOL OPTIONS] ID
USAGE
    exit $exit_code;
}

sub parse_arguments {
    my @all = @_;
    my (@ours, @iou_args);
    my $separator = -1;

    for my $index (0 .. $#all) {
        if ($all[$index] eq '--') {
            $separator = $index;
            last;
        }
    }
    if ($separator >= 0) {
        @ours = @all[0 .. $separator - 1] if $separator > 0;
        @iou_args = @all[$separator + 1 .. $#all] if $separator < $#all;
    }
    else {
        @ours = @all;
    }

    my ($image, $port, $show_version);
    my $iol_console = 0;
    my $bind = '127.0.0.1';
    my $path = '/';
    my $backlog_bytes = 262_144;
    my $max_frame_bytes = 1_048_576;
    my $exit_output_bytes = 0;
    my $transcript;
    my $transcript_bytes = 4 * 1024 * 1024;

    Getopt::Long::Configure(qw(no_auto_abbrev no_ignore_case require_order));
    my $ok = GetOptionsFromArray(
        \@ours,
        'm|image=s'        => \$image,
        'p|port=i'         => \$port,
        'iol-console'      => \$iol_console,
        'bind=s'           => \$bind,
        'path=s'           => \$path,
        'backlog-bytes=i'  => \$backlog_bytes,
        'max-frame-bytes=i'=> \$max_frame_bytes,
        'exit-output-bytes=i' => \$exit_output_bytes,
        'transcript=s'     => \$transcript,
        'transcript-bytes=i' => \$transcript_bytes,
        'v|version'        => \$show_version,
        'h|help'           => sub { usage(0) },
    );
    usage(2, 'invalid arguments') unless $ok;
    if ($show_version) {
        print "wrapper-ws.pl 1.0\n";
        exit 0;
    }

    push @iou_args, @ours;
    usage(2, 'option -m/--image is required') unless defined $image;
    usage(2, 'option -p/--port is required') unless defined $port;
    usage(2, 'PORT must be between 1 and 65535')
        unless $port >= 1 && $port <= 65_535;
    usage(2, '--path must begin with /') unless $path =~ m{^/};
    usage(2, '--backlog-bytes cannot be negative') if $backlog_bytes < 0;
    usage(2, '--max-frame-bytes must be positive') if $max_frame_bytes < 1;
    usage(2, '--exit-output-bytes cannot be negative') if $exit_output_bytes < 0;
    usage(2, '--transcript-bytes must be between 1024 and 67108864')
        unless $transcript_bytes >= 1024 && $transcript_bytes <= 64 * 1024 * 1024;

    return ($image, $port, $bind, $path, $backlog_bytes,
            $max_frame_bytes, $exit_output_bytes, $transcript, $transcript_bytes, $iol_console, @iou_args);
}

# Retain current and previous segments across restarts. Log only PTY output:
# hidden passwords and WebSocket lock/control messages are never input records.
# A recording failure disables recording, not the device or shared console.
sub transcript_writer {
    my ($path, $limit) = @_;
    return sub {} unless defined $path;
    my ($handle, $size, $failed);
    my $open = sub {
        sysopen($handle, $path, O_WRONLY | O_APPEND | O_CREAT | O_NOFOLLOW | O_NONBLOCK, 0600)
            or die "open: $!";
        die 'not a regular file' unless -f $handle;
        $size = -s $handle;
    };
    return sub {
        my ($data) = @_;
        return if $failed;
        eval {
            $open->() unless $handle;
            while (length $data) {
                if ($size >= $limit) {
                    close $handle or die "close: $!";
                    undef $handle;
                    die 'unsafe transcript rotation' if -l $path || -l "$path.1";
                    rename($path, "$path.1") or die "rotate: $!";
                    $open->();
                }
                my $length = length($data) < $limit - $size ? length($data) : $limit - $size;
                my $written = syswrite($handle, $data, $length);
                next if !defined($written) && $! == EINTR;
                die "write: $!" unless defined($written) && $written > 0;
                substr($data, 0, $written, '');
                $size += $written;
            }
            1;
        } or do {
            $failed = 1;
            print STDERR "wrapper-ws.pl: console recording disabled: $@\n";
            close $handle if $handle;
        };
    };
}

sub set_nonblocking {
    my ($handle) = @_;
    my $flags = fcntl($handle, F_GETFL, 0);
    die "wrapper-ws.pl: fcntl(F_GETFL): $!\n" unless defined $flags;
    fcntl($handle, F_SETFL, $flags | O_NONBLOCK)
        or die "wrapper-ws.pl: fcntl(F_SETFL): $!\n";
}

sub open_linux_pty {
    my $master;
    sysopen($master, '/dev/ptmx', O_RDWR | O_NOCTTY)
        or die "wrapper-ws.pl: cannot open /dev/ptmx: $!\n";

    my $unlock = pack('i', 0);
    ioctl($master, TIOCSPTLCK, $unlock)
        or die "wrapper-ws.pl: cannot unlock PTY: $!\n";
    my $number = pack('i', 0);
    ioctl($master, TIOCGPTN, $number)
        or die "wrapper-ws.pl: cannot obtain PTY number: $!\n";
    return ($master, '/dev/pts/' . unpack('i', $number));
}

sub append_backlog {
    my ($backlog_ref, $limit, $data) = @_;
    return if $limit == 0 || $data eq '';
    $$backlog_ref .= $data;
    $$backlog_ref = substr($$backlog_ref, -$limit)
        if length($$backlog_ref) > $limit;
}

sub shell_display {
    return join ' ', map {
        my $item = $_;
        $item =~ s/'/'\\''/g;
        $item =~ /[^A-Za-z0-9_\.\/:=+-]/ ? "'$item'" : $item;
    } @_;
}

sub websocket_frame {
    my ($opcode, $payload) = @_;
    my $length = length($payload);
    my $header = pack('C', 0x80 | $opcode);
    if ($length <= 125) {
        $header .= pack('C', $length);
    }
    elsif ($length <= 65_535) {
        $header .= pack('Cn', 126, $length);
    }
    else {
        $header .= pack('CNN', 127, 0, $length);
    }
    return $header . $payload;
}

sub websocket_close_frame {
    my ($code, $reason) = @_;
    $reason //= '';
    $reason = substr($reason, 0, 123);
    return websocket_frame(WS_CLOSE, pack('n', $code) . $reason);
}

sub parse_http_upgrade {
    my ($buffer_ref, $expected_path) = @_;
    return (0) unless $$buffer_ref =~ /\r\n\r\n/;

    my $end = index($$buffer_ref, "\r\n\r\n") + 4;
    my $request = substr($$buffer_ref, 0, $end, '');
    my @lines = split /\r\n/, $request;
    my $request_line = shift @lines;
    return (-1, 'invalid HTTP request')
        unless defined $request_line &&
               $request_line =~ m{^GET\s+(\S+)\s+HTTP/1\.[01]$};
    my $target = $1;
    my ($cursor) = $target =~ /[?&]cursor=([a-z0-9-]{1,80}:\d{1,16})(?:&|$)/;
    $target =~ s/\?.*\z//;
    return (-1, 'WebSocket path not found') unless $target eq $expected_path;

    my %headers;
    for my $line (@lines) {
        next if $line eq '';
        return (-1, 'malformed HTTP header') unless $line =~ /^([^:]+):\s*(.*)$/;
        my ($name, $value) = (lc($1), $2);
        $headers{$name} = exists $headers{$name}
            ? "$headers{$name}, $value" : $value;
    }

    return (-1, 'Upgrade: websocket is required')
        unless lc($headers{upgrade} // '') eq 'websocket';
    return (-1, 'Connection: Upgrade is required')
        unless ($headers{connection} // '') =~ /(?:^|,)\s*upgrade\s*(?:,|$)/i;
    return (-1, 'Sec-WebSocket-Version 13 is required')
        unless ($headers{'sec-websocket-version'} // '') eq '13';
    my $key = $headers{'sec-websocket-key'} // '';
    $key =~ s/^\s+|\s+$//g;
    return (-1, 'invalid Sec-WebSocket-Key')
        unless $key =~ m{^[A-Za-z0-9+/]+={0,2}$} &&
               length(decode_base64($key)) == 16;

    my $accept = encode_base64(sha1($key . $WS_GUID), '');
    my $offered = $headers{'sec-websocket-protocol'} // '';
    my $versioned = $offered =~ /(?:^|,)\s*netlab.console.v2\s*(?:,|$)/ ? 2 :
                    $offered =~ /(?:^|,)\s*netlab.console.v1\s*(?:,|$)/ ? 1 : 0;
    my $protocol = $versioned ? "Sec-WebSocket-Protocol: netlab.console.v$versioned\r\n" : '';
    my $response = "HTTP/1.1 101 Switching Protocols\r\n" .
                   "Upgrade: websocket\r\n" .
                   "Connection: Upgrade\r\n" .
                   "Sec-WebSocket-Accept: $accept\r\n${protocol}\r\n";
    return (1, $response, $versioned, $cursor);
}

# Return (status, events...). status: 0=need data, 1=events, -1=protocol error.
# Each event is [opcode, payload]. Fragmented data is reassembled here.
sub parse_websocket_frames {
    my ($state, $max_frame_bytes) = @_;
    my @events;

    while (1) {
        my $buffer_length = length($state->{input});
        last if $buffer_length < 2;
        my ($first, $second) = unpack('CC', substr($state->{input}, 0, 2));
        my $fin = ($first & 0x80) != 0;
        my $opcode = $first & 0x0f;
        return (-1, 'RSV bits are unsupported') if $first & 0x70;
        return (-1, 'client frames must be masked') unless $second & 0x80;

        my $length = $second & 0x7f;
        my $offset = 2;
        if ($length == 126) {
            last if $buffer_length < 4;
            $length = unpack('n', substr($state->{input}, 2, 2));
            $offset = 4;
        }
        elsif ($length == 127) {
            last if $buffer_length < 10;
            my ($high, $low) = unpack('NN', substr($state->{input}, 2, 8));
            return (-1, 'frame is too large') if $high != 0;
            $length = $low;
            $offset = 10;
        }
        return (-1, 'frame exceeds --max-frame-bytes')
            if $length > $max_frame_bytes;
        return (-1, 'invalid control frame')
            if $opcode >= 8 && (!$fin || $length > 125);
        return (-1, 'invalid close frame') if $opcode == WS_CLOSE && $length == 1;
        last if $buffer_length < $offset + 4 + $length;

        my $mask = substr($state->{input}, $offset, 4);
        $offset += 4;
        my $payload = substr($state->{input}, $offset, $length);
        substr($state->{input}, 0, $offset + $length, '');
        my $mask_stream = substr($mask x int(($length + 3) / 4), 0, $length);
        $payload ^= $mask_stream if $length;

        if ($opcode == WS_CONTINUATION) {
            return (-1, 'unexpected continuation frame')
                unless defined $state->{fragment_opcode};
            $state->{fragment_data} .= $payload;
            return (-1, 'fragmented message is too large')
                if length($state->{fragment_data}) > $max_frame_bytes;
            if ($fin) {
                push @events, [$state->{fragment_opcode}, $state->{fragment_data}];
                undef $state->{fragment_opcode};
                $state->{fragment_data} = '';
            }
        }
        elsif ($opcode == WS_TEXT || $opcode == WS_BINARY) {
            return (-1, 'new data frame during fragmented message')
                if defined $state->{fragment_opcode};
            if ($fin) {
                push @events, [$opcode, $payload];
            }
            else {
                $state->{fragment_opcode} = $opcode;
                $state->{fragment_data} = $payload;
            }
        }
        elsif ($opcode == WS_CLOSE || $opcode == WS_PING || $opcode == WS_PONG) {
            push @events, [$opcode, $payload];
        }
        else {
            return (-1, 'unsupported WebSocket opcode');
        }
    }
    return (@events ? 1 : 0, @events);
}

sub main {
    my ($image, $port, $bind, $path, $backlog_limit,
        $max_frame_bytes, $exit_output_limit, $transcript, $transcript_limit, $iol_console, @iou_args) = parse_arguments(@ARGV);
    $image = File::Spec->rel2abs($image) if $image =~ m{/};
    my @command = ($image, @iou_args);

    my $listener = IO::Socket::INET->new(
        LocalAddr => $bind,
        LocalPort => $port,
        Proto     => 'tcp',
        Listen    => 4,
        ReuseAddr => 1,
    ) or die "wrapper-ws.pl: cannot listen on $bind:$port: $!\n";
    set_nonblocking($listener);

    my ($master, $slave_name) = open_linux_pty();
    my $child_pid = fork();
    die "wrapper-ws.pl: fork failed: $!\n" unless defined $child_pid;
    if ($child_pid == 0) {
        close $listener;
        close $master;
        setsid() or die "wrapper-ws.pl: setsid failed: $!\n";
        my $slave;
        sysopen($slave, $slave_name, O_RDWR)
            or die "wrapper-ws.pl: cannot open $slave_name: $!\n";
        my $ctty = pack('i', 0);
        ioctl($slave, TIOCSCTTY, $ctty)
            or die "wrapper-ws.pl: cannot acquire controlling PTY: $!\n";
        dup2(fileno($slave), 0) >= 0 or die "wrapper-ws.pl: dup2 stdin: $!\n";
        dup2(fileno($slave), 1) >= 0 or die "wrapper-ws.pl: dup2 stdout: $!\n";
        dup2(fileno($slave), 2) >= 0 or die "wrapper-ws.pl: dup2 stderr: $!\n";
        close $slave if fileno($slave) > 2;
        exec {$image} @command or do {
            print STDERR "wrapper-ws.pl: cannot execute $image: $!\n";
            POSIX::_exit(127);
        };
    }

    set_nonblocking($master);
    my $record = transcript_writer($transcript, $transcript_limit);
    $record->("\n--- Console started " . strftime('%Y-%m-%d %H:%M:%S UTC', gmtime) . " ---\n");
    my $readable = IO::Select->new($listener, $master);
    my $writable = IO::Select->new();
    my %clients;
    my $pty_input = '';
    my $lock_owner;
    my $lock_revision = 0;
    my $lock_dirty = 0;
    my $backlog = '';
    my $output_offset = 0;
    my $epoch = sprintf('%x-%x-%x', time, $$, int(rand(0xffffffff)));
    my $queue_limit = $backlog_limit + 131_072;
    $queue_limit = 1_048_576 if $queue_limit < 1_048_576;
    my $exit_output = '';
    my $requested_stop = 0;
    my $stopping = 0;
    my $child_status;

    my $disconnect = sub {
        my ($state) = @_;
        return unless exists $clients{$state->{id}};
        delete $clients{$state->{id}};
        $lock_dirty = 1; # The terminal-response station may have disconnected.
        if (defined $lock_owner && $lock_owner == $state->{id}) {
            undef $lock_owner;
            ++$lock_revision;
            $lock_dirty = 1;
        }
        $readable->remove($state->{socket});
        $writable->remove($state->{socket});
        close $state->{socket};
    };
    my $queue_client = sub {
        my ($state, $data) = @_;
        return unless exists $clients{$state->{id}} && $data ne '';
        # One slow/suspended station must never stall the PTY or other stations.
        if (length($state->{output}) + length($data) > $queue_limit) {
            print STDERR "wrapper-ws.pl: dropping slow WebSocket client\n";
            $disconnect->($state);
            return;
        }
        $state->{output} .= $data;
        $writable->add($state->{socket});
    };
    my $close_after_output = sub {
        my ($state) = @_;
        return unless exists $clients{$state->{id}};
        $state->{close_after_write} = 1;
        $readable->remove($state->{socket});
        $disconnect->($state) if $state->{output} eq '';
    };

    my $send_lock = sub {
        my ($state, $error) = @_;
        return unless ($state->{versioned} // 0) == 2 && !$state->{close_after_write};
        my @terminals = sort { $a <=> $b } map { $_->{id} }
            grep { !$_->{handshake} && !$_->{close_after_write} && ($_->{versioned} // 0) == 2 } values %clients;
        my $responder = defined($lock_owner) ? $lock_owner : $terminals[0];
        $queue_client->($state, websocket_frame(WS_TEXT, encode_json({
            type => 'console-lock', revision => $lock_revision,
            locked => defined($lock_owner) ? JSON::PP::true : JSON::PP::false,
            mine => defined($lock_owner) && $lock_owner == $state->{id} ? JSON::PP::true : JSON::PP::false,
            terminal_responder => defined($responder) && $responder == $state->{id} ? JSON::PP::true : JSON::PP::false,
            error => $error // '',
        })));
    };

    $SIG{PIPE} = 'IGNORE';
    $SIG{INT} = $SIG{TERM} = sub {
        $requested_stop = 1;
        $stopping = 1;
        kill $_[0], $child_pid if $child_pid;
    };

    print STDERR 'wrapper-ws.pl: started PID ', $child_pid, ': ',
        shell_display(@command), "\n";
    print STDERR "wrapper-ws.pl: WebSocket listening on ws://$bind:$port$path\n";
    if ($bind ne '127.0.0.1' && $bind ne 'localhost') {
        print STDERR "wrapper-ws.pl: WARNING: console has no authentication\n";
    }

    while (!$stopping) {
        my $waited = waitpid($child_pid, WNOHANG);
        if ($waited == $child_pid) {
            $child_status = $?;
            last;
        }
        my $now = time;
        for my $state (values %clients) {
            if (($state->{handshake} && $now - $state->{created} > 10) ||
                $now - $state->{last_seen} > 90) {
                $disconnect->($state);
            }
            elsif (!$state->{handshake} && !$state->{close_after_write} && $now - $state->{last_ping} > 30) {
                $queue_client->($state, websocket_frame(WS_PING, ''));
                $state->{last_ping} = $now;
            }
        }

        for my $handle ($readable->can_read(0.1)) {
            my $fd = fileno($handle);
            next unless defined $fd;
            if ($fd == fileno($listener)) {
                my $connection = $listener->accept();
                next unless $connection;
                set_nonblocking($connection);
                if (keys(%clients) >= 32) {
                    syswrite($connection, "HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n");
                    close $connection;
                    next;
                }
                my $id = fileno($connection);
                $clients{$id} = {
                    id => $id, socket => $connection, handshake => 1,
                    input => '', output => '', fragment_opcode => undef,
                    fragment_data => '', close_after_write => 0,
                    created => time, last_seen => time, last_ping => time,
                };
                $readable->add($connection);
            }
            elsif ($fd == fileno($master)) {
                my $output = '';
                my $count = sysread($master, $output, 65_536);
                if (!defined $count) {
                    next if $! == EAGAIN || $! == EWOULDBLOCK || $! == EINTR;
                    if ($! == EIO) { $stopping = 1; last; }
                    die "wrapper-ws.pl: PTY read failed: $!\n";
                }
                if ($count == 0) { $stopping = 1; last; }
                append_backlog(\$exit_output, $exit_output_limit, $output);
                $record->($output);
                append_backlog(\$backlog, $backlog_limit, $output);
                $output_offset += $count;
                my $frame = websocket_frame(WS_BINARY, $output);
                for my $state (values %clients) {
                    $queue_client->($state, $frame) unless $state->{handshake} || $state->{close_after_write};
                }
            }
            elsif (my $state = $clients{$fd}) {
                my $incoming = '';
                my $count = sysread($handle, $incoming, 65_536);
                if (!defined $count) {
                    next if $! == EAGAIN || $! == EWOULDBLOCK || $! == EINTR;
                    $disconnect->($state);
                    next;
                }
                if ($count == 0) { $disconnect->($state); next; }
                $state->{last_seen} = time;
                $state->{input} .= $incoming;

                if ($state->{handshake}) {
                    if (length($state->{input}) > 16_384 && index($state->{input}, "\r\n\r\n") < 0) {
                        $queue_client->($state, "HTTP/1.1 431 Request Header Fields Too Large\r\nConnection: close\r\nContent-Length: 0\r\n\r\n");
                        $close_after_output->($state);
                        next;
                    }
                    my ($status, $response, $versioned, $cursor) = parse_http_upgrade(\$state->{input}, $path);
                    next if $status == 0;
                    if ($status < 0) {
                        my $body = "$response\n";
                        $queue_client->($state, "HTTP/1.1 400 Bad Request\r\nConnection: close\r\n" .
                                       'Content-Length: ' . length($body) . "\r\nContent-Type: text/plain\r\n\r\n$body");
                        $close_after_output->($state);
                        next;
                    }
                    $state->{handshake} = 0;
                    $state->{versioned} = $versioned;
                    $lock_dirty = 1;
                    $queue_client->($state, $response);
                    my $start = $output_offset - length($backlog);
                    my $gap = 0;
                    if ($versioned && defined $cursor) {
                        my ($old_epoch, $offset) = split /:/, $cursor;
                        if ($old_epoch eq $epoch && $offset >= $start && $offset <= $output_offset) {
                            $start = 0 + $offset;
                        }
                        else { $gap = 1; }
                    }
                    if ($versioned) {
                        $queue_client->($state, websocket_frame(WS_TEXT, encode_json({
                            type => 'console-start', epoch => $epoch, offset => $start,
                            replay_bytes => $output_offset - $start,
                            gap => $gap ? JSON::PP::true : JSON::PP::false,
                        })));
                    }
                    $send_lock->($state);
                    my $replay = substr($backlog, $start - ($output_offset - length($backlog)));
                    $queue_client->($state, websocket_frame(WS_BINARY, $replay)) if $replay ne '';
                }

                next unless exists $clients{$fd} && !$state->{handshake} && !$state->{close_after_write};
                my ($status, @events) = parse_websocket_frames($state, $max_frame_bytes);
                if ($status < 0) {
                    $queue_client->($state, websocket_close_frame(1002, $events[0] // 'protocol error'));
                    $close_after_output->($state);
                    next;
                }
                for my $event (@events) {
                    my ($opcode, $payload) = @$event;
                    if ($opcode == WS_TEXT && ($state->{versioned} // 0) == 2) {
                        # In v2 text frames are controls, binary frames are PTY input.
                        # Never interpret a pasted CLI command as a lock request.
                        my $control = eval { decode_json($payload) };
                        my $action = ref($control) eq 'HASH' ? ($control->{action} // '') : '';
                        my $error = '';
                        if ($action !~ /^(lock|unlock|takeover)$/ ||
                            ref($control->{revision}) || !defined($control->{revision}) ||
                            $control->{revision} !~ /^\d{1,15}$/) {
                            $error = 'Invalid console lock request';
                        }
                        elsif ($control->{revision} != $lock_revision) {
                            $error = 'The console lock changed. Please try again.';
                        }
                        elsif ($action eq 'unlock' && defined($lock_owner) && $lock_owner != $state->{id}) {
                            $error = 'Only the station holding the lock can release it. Use Take over instead.';
                        }
                        elsif ($action eq 'lock' && defined($lock_owner) && $lock_owner != $state->{id}) {
                            $error = 'Another station locked this console. Confirm Take over to write.';
                        }
                        else {
                            my $next_owner = $action eq 'unlock' ? undef : $state->{id};
                            if ((defined($next_owner) ? $next_owner : -1) != (defined($lock_owner) ? $lock_owner : -1)) {
                                $lock_owner = $next_owner;
                                ++$lock_revision;
                                $lock_dirty = 1;
                                # A takeover also stops buffered input from the previous writer.
                                if (defined $lock_owner) {
                                    $pty_input = '';
                                    $writable->remove($master);
                                }
                            }
                        }
                        $send_lock->($state, $error);
                    }
                    elsif ($opcode == WS_TEXT || $opcode == WS_BINARY) {
                        # Enforce exclusivity for every client, including legacy clients.
                        next if defined($lock_owner) && $lock_owner != $state->{id};
                        if (length($pty_input) + length($payload) > 2 * $max_frame_bytes) {
                            $queue_client->($state, websocket_close_frame(1013, 'Console input queue is full'));
                            $close_after_output->($state);
                            last;
                        }
                        # Native IOL can treat ETX as an emulator termination.
                        # Translate only after input locks, for all client protocols.
                        $payload =~ tr/\x03/\x1e/ if $iol_console;
                        $pty_input .= $payload;
                        $writable->add($master) if $pty_input ne '';
                    }
                    elsif ($opcode == WS_PING) {
                        $queue_client->($state, websocket_frame(WS_PONG, $payload));
                    }
                    elsif ($opcode == WS_CLOSE) {
                        $queue_client->($state, websocket_frame(WS_CLOSE, $payload));
                        $close_after_output->($state);
                        last;
                    }
                }
            }
        }

        if ($lock_dirty) {
            $lock_dirty = 0;
            $send_lock->($_) for values %clients;
        }
        for my $handle ($writable->can_write(0)) {
            my $fd = fileno($handle);
            next unless defined $fd;
            if ($fd == fileno($master)) {
                my $written = syswrite($master, $pty_input);
                if (!defined $written) {
                    next if $! == EAGAIN || $! == EWOULDBLOCK || $! == EINTR;
                    $stopping = 1; last;
                }
                substr($pty_input, 0, $written, '');
                $writable->remove($master) if $pty_input eq '';
            }
            elsif (my $state = $clients{$fd}) {
                my $written = syswrite($handle, $state->{output});
                if (!defined $written) {
                    next if $! == EAGAIN || $! == EWOULDBLOCK || $! == EINTR;
                    $disconnect->($state); next;
                }
                substr($state->{output}, 0, $written, '');
                if ($state->{output} eq '') {
                    $writable->remove($handle);
                    $disconnect->($state) if $state->{close_after_write};
                }
            }
        }
    }

    $disconnect->($_) for values %clients;
    close $listener;
    # A fast child can exit before the select loop reads its diagnostic output.
    # The PTY is nonblocking, so drain any remaining bytes before closing it.
    if (defined($transcript) || (!$requested_stop && $exit_output_limit)) {
        for (1 .. 256) {
            my $output = '';
            my $count = sysread($master, $output, 65_536);
            last unless defined($count) && $count > 0;
            append_backlog(\$exit_output, $exit_output_limit, $output);
            $record->($output);
        }
        if (!$requested_stop && $exit_output ne '') {
            print STDERR "wrapper-ws.pl: last console output before exit:\n", $exit_output, "\n";
        }
    }
    $record->("\n--- Console ended " . strftime('%Y-%m-%d %H:%M:%S UTC', gmtime) . " ---\n");
    close $master;
    if (!defined $child_status) {
        my $waited = waitpid($child_pid, WNOHANG);
        if ($waited == $child_pid) {
            $child_status = $?;
        }
        else {
            kill 'TERM', $child_pid;
            waitpid($child_pid, 0);
            $child_status = $?;
        }
    }
    if (($child_status & 127) == 0) {
        my $code = $child_status >> 8;
        print STDERR "wrapper-ws.pl: child exited with status $code\n";
        return $code;
    }
    my $signal = $child_status & 127;
    print STDERR "wrapper-ws.pl: child terminated by signal $signal\n";
    return 128 + $signal;
}

exit main();
