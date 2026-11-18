# upWATCH! v1.0.0 RC1 — Installation Guide

This release is a **fresh-install-only baseline** for Debian GNU/Linux **13** with systemd. It does **not** upgrade an existing upWATCH! deployment. The installed application reports `1.0.0-rc1`.

## 1. Requirements

- A clean Debian 13 host, VM, or Proxmox LXC with systemd and an interactive root shell
- Working DNS, Debian APT repositories, and connectivity to PyPI
- TCP **8080** for the local application, accessible only from a trusted network or HTTPS reverse proxy
- Outbound connectivity according to the chosen monitor protocols (TCP, UDP, ICMP, etc.)
- A **synchronized system clock** for TOTP/MFA; in LXC the **Proxmox host** is responsible for clock synchronization

See [REQUIREMENTS.md](REQUIREMENTS.md), [Standalone Linux](docs/INSTALL_LINUX.md), and [Proxmox LXC](docs/INSTALL_PROXMOX_LXC.md).

## 2. Fresh installation

As root on a **clean** Debian 13 system:

```bash
cd /root
unzip upWatch-rc1-v1.0.0-DE-EN-clear-wizard.zip
cd upwatch
sha256sum -c SHA256SUMS.txt
bash install.sh
```

The installer verifies the OS, sets up dependencies, and **checks local/UTC time and NTP status**. It offers an optional IANA time-zone configuration (e.g. `Europe/Berlin` or `UTC`). If clock synchronization cannot be confirmed, a warning requires explicit acknowledgment. **Time-zone selection does not correct clock drift.**

Before starting the application, the installer also walks through the **network and monitoring setup**:

1. Choose one of four access modes: **localhost only**, **local subnet / selected client IPs**, **HTTPS reverse proxy only**, or **local subnet + HTTPS reverse proxy**.
2. For LAN access, enter the server private IP and add permitted client IPs or CIDRs one by one. Finish each list with `Add another? [y/N]` (default **No**). For proxy access, enter the public hostname and trusted proxy IPs/CIDRs in the same way. Combined access configures both independently.
3. Add permitted monitoring target IPs/CIDRs separately, one at a time; press Enter without an entry to keep the default policy.
4. Review the **STEP 5/5** summary and choose **Y** to apply, **r** to re-enter the network settings, or **q** to cancel. The installer then writes `/etc/upwatch/upwatch.env` with restrictive file permissions.
5. Automatically verify ICMP as the `upwatch` service user. If ping is blocked by the standard Debian `65534 65534` group range, the installer applies a narrow, persistent rule for the actual `upwatch` GID, then retests. It never grants broad network capabilities or widens a custom group range. If the container kernel refuses the change or the active policy is custom, installation stops with a specific error.
6. Choose whether to **enable the worker**. For a deliberately isolated test container, answer `n`; otherwise accept the default.

In unprivileged LXC a host-enforced sysctl policy can prevent automatic correction. The installer will not override such policies silently; see the LXC troubleshooting guide if it stops.

It then sets up the `upwatch` system user, a UTF-8 PostgreSQL database, a Python virtual environment, `/etc/upwatch/vault.key`, systemd units, and an initial administrator (password at least 14 characters).

Do not rerun the installer blindly if an installation step fails. Inspect the exact error or restore the clean pre-installation snapshot.

## 3. Verification

```bash
cat /opt/upwatch/VERSION
# Expected: 1.0.0-rc1
systemctl is-active postgresql upwatch-web
# If worker was enabled during setup:
systemctl is-active upwatch-worker
journalctl -u upwatch-web -n 50 --no-pager
journalctl -u upwatch-worker -n 50 --no-pager
runuser -u upwatch -- /opt/upwatch/.venv/bin/python /opt/upwatch/health_probe.py
```

Access is configured **during installation**. The `localhost-only` mode also binds the web service to loopback. Single client IPs are converted into host CIDRs (`/32` for IPv4 or `/128` for IPv6); multiple entries are stored comma-separated. **Allowed client CIDRs**, **trusted reverse proxy source CIDRs**, and **monitor destination CIDRs** are distinct settings. You can later change settings in `/etc/upwatch/upwatch.env` and restart the affected services; review your firewall and reverse-proxy policy before exposing the application.

## 4. Changing the network settings after installation (optional)

The installer already writes these settings. If your infrastructure changes later, edit `/etc/upwatch/upwatch.env` manually, for example:

```ini
UPWATCH_DOMAIN=monitor.example.net
ALLOWED_HOSTS=monitor.example.net,localhost,127.0.0.1,[::1]
TRUSTED_PROXY_CIDRS=192.0.2.10/32
# Restrict to the network ranges you explicitly need:
# MONITOR_ALLOWED_CIDRS=10.10.0.0/24
```

`192.0.2.10` is a documentation address, not a functional proxy configuration. Restart after making changes:

```bash
systemctl restart upwatch-web upwatch-worker
```

In an unprivileged Proxmox LXC, verify ICMP as the **upwatch** service user; refer to the [LXC guide](docs/INSTALL_PROXMOX_LXC.md).

## 5. First-run acceptance checklist

1. Sign in using the administrator created during setup, enable MFA, securely store the recovery codes, and verify a fresh one-time code.
2. Create HTTP/TCP and ICMP monitors; verify successful and failed checks.
3. Configure SMTP and send a test notification.
4. Create Viewer and Site Manager accounts; test group/tag visibility and server-side access restrictions.
5. Open scaled history charts, availability reports, and CSV downloads; inspect audit logs.
6. Validate separate dependency groups and notification suppression.
7. Test UDP DNS/NTP and any SNMPv3 devices you intend to monitor.
8. Confirm language switching between German and English on the login page and dashboard.

**Release-candidate disclaimer:** SNMPv3 has **not** been comprehensively validated against real-world devices. The complete clean-install distribution also requires end-to-end acceptance before a stable release.

## 6. Backup and restore

Protect and back up **together**:

- PostgreSQL database `upwatch` (`pg_dump -Fc` is one option)
- `/etc/upwatch/upwatch.env`
- `/etc/upwatch/vault.key` — encrypted credentials cannot be recovered without this key
- Installed application version and any custom host configuration

Test restoration on an isolated instance. A snapshot does not replace a verified backup strategy.

## 7. Troubleshooting

```bash
systemctl status postgresql upwatch-web upwatch-worker --no-pager
journalctl -u upwatch-web --since '15 minutes ago' --no-pager
journalctl -u upwatch-worker --since '15 minutes ago' --no-pager
date -u
timedatectl status
```

In Proxmox LXC, inspect the **host** time-sync status as well. Redact credentials before sharing diagnostic logs.

## Updates

This baseline archive intentionally contains **no updater or historical database migrations**. Future versions require a separately tested update package.


### Terminal color highlighting

The setup wizard highlights section titles in cyan, questions in yellow, and accepted
IP/CIDR entries in green when the terminal supports ANSI colors. Colors are
automatically disabled for redirected output, for `TERM=dumb`, or whenever the
`NO_COLOR` environment variable is set. For example:

```bash
NO_COLOR=1 bash install.sh
```

Color highlighting changes only the appearance of the prompts, not their defaults
or validation behavior.
