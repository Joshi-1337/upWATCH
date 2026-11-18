# Installation A — Standalone Linux server (Debian 13)

This guide installs **upWATCH! v1.0.0-rc1** independently on a fresh Linux host or VM. The application does not require Proxmox. **The packaged installer only supports Debian GNU/Linux 13 with systemd**; other Linux distributions require a separate, unverified installation process.

> [!CAUTION]
> This is a **fresh-install-only** procedure. Never point it at an existing upWATCH! instance. The installer checks for existing application paths and refuses to perform an upgrade.

## 1. Provision the server

Prepare a clean Debian 13 installation with:

- An interactive root shell or access to `su -`.
- Working DNS, APT repository connectivity and HTTPS access to Python package repositories.
- An available TCP 8080 listener; restrict inbound traffic to approved LAN clients or a reverse proxy.
- Outbound network access to the destinations you intend to monitor.
- Adequate storage for PostgreSQL. For a small initial trial, **2 vCPU, 2 GB RAM and 10 GB storage** are a planning starting point, **not verified minimum requirements**.
- Accurate system time (important for MFA, incident timestamps and scheduled checks).

The installation will use the local PostgreSQL cluster, local Unix-domain authentication and the systemd services `upwatch-web` and `upwatch-worker`.

Verify the host:

```bash
cat /etc/os-release
ps -p 1 -o comm=
getent hosts deb.debian.org
```

The expected OS is Debian 13 and PID 1 should be `systemd`.

## 2. Prepare the installation archive

Copy the published `upWatch-rc1-v1.0.0-DE-EN-clear-wizard.zip` to `/root`. Then, in an interactive root shell:

```bash
apt-get update
apt-get install -y unzip
cd /root
unzip upWatch-rc1-v1.0.0-DE-EN-clear-wizard.zip
cd upwatch
sha256sum -c SHA256SUMS.txt
```

All checksum entries must pass. The internal manifest detects changed files; for authenticity, compare the ZIP checksum against a trusted value published separately by the project owner when one becomes available.

## 3. Install

```bash
bash install.sh
```

The interactive installer displays the current local/UTC time, lets you keep or set an IANA time zone (for example `Europe/Berlin`), and checks `NTPSynchronized` where available. If time synchronization cannot be confirmed, it requires explicit acknowledgement. Configure NTP/chrony on the host before enabling MFA; setting a time zone **does not** fix an inaccurate system clock.

The installer installs the APT packages and Python virtual environment, provisions the service account and database, writes the configuration and encryption key, starts the web and worker services, then asks for an initial administrator name and a password of at least 14 characters.

Do not interrupt the administrator prompt. The installer is not designed to restart from an arbitrary halfway state; if it fails, diagnose the logs or restore a clean pre-installation snapshot.

Installed locations:

| Location | Purpose |
| --- | --- |
| `/opt/upwatch` | Application code and virtual environment |
| `/etc/upwatch/upwatch.env` | Environment and network security configuration |
| `/etc/upwatch/vault.key` | Encryption key — **back up securely** |
| `/var/lib/upwatch` | Application data/backups directory |
| PostgreSQL database `upwatch` | Monitoring and application records |
| `upwatch-web.service` | Gunicorn web server, TCP 8080 |
| `upwatch-worker.service` | Probe scheduler and email queue processor |

## 4. Verify the installation

```bash
cat /opt/upwatch/VERSION
# Expected: 1.0.0-rc1

systemctl is-active postgresql upwatch-web upwatch-worker
curl -fsS http://127.0.0.1:8080/healthz
journalctl -u upwatch-web -n 50 --no-pager
journalctl -u upwatch-worker -n 50 --no-pager
```

The database and web service should report `active`; the worker should be `active` only if it was enabled during installation. Health check results should be successful. If you have deliberately disabled the worker in an isolated test instance, it should remain disabled; do not treat that as a standard production configuration.

## 5. Browser access (configured by the installer)

**The guided installer has five numbered stages: **access mode**, **LAN server IP and permitted browser clients**, **HTTPS hostname and trusted proxy addresses**, **additional permitted monitoring targets**, and **review/confirmation**. Each allowlist accepts one IP address or CIDR per entry; pressing Enter at **Add another? [y/N]** stops adding addresses. The final summary offers **Y** to apply, **r** to restart the network wizard, or **q** to cancel. The installer writes the confirmed values to `.env` automatically. In localhost-only mode the web listener binds to `127.0.0.1`.** Use the examples below only if changing the installation afterwards. Never copy example IP addresses literally.

