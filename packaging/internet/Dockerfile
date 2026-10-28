FROM alpine:3.23@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0 AS packages
RUN apk add --no-cache python3 iproute2 iptables dnsmasq
RUN mkdir -p /usr/share/weblab-internet && cp /lib/apk/db/installed /usr/share/weblab-internet/apk-installed.txt

FROM packages AS runtime
ARG WEBLAB_VERSION=0.11.0-dev
ARG WEBLAB_REVISION=unpublished
LABEL network.weblab.internet="1"
LABEL network.weblab.internet.firewall="1"
LABEL network.weblab.internet.firewall.live="1"
LABEL network.weblab.internet.port-forwards="1"
LABEL org.opencontainers.image.source="https://github.com/weblab-network/weblab" \
      org.opencontainers.image.version="$WEBLAB_VERSION" \
      org.opencontainers.image.revision="$WEBLAB_REVISION"
COPY internet_cloud.py /opt/weblab/internet_cloud.py
COPY packaging/internet/gateway.py /opt/weblab/gateway.py
COPY LICENSE THIRD_PARTY.md /usr/local/share/doc/weblab-internet/
COPY packaging/internet/NOTICE /usr/share/weblab-internet/NOTICE
COPY licenses/iou2net-GPL-2.0.txt /usr/share/weblab-internet/licenses/GPL-2.0.txt
COPY internet_cloud.py LICENSE THIRD_PARTY.md /usr/share/weblab-internet/build/
COPY packaging/internet/Dockerfile packaging/internet/Dockerfile.release packaging/internet/README.md packaging/internet/SOURCES.md packaging/internet/NOTICE packaging/internet/collect_sources.py packaging/internet/gateway.py /usr/share/weblab-internet/build/packaging/internet/
COPY licenses/iou2net-GPL-2.0.txt /usr/share/weblab-internet/build/licenses/iou2net-GPL-2.0.txt
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/opt/weblab/gateway.py"]

# Local builds stay quick on both classic Docker and BuildKit.
FROM runtime AS development
