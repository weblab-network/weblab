#!/bin/sh
# Weblab fresh-install helper. Run on the Docker host, not inside a container.
set -eu

VERSION=0.10.2
LL2S_IMAGE=ghcr.io/weblab-network/ll2s:0.2.0-ovs-rstp1
FRR_IMAGE=ghcr.io/weblab-network/weblab-frr:10.7.1-wl1
name=weblab
port=8080
agent=0
kvm=0
examples=
dry_run=0
start_example=0
auth=0
auth_file=
bind=127.0.0.1
dns=

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'EOF'
Usage: sh weblab-install.sh [options]
Fresh Linux amd64 installation using prebuilt images and local rootful Docker.
  --with-agent          Include the optional isolated Agent companion
  --with-kvm            Pass /dev/kvm to the core for QEMU devices
  --with-auth           Enable admin/user/guest login; listen on 0.0.0.0
  --auth-file FILE      NEW key file (default: ./NAME-auth.env; mode 600)
  --name NAME           Container/volume prefix (default: weblab)
  --port PORT           HTTP port, 1024-65535 (default: 8080)
  --dns IPV4            Override core DNS only (default: Docker DNS settings)
  --examples-dir DIR    Download the LL2S/FRR example into a NEW directory
  --start-example       Import, publish instructions and start that lab (12 nodes)
  --dry-run             Print the plan without host checks or changes
  --help                Show this help
Run as root (sudo sh ...). Docker must already be installed.
Existing containers/volumes are never replaced. This is not an upgrade tool.
For non-resolving DNS, use --dns 127.0.0.199 or another address with no DNS
service. This does not block Internet access and can prevent Agent cloud access.
EOF
}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --with-agent) agent=1; shift ;;
        --with-kvm) kvm=1; shift ;;
        --start-example) start_example=1; shift ;;
        --with-auth) auth=1; shift ;;
        --dry-run) dry_run=1; shift ;;
        --name|--port|--examples-dir|--auth-file|--dns)
            [ "$#" -ge 2 ] && [ -n "$2" ] || die "Missing value for $1"
            case "$1" in
                --name) name=$2 ;;
                --port) port=$2 ;;
                --examples-dir) examples=$2 ;;
                --auth-file) auth_file=$2 ;;
                --dns) dns=$2 ;;
            esac
            shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) die "Unknown option: $1 (use --help)" ;;
    esac
