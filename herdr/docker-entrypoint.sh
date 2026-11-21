#!/bin/sh
set -eu

authorized_keys=/run/herdr/authorized_keys
if [ ! -f "$authorized_keys" ] || ! ssh-keygen -lf "$authorized_keys" >/dev/null; then
    echo "Mount a valid authorized_keys file at $authorized_keys" >&2
    exit 1
fi

install -d -m 0755 /run/sshd
# OpenSSH reads this file as the login user. Only root can change it.
install -m 0644 "$authorized_keys" /etc/ssh/herdr_authorized_keys

host_key=/var/lib/herdr-ssh/ssh_host_ed25519_key
if [ ! -e "$host_key" ]; then
    ssh-keygen -q -t ed25519 -N '' -f "$host_key"
fi

/usr/sbin/sshd -t -f /etc/ssh/sshd_config.herdr
exec /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config.herdr
