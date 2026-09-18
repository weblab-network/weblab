#!/usr/bin/env perl

# Run a console application on a Linux PTY and expose it through Telnet/TCP.
#
# The command line intentionally follows the old Cisco IOU/IOL wrapper:
#
#   wrapper.pl -m IMAGE -p PORT -- [IMAGE OPTIONS] APPLICATION_ID
#
# This program contains no Cisco code and does not modify or license an image.

use strict;
use warnings;
use bytes;

use Errno qw(EAGAIN EWOULDBLOCK EIO EINTR);
use Fcntl qw(F_GETFL F_SETFL O_NONBLOCK O_NOCTTY O_RDWR);
use File::Spec ();
use Getopt::Long qw(GetOptionsFromArray);
use IO::Select ();
use IO::Socket::INET ();
use POSIX qw(WNOHANG dup2 setsid);

use constant {
    IAC               => 255,
    DO                => 253,
    DONT              => 254,
    WILL              => 251,
    WONT              => 252,
    SB                => 250,
    SE                => 240,
    TELOPT_ECHO       => 1,
    SUPPRESS_GO_AHEAD => 3,
    LINEMODE          => 34,

    # Linux asm-generic ioctl values. IOL itself is Linux-only.
    TIOCGPTN          => 0x80045430,
    TIOCSPTLCK        => 0x40045431,
    TIOCSCTTY         => 0x0000540e,
};

sub usage {
    my ($exit_code, $message) = @_;
    print STDERR "wrapper.pl: $message\n" if defined $message;
    print STDERR <<'USAGE';
Usage: wrapper.pl [-v] -m IMAGE -p PORT [--bind ADDRESS]
                  [--backlog-bytes N] -- [IOL OPTIONS] ID
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
    my $bind = '127.0.0.1';
    my $backlog_bytes = 262_144;

    Getopt::Long::Configure(qw(no_auto_abbrev no_ignore_case require_order));
    my $ok = GetOptionsFromArray(
        \@ours,
        'm|image=s'       => \$image,
        'p|port=i'        => \$port,
        'bind=s'          => \$bind,
        'backlog-bytes=i' => \$backlog_bytes,
        'v|version'       => \$show_version,
        'h|help'          => sub { usage(0) },
    );
    usage(2, 'invalid arguments') unless $ok;

    if ($show_version) {
        print "wrapper.pl 1.0\n";
        exit 0;
    }

    # With no explicit --, preserve operands left after wrapper options.
    push @iou_args, @ours;
    usage(2, 'option -m/--image is required') unless defined $image;
    usage(2, 'option -p/--port is required') unless defined $port;
    usage(2, 'PORT must be between 1 and 65535')
        unless $port >= 1 && $port <= 65_535;
    usage(2, '--backlog-bytes cannot be negative') if $backlog_bytes < 0;

    return ($image, $port, $bind, $backlog_bytes, @iou_args);
}

sub set_nonblocking {
    my ($handle) = @_;
    my $flags = fcntl($handle, F_GETFL, 0);
    die "wrapper.pl: fcntl(F_GETFL): $!\n" unless defined $flags;
    fcntl($handle, F_SETFL, $flags | O_NONBLOCK)
        or die "wrapper.pl: fcntl(F_SETFL): $!\n";
}

sub open_linux_pty {
    my $master;
    sysopen($master, '/dev/ptmx', O_RDWR | O_NOCTTY)
        or die "wrapper.pl: cannot open /dev/ptmx: $!\n";

    my $unlock = pack('i', 0);
    ioctl($master, TIOCSPTLCK, $unlock)
        or die "wrapper.pl: cannot unlock PTY: $!\n";

    my $number = pack('i', 0);
    ioctl($master, TIOCGPTN, $number)
        or die "wrapper.pl: cannot obtain PTY number: $!\n";
    my $slave_name = '/dev/pts/' . unpack('i', $number);
    return ($master, $slave_name);
}

sub telnet_preamble {
    return pack(
        'C*',
        IAC, WILL, TELOPT_ECHO,
        IAC, WILL, SUPPRESS_GO_AHEAD,
        IAC, DO,   SUPPRESS_GO_AHEAD,
        IAC, DONT, LINEMODE,
    );
}

