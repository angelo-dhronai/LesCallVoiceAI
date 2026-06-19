#!/bin/bash
# Runs after a successful Let's Encrypt renewal. nginx is containerized, so the
# new cert (mounted from /etc/letsencrypt) is picked up by restarting the web container.
set -e
cd /home/angelo/lescall
docker compose restart web || docker restart lescall-web-1