done
case "$name" in ''|[!a-z0-9]*|*[!a-z0-9-]*) die 'Name must use lowercase letters, numbers and hyphens, starting with a letter or number' ;; esac
[ "${#name}" -le 40 ] || die 'Name is limited to 40 characters'
case "$port" in ''|*[!0-9]*|0*) die 'Port must be a decimal number from 1024 to 65535' ;; esac
[ "${#port}" -le 5 ] && [ "$port" -ge 1024 ] && [ "$port" -le 65535 ] || die 'Port must be from 1024 to 65535'
valid_dns_ipv4() (
    case "$1" in *[!0-9.]*|.*|*.|*..*) exit 1 ;; esac
    IFS=.
    set -f
    set -- $1
    [ "$#" = 4 ] || exit 1
    for octet do
        case "$octet" in ''|0[0-9]*) exit 1 ;; esac
        [ "${#octet}" -le 3 ] && [ "$octet" -le 255 ] || exit 1
    done
)
[ -z "$dns" ] || valid_dns_ipv4 "$dns" || die '--dns requires an IPv4 address such as 127.0.0.199'
if [ "$auth" = 1 ]; then
    bind=0.0.0.0
    auth_file=${auth_file:-$PWD/$name-auth.env}
    case "$auth_file" in /*) ;; *) auth_file=$PWD/$auth_file ;; esac
else
    [ -z "$auth_file" ] || die '--auth-file requires --with-auth'
fi
# Make filesystem operands absolute, including names beginning with a dash.
if [ -n "$examples" ]; then
    case "$examples" in /*) ;; *) examples=$PWD/$examples ;; esac
fi

printf 'Weblab %s: %s listening on %s:%s\n' "$VERSION" "$name" "$bind" "$port"
printf 'Images: Alpine, LL2S 0.2.0-ovs-rstp1, Weblab FRR 10.7.1-wl1; Agent=%s; KVM=%s\n' "$agent" "$kvm"
printf 'Persistent volumes: %s-images, %s-data\n' "$name" "$name"
if [ "$agent" = 1 ]; then
    printf 'Agent volumes: %s-agent-sockets, %s-agent-conversations, %s-agent-bridge-state\n' "$name" "$name" "$name"
fi
[ -z "$examples" ] || printf 'Example download: %s\n' "$examples"
[ "$start_example" = 0 ] || printf 'Will start the preconfigured FRR/LL2S lab: 3 routers, 3 switches, 6 PCs.\n'
[ "$auth" = 0 ] || printf 'Authentication on; new key file: %s (HTTP on all IPv4 interfaces).\n' "$auth_file"
[ -z "$dns" ] || printf 'Core DNS override: %s. No network egress isolation; hostname-based Agent providers need working DNS.\n' "$dns"
if [ "$dry_run" = 1 ]; then
    printf 'Dry run: no host checks, downloads or changes performed.\n'
    exit 0
fi

[ "$(id -u)" = 0 ] || die 'Run on the Docker host with sudo sh weblab-install.sh (same options)'
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = x86_64 ] || die 'Linux x86-64 is required'
for command in docker ss; do
    command -v "$command" >/dev/null 2>&1 || die "Install $command first"
done
# Host networking and the mounted socket must address this same local daemon.
[ -z "${DOCKER_HOST:-}" ] && [ -z "${DOCKER_CONTEXT:-}" ] || die 'Unset DOCKER_HOST and DOCKER_CONTEXT; use the local default Docker context'
endpoint=$(docker context inspect --format '{{.Endpoints.docker.Host}}')
[ "$endpoint" = unix:///var/run/docker.sock ] || die 'Select a local Docker context using unix:///var/run/docker.sock'
security=$(docker info --format '{{json .SecurityOptions}}')
case "$security" in *rootless*) die 'Rootful Docker is required' ;; esac
platform=$(docker info --format '{{.OSType}}/{{.Architecture}}')
case "$platform" in linux/x86_64|linux/amd64) ;; *) die 'The Docker server must be Linux amd64' ;; esac
[ -c /dev/net/tun ] || die '/dev/net/tun is missing; enable the host tun module first'
if [ "$kvm" = 1 ]; then
    [ -c /dev/kvm ] || die '/dev/kvm is missing; enable KVM or omit --with-kvm'
fi
listeners=$(ss -H -ltn "sport = :$port")
[ -z "$listeners" ] || die "TCP port $port is already in use"
# List operations must succeed. A failed daemon query must not mean 'absent'.
containers=$(docker container ls -a --format '{{.Names}}')
volumes=$(docker volume ls --format '{{.Name}}')
for existing in $containers; do
    case "$existing" in "$name"|"$name-agent") die "Container $existing already exists; use the documented upgrade procedure" ;; esac
done
for existing in $volumes; do
    case "$existing" in "$name-images"|"$name-data"|"$name-agent-sockets"|"$name-agent-conversations"|"$name-agent-bridge-state")
        die "Volume $existing already exists; refusing to reuse saved state" ;;
    esac
done
if [ -n "$examples" ] || [ "$start_example" = 1 ]; then
    command -v curl >/dev/null 2>&1 || die 'Install curl to download examples'
fi
if [ -n "$examples" ]; then
    [ ! -e "$examples" ] && [ ! -L "$examples" ] || die 'Example destination already exists'
    [ -d "$(dirname "$examples")" ] || die 'Create the parent directory for --examples-dir first'
fi
if [ "$auth" = 1 ]; then
    [ ! -e "$auth_file" ] && [ ! -L "$auth_file" ] || die 'Authentication key file already exists; refusing to overwrite it'
    [ -d "$(dirname "$auth_file")" ] || die 'Create the parent directory for --auth-file first'
    command -v od >/dev/null 2>&1 || die 'Install od to generate authentication keys'
fi
if [ ! -d /sys/module/openvswitch ]; then
    command -v modprobe >/dev/null 2>&1 || die 'Install modprobe and the host Open vSwitch kernel module first'
    modprobe openvswitch || die 'Could not load the host Open vSwitch module'
fi

tmp=
started=0
cleanup() {
    result=$?
    trap - 0
    [ -z "$tmp" ] || rm -rf "$tmp"
    if [ "$result" -ne 0 ] && [ "$started" = 1 ]; then
        printf 'Installation incomplete. Containers and volumes were preserved. Inspect docker logs %s and, if enabled, %s-agent before recovery.\n' "$name" "$name" >&2
    fi
    exit "$result"
}
trap cleanup 0
trap 'exit 130' INT
trap 'exit 143' TERM
if [ -n "$examples" ] || [ "$start_example" = 1 ]; then
    tmp=$(mktemp -d)
    for file in ll2s-frr-vrrp-test.json ll2s-frr-vrrp-test.md; do
        curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
            --connect-timeout 15 --max-time 120 \
            "https://raw.githubusercontent.com/weblab-network/weblab/v$VERSION/examples/$file" -o "$tmp/$file"
    done
fi
# Users may deliberately tag their Alpine tools image as alpine:latest.
if docker image inspect alpine:latest >/dev/null 2>&1; then
    printf 'Keeping the existing alpine:latest image (including any local tools).\n'
else
    docker pull alpine:latest
fi
docker pull "$LL2S_IMAGE"
docker pull "$FRR_IMAGE"
docker pull "ghcr.io/weblab-network/weblab:$VERSION"
if [ "$agent" = 1 ]; then
    docker pull "ghcr.io/weblab-network/weblab-agent:$VERSION"
fi
if [ "$auth" = 1 ]; then
    # Exclusive creation, restrictive permissions, no secrets on stdout/argv.
    (
        umask 077
        set -C
        {
            printf 'WL_AUTH=1\n'
            for account in ADMIN USER GUEST; do
                key=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')
                [ "${#key}" = 48 ] || exit 1
                printf 'WL_AUTH_%s_PASSWORD=Wl1%s\n' "$account" "$key"
            done
        } > "$auth_file"
    ) || die "Could not create complete keys; inspect $auth_file before retrying"
fi
started=1
if [ "$agent" = 1 ]; then
    # Start first: Docker initializes the socket volume with the companion's UID.
    docker run -d --name "$name-agent" --platform linux/amd64 \
        --init --stop-timeout 20 --network none --user 10001:10001 \
        --read-only --cap-drop ALL --security-opt no-new-privileges:true \
        --pids-limit 128 --memory 1g --cpus 1 \
        --tmpfs /tmp:rw,nosuid,nodev,size=256m \
        --tmpfs /workspace:rw,nosuid,nodev,uid=10001,gid=10001,size=32m \
        -v "$name-agent-sockets:/run/weblab-agent" \
        -v "$name-agent-conversations:/home/agent" \
        "ghcr.io/weblab-network/weblab-agent:$VERSION"
fi
set -- docker run -d --name "$name" --platform linux/amd64 --init --stop-timeout 120 \
    --hostname "$name" --add-host "$name:127.0.0.1" \
    --network host --cap-add NET_ADMIN --device /dev/net/tun \
    -e "WL_BIND=$bind" -e "WL_PORT=$port" \
    -v "$name-images:/iou" -v "$name-data:/data" \
    -v /var/run/docker.sock:/var/run/docker.sock
if [ "$agent" = 1 ]; then
    set -- "$@" -e WL_AGENT_SOCKET_DIR=/run/weblab-agent \
        -e WL_AGENT_STATE_DIR=/agent-state \
        -e WL_AGENT_PROVIDERS=http://127.0.0.1:11434/v1 \
        -v "$name-agent-sockets:/run/weblab-agent" \
        -v "$name-agent-bridge-state:/agent-state"
fi
[ "$kvm" = 0 ] || set -- "$@" --device /dev/kvm
[ "$auth" = 0 ] || set -- "$@" --env-file "$auth_file"
[ -z "$dns" ] || set -- "$@" --dns "$dns"
"$@" "ghcr.io/weblab-network/weblab:$VERSION"
printf 'Waiting for Weblab startup...\n'
docker exec "$name" python3 -c '
import sys, time, urllib.request
for attempt in range(30):
    try:
        urllib.request.urlopen("http://127.0.0.1:" + sys.argv[1] + "/health", timeout=2).close()
        break
    except Exception:
        time.sleep(1)
else:
    sys.exit("Weblab did not become ready; inspect container logs.")
' "$port"
if [ "$start_example" = 1 ]; then
    printf 'Importing and starting the example; initial device startup can take several minutes...\n'
    docker cp "$tmp/." "$name:/tmp/weblab-example"
    docker exec "$name" python3 -c '
import http.cookiejar, json, os, pathlib, sys, urllib.error, urllib.request
base = "http://127.0.0.1:" + sys.argv[1]
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
csrf = ""
def request(path, data=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(base+path, data=body, method=method,
                                 headers={"Content-Type": "application/json", "X-Weblab-Login": "1", "X-Weblab-CSRF": csrf})
    try:
        with opener.open(req, timeout=1800 if path == "/api/lab/start" else 600) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = ""
        with exc:
            raw = exc.read(16384)
        if not path.startswith("/api/auth/"):
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict) and isinstance(payload.get("error"), str):
                    detail = ": " + payload["error"]
            except (ValueError, UnicodeError):
                pass
        sys.exit(f"Weblab {req.get_method()} {path} failed (HTTP {exc.code}){detail}. "
                 "Existing containers and data were preserved; inspect the workspace before retrying.")
if os.environ.get("WL_AUTH") == "1":
    login = request("/api/auth/login", {"name": "admin", "password": os.environ["WL_AUTH_ADMIN_PASSWORD"]}, "POST")
    csrf = login["csrf"]
    import atexit
    atexit.register(lambda: request("/api/auth/logout", {}, "POST"))
state = request("/api/state")
if state["topology"]["nodes"] or state["topology"]["links"]:
    sys.exit("Workspace is no longer empty; refusing to replace it.")
instructions = request("/api/instructions")
if instructions.get("file"):
    sys.exit("Instructions already exist; refusing to replace them.")
directory = pathlib.Path("/tmp/weblab-example")
topology = json.loads((directory/"ll2s-frr-vrrp-test.json").read_text())
request("/api/topology", topology, "PUT")
request("/api/instructions", {"revision": instructions["revision"], "file": {
    "name": "ll2s-frr-vrrp-test.md", "text": (directory/"ll2s-frr-vrrp-test.md").read_text()}}, "PUT")
state = request("/api/lab/start", {}, "POST")
if len(state["status"]) != len(topology["nodes"]) or any(
        node["state"] != "running" for node in state["status"].values()):
    sys.exit("Not all example devices are running. Inspect the workspace and logs.")
print("Example devices are running. Allow time for OSPF, RSTP and VRRP convergence.")
' "$port"
fi
if [ -n "$examples" ]; then
    mkdir "$examples"
    cp "$tmp/ll2s-frr-vrrp-test.json" "$tmp/ll2s-frr-vrrp-test.md" "$examples/"
fi
printf '\nWeblab is ready: http://127.0.0.1:%s/\n' "$port"
printf 'From another workstation: ssh -L %s:127.0.0.1:%s USER@LAB_HOST\n' "$port" "$port"
printf 'Logs: docker logs -f %s\n' "$name"
printf 'Save guest configurations, then stop the core: docker stop %s\n' "$name"
if [ "$agent" = 1 ]; then
    printf 'Then stop the companion: docker stop %s-agent\n' "$name"
    printf 'Agent > Settings: choose OpenAI or host Ollama at http://127.0.0.1:11434/v1.\n'
fi
if [ -n "$examples" ]; then
    printf 'Example files saved in %s (import manually when --start-example was omitted).\n' "$examples"
fi
[ "$start_example" = 1 ] || printf 'No lab devices were started.\n'
if [ "$auth" = 1 ]; then
    printf 'Login accounts: admin, user, guest. Keys saved in %s (mode 600).\n' "$auth_file"
    printf 'LAN access: http://HOST_IP:%s/ (all IPv4 interfaces; HTTP, not HTTPS).\n' "$port"
    printf 'Keep access on a trusted network or use a TLS reverse proxy; the installer does not configure a firewall.\n'
else
    printf 'Authentication is off; access is localhost only.\n'
fi
printf 'Keep named volumes for persistence. Load openvswitch again after host reboot.\n'
printf 'Upgrades/authentication: https://github.com/weblab-network/weblab/blob/v%s/docs/installation.md\n' "$VERSION"
