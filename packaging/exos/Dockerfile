ARG WEBLAB_IMAGE=ghcr.io/weblab-network/weblab:latest
FROM ${WEBLAB_IMAGE}
COPY packaging/exos/initialize.py /app/initialize_exos_demo.py
COPY packaging/exos/image.json packaging/exos/build/EXOS-VM_33.1.1.31.qcow2 /opt/weblab/exos-demo/
COPY examples/exos-demo.json /opt/weblab/exos-demo/topology.json
COPY examples/exos-demo.md /opt/weblab/exos-demo/exercise.md
COPY licenses/Virtual-EXOS.txt /opt/weblab/exos-demo/LICENSE.txt
ENTRYPOINT ["python3", "/app/initialize_exos_demo.py"]
CMD ["--image-dir", "/iou", "--data-dir", "/data"]
