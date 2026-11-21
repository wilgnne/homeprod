FROM ubuntu:24.04 AS download

ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# SHA-256 values from https://releases.herdr.dev/core/0.1.21/manifest.json
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) arch=x86_64; sha=78424755c10a40dde38a0bdb55c20ac19706e9ae05ed68fc2acf1fcff0d941c1 ;; \
      arm64) arch=aarch64; sha=e844a2bfab48a226097679d076bc83042fe57fccef5387ef802e5b24e78b529a ;; \
      *) echo "Unsupported target architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --retry 3 -o /tmp/herdr-core.tar.gz \
      "https://releases.herdr.dev/core/0.1.21/herdr-core-linux-$arch.tar.gz"; \
    printf '%s  %s\n' "$sha" /tmp/herdr-core.tar.gz | sha256sum -c -; \
    tar -xzf /tmp/herdr-core.tar.gz -C /tmp herdr-core; \
    install -m 0755 /tmp/herdr-core /usr/local/bin/herdr-core; \
    /usr/local/bin/herdr-core --version

FROM ubuntu:24.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates git openssh-server \
    && rm -rf /var/lib/apt/lists/* /etc/ssh/ssh_host_* \
    && useradd --create-home --shell /bin/bash herdr \
    && install -d -m 0700 /var/lib/herdr-ssh \
    && install -d -m 0700 -o herdr -g herdr /home/herdr/workspaces

COPY --from=download /usr/local/bin/herdr-core /usr/local/bin/herdr-core
RUN ln -s herdr-core /usr/local/bin/hc

COPY sshd_config /etc/ssh/sshd_config.herdr
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/herdr-entrypoint

EXPOSE 22
WORKDIR /home/herdr
ENTRYPOINT ["/usr/local/bin/herdr-entrypoint"]