sub telnet_escape {
    my ($data) = @_;
    $data =~ s/\xff/\xff\xff/g;
    return $data;
}

sub telnet_decode {
    my ($decoder, $data) = @_;
    my $plain = '';

    for my $byte (unpack('C*', $data)) {
        if ($decoder->{state} eq 'data') {
            if ($byte == IAC) {
                $decoder->{state} = 'iac';
            }
            else {
                $plain .= chr($byte);
            }
        }
        elsif ($decoder->{state} eq 'iac') {
            if ($byte == IAC) {
                $plain .= chr(IAC);
                $decoder->{state} = 'data';
            }
            elsif ($byte == DO || $byte == DONT ||
                   $byte == WILL || $byte == WONT) {
                $decoder->{state} = 'option';
            }
            elsif ($byte == SB) {
                $decoder->{state} = 'subneg';
            }
            else {
                $decoder->{state} = 'data';
            }
        }
        elsif ($decoder->{state} eq 'option') {
            $decoder->{state} = 'data';
        }
        elsif ($decoder->{state} eq 'subneg') {
            $decoder->{state} = 'subneg_iac' if $byte == IAC;
        }
        elsif ($decoder->{state} eq 'subneg_iac') {
            $decoder->{state} = ($byte == SE) ? 'data' : 'subneg';
        }
    }

    my $normalized = '';
    for my $byte (unpack('C*', $plain)) {
        if ($decoder->{pending_cr}) {
            $normalized .= "\r";
            $decoder->{pending_cr} = 0;
            next if $byte == 0 || $byte == 10;
        }
        if ($byte == 13) {
            $decoder->{pending_cr} = 1;
        }
        else {
            $normalized .= chr($byte);
        }
    }
    return $normalized;
}

sub append_backlog {
    my ($backlog_ref, $limit, $data) = @_;
    return if $limit == 0 || $data eq '';
    $$backlog_ref .= $data;
    if (length($$backlog_ref) > $limit) {
        $$backlog_ref = substr($$backlog_ref, -$limit);
    }
}

sub shell_display {
    return join ' ', map {
        my $item = $_;
        $item =~ s/'/'\\''/g;
        $item =~ /[^A-Za-z0-9_\.\/:=+-]/ ? "'$item'" : $item;
    } @_;
}

