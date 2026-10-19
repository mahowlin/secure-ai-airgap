#!/usr/bin/env bash
# Invoked by Packer's final shutdown session, after all provisioning/validation.
set -euo pipefail
build_user=${1:?build account required}
[[ "$build_user" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || { echo 'Invalid build account' >&2; exit 2; }
[[ $(id -u) -eq 0 ]] || { echo 'Finalization requires root' >&2; exit 2; }
# Explicit site public keys remain; the temporary password never survives conversion.
usermod --password '!' "$build_user"
# Autoinstall copies contain the temporary build hash; omit them from the template.
rm -rf -- /var/log/installer /var/lib/cloud/instances /var/lib/cloud/seed
install -d -m 0755 /etc/ssh/sshd_config.d
printf '%s\n' 'PasswordAuthentication no' 'KbdInteractiveAuthentication no' \
  > /etc/ssh/sshd_config.d/00-endpoint-key-only.conf
chmod 0644 /etc/ssh/sshd_config.d/00-endpoint-key-only.conf
rm -f -- "$0"
shutdown -P now