### Option A — restricted private LAN access

Edit `/etc/upwatch/upwatch.env` and set your actual private address and trusted source CIDRs:

```ini
UPWATCH_LAN_HOST=10.20.30.40
UPWATCH_LAN_CLIENT_CIDRS=10.20.30.0/24
```

Then restart:

```bash
systemctl restart upwatch-web upwatch-worker
```

Access `http://10.20.30.40:8080` **from a permitted LAN client only**, behind a firewall. These are documentation examples, not values to copy verbatim. This mode uses unencrypted HTTP, so do not expose it to untrusted networks.

### Option B — HTTPS reverse proxy (recommended for remote access)

1. Obtain a DNS hostname and a trusted TLS certificate on a reverse proxy.
2. Permit the reverse proxy, but **not the public Internet**, to reach upWATCH! on TCP 8080.
3. Ensure the reverse proxy forwards the original `Host`, sets `X-Forwarded-Proto: https`, and overwrites client-supplied forwarded headers.
4. Add your real values to `/etc/upwatch/upwatch.env`:

   ```ini
   UPWATCH_DOMAIN=watch.example.net
   TRUSTED_PROXY_CIDRS=10.20.30.10/32
   # Optional, for approved private probe destinations:
   MONITOR_ALLOWED_CIDRS=10.20.30.0/24
   ```

5. Restart both services and verify that the browser reaches the *HTTPS* domain, while direct Internet access to port 8080 is blocked.

Setting `UPWATCH_DOMAIN` enables the application's public HTTPS/proxy trust boundary. Incorrect proxy trust or host configuration can cause HTTP 400/403/421 responses; see service logs before changing security settings.

## 6. Initial configuration

- Sign in as the newly created administrator.
- Activate TOTP MFA and **save the recovery codes offline**.
- Add one HTTP or TCP monitor and confirm that it transitions correctly.
- Add a Ping monitor and confirm ICMP access as the `upwatch` Linux user.
- Create groups/tags and test role-restricted users.
- Configure the SMTP sender and send a test message.
- Create dependencies and check that alerts are deferred when an upstream parent is down.
- Test UDP DNS/NTP against a service you control.
- Test history axes, reports and CSV exports.
- Treat SNMPv3 as **unverified** until a compatible real device has been tested.

## 7. Verification and troubleshooting

```bash
# Units and recent errors
systemctl status postgresql upwatch-web upwatch-worker --no-pager
journalctl -u upwatch-web --since '15 minutes ago' --no-pager
journalctl -u upwatch-worker --since '15 minutes ago' --no-pager

# Check the monitoring user can send ICMP echo requests
id -g upwatch
runuser -u upwatch -- ping -n -c 1 -W 2 127.0.0.1
sysctl net.ipv4.ping_group_range

# Inspect PostgreSQL readiness
runuser -u postgres -- pg_isready -h /var/run/postgresql

# Check the installed Python environment
/opt/upwatch/.venv/bin/python -m pip check
```

Never post logs without removing secrets or personal information.

## 8. Backup and recovery

A safe backup includes **the PostgreSQL database and both `/etc/upwatch` files**. Run the following as root, and secure the resulting files:

```bash
mkdir -m 0700 -p /root/upwatch-backup
runuser -u postgres -- pg_dump -Fc upwatch > /root/upwatch-backup/upwatch.dump
cp -p /etc/upwatch/upwatch.env /etc/upwatch/vault.key /root/upwatch-backup/
chmod -R go-rwx /root/upwatch-backup
```

The PostgreSQL dump alone is insufficient for encrypted SMTP/SNMP secrets. Test restoration on an isolated environment, confirm login/MFA and inspect history after restoring. Do **not** execute destructive `pg_restore --clean` or database deletion commands against a production instance without a separately reviewed restore procedure.

## 9. Updates

There is **no updater** in `v1.0.0-rc1`. A future upgrade procedure must be supplied and tested independently; rerunning `install.sh` is not a valid update path.

### Automatic ICMP permissions

The installer checks ICMP as the dedicated `upwatch` service user. If Debian's default `ping_group_range` prevents ping, it automatically installs a narrowly scoped persistent rule for the detected service GID and verifies the result. Existing custom policies are not replaced automatically; a blocked system configuration causes an explicit installation error. After reboot, check with `runuser -u upwatch -- ping -n -c 1 -W 2 127.0.0.1`.
