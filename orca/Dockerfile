# Orca publishes no container image, so this builds one from the
# pinned upstream release AppImage, following the official headless
# Linux server guide (docs/reference/headless-linux-server.md).

FROM ubuntu:24.04
ARG ORCA_VERSION
ARG TARGETARCH
ENV DEBIAN_FRONTEND=noninteractive
ENV CODEX_UNSAFE_ALLOW_NO_SANDBOX=1

# Install GitHub CLI
RUN (type -p wget >/dev/null || (apt-get update &&  apt-get install wget -y)) \
	&& mkdir -p -m 755 /etc/apt/keyrings \
	&& out=$(mktemp) && wget -nv -O$out https://cli.github.com/packages/githubcli-archive-keyring.gpg \
	&& cat $out | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
	&& chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
	&& mkdir -p -m 755 /etc/apt/sources.list.d \
	&& echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
	&& apt-get update \
	&& apt-get install gh -y

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash ca-certificates curl file git jq openssh-client procps \
      xvfb zlib1g-dev libgtk-3-0t64 libpango-1.0-0 libcairo2 \
      libnss3 libgbm1 libdrm2 libasound2t64 libcups2t64 libxkbcommon0 \
      libatk1.0-0t64 libatk-bridge2.0-0t64 libatspi2.0-0t64 \
      libx11-xcb1 libxcb-dri3-0 libxcomposite1 libxdamage1 libxfixes3 \
      libxrandr2 libxrender1 libxss1 libxtst6 \
    && rm -rf /var/lib/apt/lists/*

RUN case "$TARGETARCH" in \
      amd64) asset=orca-linux.AppImage ;; \
      arm64) asset=orca-linux-arm64.AppImage ;; \
      *) echo "Unsupported target architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac \
    && curl -fL --retry 3 -o /tmp/orca.AppImage \
      "https://github.com/stablyai/orca/releases/download/$ORCA_VERSION/$asset" \
    && chmod +x /tmp/orca.AppImage \
    && cd /opt && /tmp/orca.AppImage --appimage-extract >/dev/null \
    && mv /opt/squashfs-root /opt/orca \
    && chmod -R a+rX /opt/orca \
    && rm /tmp/orca.AppImage


RUN useradd --create-home --shell /bin/bash orca

COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/orca-entrypoint

USER orca
WORKDIR /home/orca
ENV PATH=/home/orca/.local/bin:/opt/orca/resources/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LIBGL_ALWAYS_SOFTWARE=1


EXPOSE 6768

# Chromium cannot create its sandbox namespaces in an unprivileged
# container; the container is the isolation boundary instead.
ENTRYPOINT ["/usr/local/bin/orca-entrypoint"]
