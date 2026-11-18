# Operations, backup, restore planning and troubleshooting

**Applies to:** upWATCH! `1.0.0-rc1`, fresh Debian 13 deployment (standalone or Proxmox LXC).

## 1. Service lifecycle

upWATCH! has two separate application systemd services and local PostgreSQL:

| Service | Function |
| --- | --- |
| `postgresql` | Database |
| `upwatch-web` | UI, authentication, API and report generation |
| `upwatch-worker` | Network checks, notifications, aggregation and retention |

```bash
cat /opt/upwatch/VERSION
systemctl status postgresql upwatch-web upwatch-worker --no-pager
systemctl is-enabled upwatch-web upwatch-worker
journalctl -u upwatch-web --since '15 minutes ago' --no-pager
journalctl -u upwatch-worker --since '15 minutes ago' --no-pager
```

If the installer was told **not** to start the worker for an isolated test system, it is normal for `upwatch-worker` to be inactive. An inactive worker means **no fresh checks, mail processing or background cleanup**.

**Intentional worker control only:**

```bash
# Stop checks and notifications:
systemctl disable --now upwatch-worker

# Enable checks and notifications (only when operationally intended):
systemctl enable --now upwatch-worker
```

Do not enable cloned workers that point to real production endpoints and SMTP recipients without reviewing duplicate-check and duplicate-alert risks.

## 2. Health and version checks

```bash
cat /opt/upwatch/VERSION
runuser -u postgres -- pg_isready -h /var/run/postgresql
runuser -u upwatch -- /opt/upwatch/.venv/bin/python /opt/upwatch/health_probe.py
id -g upwatch
sysctl net.ipv4.ping_group_range
runuser -u upwatch -- ping -n -c 2 -W 2 127.0.0.1
date -u
timedatectl status
```

`upwatch-web` may work correctly while monitoring data is stale if the worker is stopped. Check both services and freshness indicators before trusting green widgets.

## 3. Backup — data and secrets belong together

A recoverable application backup needs:

1. The PostgreSQL **`upwatch`** database, preferably in custom-format dump form.
2. `/etc/upwatch/upwatch.env` (network and DB settings).
3. **`/etc/upwatch/vault.key`** (needed to decrypt stored credentials).
4. The application version and any local service/firewall/reverse-proxy modifications.

Example, run **as root** on the installed Debian system:

```bash
install -d -m 0700 /root/upwatch-backup
runuser -u postgres -- pg_dump -Fc upwatch > /root/upwatch-backup/upwatch.dump
cp -p /etc/upwatch/upwatch.env /etc/upwatch/vault.key /root/upwatch-backup/
cp -p /opt/upwatch/VERSION /root/upwatch-backup/VERSION
chmod -R go-rwx /root/upwatch-backup
ls -lh /root/upwatch-backup/
```

`pg_dump` success and a non-empty file are **not** proof of a successful restore. Backups should be encrypted at rest, stored off-host as appropriate and periodically restored to an isolated system. Avoid including credentials in public diagnostic bundles.

### Restore acceptance — isolated test environment

For a controlled recovery test, use a **separate** Debian 13 host/container, disconnected from production alerting. Preserve the correct application build, database dump, environment and vault key. Recreate the database/service prerequisites as appropriate for that recovery environment and restore the dump using PostgreSQL tools such as `pg_restore`.

**Do not restore production data into a live server or run a restored clone's worker without explicit authorization.** Validate login, MFA, decrypted SNMP/SMTP credentials, monitor/group/tag assignments, histories, dependencies and reports before declaring the backup usable.

There is **no universal automated restore command supplied as part of this baseline release**; the exact database/role recreation procedure must be adapted and separately tested for your environment. The new-install script is **not** a restore utility.

## 4. Post-installation functional acceptance

| Test | Expected result |
| --- | --- |
| Admin login + MFA | Login and one-time code accepted; recovery codes secured |
| ICMP after reboot | `upwatch` service user can still ping a permitted host |
| HTTP/TCP probe | Both successful and failing conditions correctly recorded |
| UDP DNS/NTP | Valid reply reports success; silent/filtered destination is **not** falsely UP |
| Roles | Viewer cannot edit; Site Manager cannot manage foreign monitors or global settings |
| Groups/tags | Assigned scope matches visibility preview and direct API access restrictions |
| Dependencies | Two independent parent groups suppress only applicable downstream alarms |
| SMTP | Test delivery appears in outbox; failures are visible |
| Maintenance | Checks continue, intended alarm suppression is applied |
| Availability | 7/30/90/365-day report opens; CSV exports; results are clearly identified as check-success ratios |
| Audit | Administrative actions appear; admin CSV export works |
| DE/EN | Login and dashboard switch languages; layout remains responsive |
| SNMPv3 | **Not considered acceptance-complete until real-device testing** |

## 5. Typical problems and investigation

### Web UI unavailable

```bash
systemctl status upwatch-web postgresql --no-pager
journalctl -u upwatch-web -n 80 --no-pager
runuser -u postgres -- pg_isready -h /var/run/postgresql
```

Check host/guest firewall, TCP 8080, the installed web unit's bind address, configured hostname and reverse proxy. Localhost mode binds to loopback; LAN mode requires an approved client.

### HTTP 400/403/421 or proxy errors

Check the exact public hostname, `UPWATCH_DOMAIN`, the source IP the application **actually sees** for the trusted proxy, and whether the proxy replaces forwarded headers. Do not simply trust all source IPs to make an error disappear.

### ICMP succeeds as root but not as `upwatch`

```bash
id -g upwatch
sysctl net.ipv4.ping_group_range
runuser -u upwatch -- ping -n -c 2 -W 2 127.0.0.1
```

The installer automatically handles the common Debian default policy. A custom policy or Proxmox host restriction needs operator review, not blanket privileged mode.

### MFA codes fail

```bash
date -u
timedatectl status
```

Check the **actual UTC time** on the machine. For LXC check the Proxmox **host** clock. A correct local `Europe/Berlin` setting alone does not ensure accurate TOTP codes.

### A monitoring destination is blocked

Check the destination hostname/address and **`MONITOR_ALLOWED_CIDRS`**. Browser allowlists are separate and do not grant monitoring destinations. A hostname resolving to additional IPv4/IPv6 addresses may fail policy checks.

### No monitoring updates

```bash
systemctl is-active upwatch-worker
journalctl -u upwatch-worker --since '15 minutes ago' --no-pager
```

The worker may have been intentionally disabled at install time, or stopped by systemd policy. Stale measurements should not be treated as live UP states.

### No notification email

Verify outgoing SMTP settings, credentials, TLS mode, allowed recipients, per-monitor DOWN/UP flags and the **outbox delivery status**. A sent test probe to an SMTP **service monitor** is different from an outbound notification test.

### Failed SNMPv3 probe

Validate target, UDP 161/firewall, profile, credentials, authentication/privacy settings and OID/interface index. **Real-world SNMPv3 behavior is not yet sufficiently validated in this RC**; do not interpret a failure as proof of a faulty network device without independently checking it.

## 6. Release boundaries

- This package is for **fresh Debian 13 + systemd installations only**; no updater exists.
- Proxmox LXC requires correct host networking/time policies.
- SNMPv3 has insufficient real-device end-to-end testing.
- Monitoring check-success percentages are **not a time-weighted SLA availability guarantee**.
- This release is **not a certified security or high-availability product**.
- Choose a repository licence and a security contact before public distribution; neither is assumed in this documentation.
