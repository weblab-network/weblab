FROM docker:28-cli AS docker-cli
FROM debian:bookworm-slim

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       python3 perl libnet-pcap-perl iproute2 ca-certificates tshark util-linux openssh-client \
    gzip \
 && case "$(dpkg --print-architecture)" in \
      amd64) DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libc6-i386 lib32gcc-s1 lib32stdc++6 lib32z1 qemu-system-x86 qemu-utils ovmf mtools ;; \
      arm64) ;; \
      *) echo 'Weblab supports amd64 and the ARM64 container-only profile' >&2; exit 1 ;; \
    esac \
    && rm -rf /var/lib/apt/lists/*

# Only the client: PC containers are managed by the host's Docker Engine.
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /app
COPY external_access.py external_tty.py internet_cloud.py traffic_capture.py web_auth.py agent_bridge.py agent_transport.py agent_attachments.py storage.py lab_automation.py container_console.py ll2s_device.py frr.py tap_net.py disk_delta.py lab_server.py link_fabric.py iol_l1.py qmp.py lab_backup.py console_capture.py console_evidence.py cisco_config.py saved_config.py initial_config.py vios.py qemu_net.py start-lab.sh wrapper.pl wrapper-ws.pl iou2net.pl iol-console.html ./
COPY web/ ./web/
COPY docs/practice-labs.md ./docs/
COPY LICENSE THIRD_PARTY.md ./
COPY licenses/ ./licenses/
RUN chmod +x start-lab.sh wrapper.pl wrapper-ws.pl \
    && mkdir -p /iou /data
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
STOPSIGNAL SIGTERM
ENTRYPOINT ["./start-lab.sh"]
CMD ["--image-dir", "/iou", "--data-dir", "/data"]
