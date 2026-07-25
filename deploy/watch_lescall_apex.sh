#!/usr/bin/env bash
# One-shot DNS watcher for the 2026-07-25 apex move: waits for lescall.com to
# resolve to this VM, then issues the Let's Encrypt cert and lets
# certbot --nginx wire HTTPS + redirect into the lescall.com vhost
# (deploy/nginx-lescall.com.conf, installed at sites-enabled/lescall.com).
# Safe to re-run; exits 0 once TLS is live, 1 on deadline/certbot failure.
set -u
TARGET_IP="35.207.225.237"
DEADLINE=$(( $(date +%s) + 86400 ))   # give up after 24h
log() { echo "[$(date '+%F %T')] $*"; }

while :; do
  (( $(date +%s) > DEADLINE )) && { log "deadline reached, giving up"; exit 1; }
  apex=$(getent ahostsv4 lescall.com | awk '{print $1; exit}')
  [[ "${apex:-}" == "$TARGET_IP" ]] && break
  log "apex still ${apex:-unresolved}, waiting"
  sleep 60
done
log "apex points here — issuing cert"

doms=(-d lescall.com)
www=$(getent ahostsv4 www.lescall.com | awk '{print $1; exit}')
[[ "${www:-}" == "$TARGET_IP" ]] && doms+=(-d www.lescall.com)

sudo certbot --nginx "${doms[@]}" --redirect -n --agree-tos || exit 1
sudo nginx -t && sudo systemctl reload nginx
log "TLS live for: ${doms[*]}"
