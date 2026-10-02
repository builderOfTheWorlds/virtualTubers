# Shared GPU Piper TTS service (docs/tts_gpu_service.md).
#
# python:3.12 + glibc >= 2.34 because onnxruntime-gpu's aarch64 (GB10 / DGX
# Spark) wheels are cp311+ manylinux_2_34 only — the worker image (Ubuntu
# 22.04, python3.10) cannot install them. CUDA 13 runtime + cuDNN 9 come from
# the onnxruntime-gpu[cuda,cudnn] pip extras (nvidia-* wheels); the driver
# (libcuda) is injected by the NVIDIA container runtime (`gpus: all`). No
# CUDA base image needed. Verified on GB10 (sm_121, driver 580, CUDA 13.0)
# with onnxruntime-gpu 1.30.0.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# piper-tts pulls the CPU `onnxruntime` wheel as a dependency; both wheels
# install into the same `onnxruntime` package dir, so remove the CPU one and
# (re)install the GPU one last, or the CPU build can shadow it.
RUN pip install "piper-tts==1.8.0" \
    && pip uninstall -y onnxruntime \
    && pip install "onnxruntime-gpu[cuda,cudnn]==1.30.0" \
    && python -c "import onnxruntime as o; assert 'CUDAExecutionProvider' in o.get_available_providers(), o.get_available_providers()"

COPY server.py /app/server.py

EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["python", "/app/server.py"]
CMD ["--data-dir", "/data/voices", "--port", "5000"]
