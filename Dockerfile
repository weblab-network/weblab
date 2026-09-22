FROM docker:28-cli AS docker-cli
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 perl libnet-pcap-perl iproute2 ca-certificates \
       libc6-i386 lib32gcc-s1 lib32stdc++6 lib32z1 qemu-system-x86 qemu-utils ovmf mtools gzip \
    && rm -rf /var/lib/apt/lists/*

# Only the client: PC containers are managed by the host's Docker Engine.
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /app
COPY frr.py tap_net.py disk_delta.py lab_server.py link_fabric.py iol_l1.py qmp.py lab_backup.py console_capture.py cisco_config.py saved_config.py initial_config.py vios.py qemu_net.py start-lab.sh wrapper.pl wrapper-ws.pl iou2net.pl iol-console.html ./
COPY web/ ./web/
COPY LICENSE THIRD_PARTY.md ./
COPY licenses/ ./licenses/
RUN chmod +x start-lab.sh wrapper.pl wrapper-ws.pl \
    && mkdir -p /iou /data
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
STOPSIGNAL SIGTERM
ENTRYPOINT ["./start-lab.sh"]
CMD ["--image-dir", "/iou", "--data-dir", "/data"]
