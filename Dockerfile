# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

# Pinned helm CLI version bundled into the image for `helm package` / `helm repo index`.
ARG HELM_VERSION=v3.15.3
ARG TARGETARCH=amd64

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CACHE_DIR=/var/cache/helm-proxy \
    HOME=/home/app

# git + ca-certificates are required at runtime; helm is downloaded below.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends git ca-certificates curl; \
    curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-${TARGETARCH}.tar.gz" \
        -o /tmp/helm.tgz; \
    tar -xzf /tmp/helm.tgz -C /tmp; \
    mv "/tmp/linux-${TARGETARCH}/helm" /usr/local/bin/helm; \
    chmod +x /usr/local/bin/helm; \
    rm -rf /tmp/helm.tgz "/tmp/linux-${TARGETARCH}"; \
    apt-get purge -y curl; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*; \
    helm version

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Non-root user; owns HOME (helm writes config there) and the cache dir.
RUN set -eux; \
    useradd --create-home --home-dir /home/app --uid 1001 app; \
    mkdir -p /var/cache/helm-proxy; \
    chown -R app:app /var/cache/helm-proxy /home/app

# Numeric UID so Kubernetes runAsNonRoot can enforce non-root.
USER 1001

EXPOSE 7713

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; \
    sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7713/health').status==200 else 1)"

CMD ["python", "-m", "app"]
