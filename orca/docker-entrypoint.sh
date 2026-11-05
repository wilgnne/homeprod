#!/usr/bin/env bash
set -euo pipefail
# Start the system bus before dropping privileges for the application.
if [[ "$(id -u)" == 0 ]]; then
  mkdir -p /run/dbus
  dbus-uuidgen --ensure
  dbus-daemon --system --fork --nopidfile
  exec gosu orca "$0" "$@"
fi

curl -fsSL https://chatgpt.com/codex/install.sh \
  | CODEX_NON_INTERACTIVE=1 \
    CODEX_RELEASE="${CODEX_VERSION:-latest}" \
    sh

codex --version

if [[ -n "${GIT_USER_NAME:-}" ]]; then
  git config --global user.name "$GIT_USER_NAME"
fi

if [[ -n "${GIT_USER_EMAIL:-}" ]]; then
  git config --global user.email "$GIT_USER_EMAIL"
fi

exec dbus-run-session -- /opt/orca/AppRun --no-sandbox "$@"
