# SECURITY P0 CHECKLIST

## 1) FastAPI Environment

- [ ] Set production env vars:
  - [ ] `APP_ENV=production`
  - [ ] `JWT_SECRET_KEY=<long-random-secret>`
  - [ ] `ALLOWED_ORIGINS=https://www.pricer3d.top,https://pricer3d.top`
  - [ ] `AUTH_RATE_LIMIT_PER_MIN=12`
  - [ ] `QUOTE_RATE_LIMIT_PER_MIN=30`

## 2) Uvicorn Binding

- [ ] Start uvicorn with loopback only:
  - [ ] `uvicorn main:app --host 127.0.0.1 --port 5000`
- [ ] Confirm port `5000` is not exposed to the public network.

## 3) Nginx Edge Security

- [ ] Use `deploy/nginx_pricer3d.conf` as baseline.
- [ ] Keep API path rate-limited (`limit_req`).
- [ ] Validate config and reload:
  - [ ] `sudo nginx -t`
  - [ ] `sudo systemctl reload nginx`

## 4) Firewall Rules

- [ ] Keep only required inbound ports open:
  - [ ] `80/tcp`
  - [ ] `443/tcp`
  - [ ] `22/tcp` (recommended: allowlist your office/home IP only)
- [ ] Ensure `5000/tcp` is blocked from public access.

## 5) Certbot Auto Renewal

- [ ] Add cron from `deploy/certbot_renew.cron`.
- [ ] Verify renewal workflow:
  - [ ] `sudo certbot renew --dry-run`

## 6) Dependency Security

- [ ] Weekly check:
  - [ ] `pip list --outdated`
  - [ ] `pip-audit`

## 7) Backup & Restore Drill

- [ ] Create backups daily:
  - [ ] `bash deploy/backup_app_db.sh`
- [ ] Monthly restore drill (in a staging environment):
  - [ ] Restore from a backup: `bash deploy/restore_app_db.sh /path/to/backup.sqlite`
  - [ ] Confirm app can start and login works

## 8) Audit Logs

- [ ] Confirm audit events are written for:
  - [ ] login/register
  - [ ] user settings updates
  - [ ] quote creation and idempotent replays
