FROM alpine:3.23
RUN apk add --no-cache python3 iproute2 iptables dnsmasq
LABEL network.weblab.internet="1"
COPY internet_cloud.py /opt/weblab/internet_cloud.py
COPY packaging/internet/gateway.py /opt/weblab/gateway.py
COPY LICENSE THIRD_PARTY.md /usr/local/share/doc/weblab-internet/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/opt/weblab/gateway.py"]