sub main {
    my ($image, $port, $bind, $backlog_limit, @iou_args) =
        parse_arguments(@ARGV);
    $image = File::Spec->rel2abs($image) if $image =~ m{/};
    my @command = ($image, @iou_args);

    my $listener = IO::Socket::INET->new(
        LocalAddr => $bind,
        LocalPort => $port,
        Proto     => 'tcp',
        Listen    => 4,
        ReuseAddr => 1,
    ) or die "wrapper.pl: cannot listen on $bind:$port: $!\n";
    set_nonblocking($listener);

    my ($master, $slave_name) = open_linux_pty();
    my $child_pid = fork();
    die "wrapper.pl: fork failed: $!\n" unless defined $child_pid;

    if ($child_pid == 0) {
        close $listener;
        close $master;
        setsid() or die "wrapper.pl: setsid failed: $!\n";

        my $slave;
        sysopen($slave, $slave_name, O_RDWR)
            or die "wrapper.pl: cannot open $slave_name: $!\n";
        my $ctty = pack('i', 0);
        ioctl($slave, TIOCSCTTY, $ctty)
            or die "wrapper.pl: cannot acquire controlling PTY: $!\n";
        dup2(fileno($slave), 0) >= 0 or die "wrapper.pl: dup2 stdin: $!\n";
        dup2(fileno($slave), 1) >= 0 or die "wrapper.pl: dup2 stdout: $!\n";
        dup2(fileno($slave), 2) >= 0 or die "wrapper.pl: dup2 stderr: $!\n";
        close $slave if fileno($slave) > 2;

        exec {$image} @command or do {
            print STDERR "wrapper.pl: cannot execute $image: $!\n";
            POSIX::_exit(127);
        };
    }

    set_nonblocking($master);
    my $readable = IO::Select->new($listener, $master);
    my $writable = IO::Select->new();
    my ($client, $decoder);
    my $client_output = '';
    my $backlog = '';
    my $stopping = 0;
    my $child_status;

    my $disconnect = sub {
        return unless defined $client;
        $readable->remove($client);
        $writable->remove($client);
        close $client;
        undef $client;
        undef $decoder;
        $client_output = '';
    };

    my $queue_client = sub {
        my ($data) = @_;
        return if !defined $client || $data eq '';
        $client_output .= $data;
        $writable->add($client);
    };

    $SIG{INT} = $SIG{TERM} = sub {
        $stopping = 1;
        kill $_[0], $child_pid if $child_pid;
    };

    print STDERR 'wrapper.pl: started PID ', $child_pid, ': ',
        shell_display(@command), "\n";
    print STDERR "wrapper.pl: console listening on $bind:$port\n";
    if ($bind ne '127.0.0.1' && $bind ne 'localhost') {
        print STDERR "wrapper.pl: WARNING: console has no authentication\n";
    }

    while (!$stopping) {
        my $waited = waitpid($child_pid, WNOHANG);
        if ($waited == $child_pid) {
            $child_status = $?;
            last;
        }

        for my $handle ($readable->can_read(0.5)) {
            if (fileno($handle) == fileno($listener)) {
                my $connection = $listener->accept();
                next unless $connection;
                set_nonblocking($connection);

                if (defined $client) {
                    syswrite($connection, "Console already in use.\r\n");
                    close $connection;
                    next;
                }

                $client = $connection;
                $decoder = { state => 'data', pending_cr => 0 };
                $readable->add($client);
                $queue_client->(telnet_preamble() . telnet_escape($backlog));
                $backlog = '';
                my $peer = eval { $client->peerhost . ':' . $client->peerport };
                $peer = 'unknown peer' unless defined $peer;
                print STDERR "wrapper.pl: console connected from $peer\n";
            }
            elsif (fileno($handle) == fileno($master)) {
                my $output = '';
                my $count = sysread($master, $output, 65_536);
                if (!defined $count) {
                    next if $! == EAGAIN || $! == EWOULDBLOCK || $! == EINTR;
                    if ($! == EIO) {
                        $stopping = 1;
                        last;
                    }
                    die "wrapper.pl: PTY read failed: $!\n";
                }
                if ($count == 0) {
                    $stopping = 1;
                    last;
                }

                if (defined $client) {
                    $queue_client->(telnet_escape($output));
                }
                else {
                    append_backlog(\$backlog, $backlog_limit, $output);
                }
            }
            elsif (defined $client && fileno($handle) == fileno($client)) {
                my $incoming = '';
                my $count = sysread($client, $incoming, 65_536);
                if (!defined $count) {
                    next if $! == EAGAIN || $! == EWOULDBLOCK || $! == EINTR;
                    print STDERR "wrapper.pl: console disconnected: $!\n";
                    $disconnect->();
                    next;
                }
                if ($count == 0) {
                    print STDERR "wrapper.pl: console disconnected\n";
                    $disconnect->();
                    next;
                }

                my $console_input = telnet_decode($decoder, $incoming);
                if ($console_input ne '') {
                    my $offset = 0;
                    while ($offset < length($console_input)) {
                        my $written = syswrite(
                            $master,
                            $console_input,
                            length($console_input) - $offset,
                            $offset,
                        );
                        if (!defined $written) {
                            next if $! == EINTR;
                            last if $! == EAGAIN || $! == EWOULDBLOCK || $! == EIO;
                            die "wrapper.pl: PTY write failed: $!\n";
                        }
                        $offset += $written;
                    }
                }
            }
        }

        for my $handle ($writable->can_write(0)) {
            next unless defined $client && fileno($handle) == fileno($client);
            my $written = syswrite($client, $client_output);
            if (!defined $written) {
                next if $! == EAGAIN || $! == EWOULDBLOCK || $! == EINTR;
                append_backlog(\$backlog, $backlog_limit, $client_output);
                $disconnect->();
                next;
            }
            substr($client_output, 0, $written, '');
            $writable->remove($client) if $client_output eq '';
        }
    }

    $disconnect->();
    close $listener;
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
        print STDERR "wrapper.pl: child exited with status $code\n";
        return $code;
    }

    my $signal = $child_status & 127;
    print STDERR "wrapper.pl: child terminated by signal $signal\n";
    return 128 + $signal;
}

exit main();
