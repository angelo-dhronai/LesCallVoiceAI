#!/usr/bin/env bash
# Deploy the lescall dashboard: API as a systemd service + nginx site for the SPA.
# Usage: sudo bash deploy/deploy_dashboard.sh <FQDN>
#   e.g. sudo bash deploy/deploy_dashboard.sh platform.lescall.ai
set -euo pipefail

FQDN="${1:?usage: deploy_dashboard.sh <FQDN>}"
REPO=/home/angelo/lescall

echo "==> Building frontend"
(cd "$REPO/dashboard/web" && npm install --no-audit --no-fund && npm run build)

echo "==> Installing dashboard API systemd service"
install -m644 "$REPO/deploy/lescall-dashboard-api.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lescall-dashboard-api
sleep 2
curl -fsS http://127.0.0.1:8080/api/health && echo " <- API healthy"

echo "==> Installing nginx site for $FQDN"
sed "s/__SERVER_NAME__/$FQDN/g" "$REPO/deploy/nginx-platform.conf" \
    > /etc/nginx/sites-available/lescall-platform
ln -sf /etc/nginx/sites-available/lescall-platform /etc/nginx/sites-enabled/lescall-platform
nginx -t
systemctl reload nginx
echo "==> Done. Dashboard should be live at http://$FQDN"
echo "    For TLS:  sudo certbot --nginx -d $FQDN"
