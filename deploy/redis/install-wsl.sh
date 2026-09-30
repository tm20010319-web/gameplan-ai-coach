#!/usr/bin/env bash
# Run as root in Ubuntu after Windows WSL installation.
set -euo pipefail
test "$(id -u)" -eq 0
if [ -f /etc/systemd/system/gameplan-redis.service ]; then
    systemctl enable --now gameplan-redis.service
    exit 0
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y curl gpg ca-certificates lsb-release
curl -fsSL https://packages.redis.io/gpg | gpg --dearmor --yes -o /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -cs) main" > /etc/apt/sources.list.d/gameplan-redis.list
apt-get update
redis_version=$(apt-cache madison redis-server | awk '!found && $3 ~ /^([0-9]+:)?8\./ {print $3; found=1}')
test -n "$redis_version"
# Refuse to reconfigure another Redis installation.
if dpkg-query -W redis-server >/dev/null 2>&1; then
    echo 'An existing Redis package is installed; inspect it before configuring GAMEPLAN.' >&2
    exit 1
fi
apt-get install -y "redis-server=$redis_version" "redis-tools=$redis_version"
systemctl disable --now redis-server.service
install -d -o redis -g redis -m 750 /var/lib/gameplan-redis
install -o redis -g redis -m 640 /dev/null /var/log/gameplan-redis.log
install -o redis -g redis -m 640 "$(dirname "$0")/redis.conf" /etc/redis/gameplan.conf
if [ -f /etc/redis/gameplan-password.conf ]; then
    cat /etc/redis/gameplan-password.conf >> /etc/redis/gameplan.conf
fi
cat > /etc/systemd/system/gameplan-redis.service <<'SERVICE'
[Unit]
Description=GAMEPLAN Redis 8 persistent store
After=network.target
[Service]
User=redis
Group=redis
ExecStart=/usr/bin/redis-server /etc/redis/gameplan.conf
Restart=on-failure
TimeoutStopSec=120
[Install]
WantedBy=multi-user.target
SERVICE
systemctl daemon-reload
systemctl enable --now gameplan-redis.service
