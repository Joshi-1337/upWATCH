# Installation B — Proxmox VE LXC (Debian 13)

This guide creates a **new, unprivileged** Proxmox LXC container and installs **upWATCH! v1.0.0-rc1** using the included Debian 13 installer.

> [!IMPORTANT]
> Proxmox VE host administration and the Debian container are separate environments. **All application installation commands below run inside the Debian 13 container**, unless the step explicitly says **Proxmox host/UI**.

> [!WARNING]
> Do not use an existing or production container for initial acceptance. Create a separate new CT. The installer is intended exclusively for fresh installations, and a monitored system must not accidentally receive duplicate notifications from a clone.

## 1. Prepare a Debian 13 template — Proxmox host/UI

1. Log in to the Proxmox VE web interface.
2. Select a storage location supporting CT templates and open **CT Templates**.
3. Download a **Debian 13** standard LXC template compatible with your Proxmox installation. The exact template filename can vary by repository and architecture.
4. Confirm that your Proxmox host has working DNS, repository access and enough free storage.

## 2. Create the container — Proxmox host/UI

In **Create CT**, choose:

| Option | Recommendation / explanation |
| --- | --- |
| CT ID | A free ID of your choice |
| Hostname | Example: `upwatch` |
| Template | Downloaded Debian 13 template |
| Unprivileged container | **Enabled** |
| Password / SSH keys | Set strong administrator access appropriate to your infrastructure |
| Disk | Start with about 10 GB for a small trial, increase for retention/history |
| CPU | Start with 2 vCPU for a small trial |
| Memory | Start with 2 GB RAM for a small trial |
| Network | Attach to the correct Linux bridge/VLAN |
| Addressing | DHCP reservation or a documented static IP, correct gateway/DNS |
| Firewall | Enable and configure restrictive inbound rules as required |
| Start at boot | Optional; usually desirable after acceptance |

These resource figures are **planning suggestions, not benchmarked minimums**. You do not need to make the container privileged or grant broad raw-network capabilities for the application.

If the template/network environment requires specific Proxmox features, follow your own host policy. Do not enable nesting or unrelated privileges solely for upWATCH!.

Start the container and open its console or SSH as root.

## 3. Verify Debian inside the container

```bash
cat /etc/os-release
ps -p 1 -o comm=
ip address
ip route
getent hosts deb.debian.org
```

Confirm Debian 13, systemd, correct IP addressing and outbound DNS/Internet connectivity.

Copy `upWatch-rc1-v1.0.0-DE-EN-clear-wizard.zip` into `/root` inside the **container**. You may use SCP/SFTP or the transfer method approved for your environment.

## 4. Install the application — inside the container

```bash
apt-get update
apt-get install -y unzip
cd /root
unzip upWatch-rc1-v1.0.0-DE-EN-clear-wizard.zip
cd upwatch
sha256sum -c SHA256SUMS.txt
bash install.sh
```

The installer also displays the local and UTC time and offers to set an IANA time zone such as `Europe/Berlin`. **Proxmox LXC shares the host clock:** ensure NTP or chrony is healthy on the **Proxmox host**. An LXC cannot repair host clock drift by changing its time zone. If synchronization is unverified, the installer warns and asks for explicit confirmation. This matters for TOTP/MFA and incident timestamps.

Choose an initial administrator name and a password of at least 14 characters when prompted.

The guided network setup contains five numbered screens: access mode, LAN browser access, HTTPS proxy access, monitoring targets, and a final configuration review. Enter allowed IP addresses/CIDRs individually (the default at **Add another? [y/N]** is No). Browser clients, trusted proxies, and monitoring targets are separate security settings. Confirm with **Y**, restart the wizard with **r**, or cancel with **q**. The installer verifies ICMP as the `upwatch` service user. It also asks whether the worker should start; choose `n` when keeping a laboratory container isolated.

The installer sets up local PostgreSQL, the Python venv, the web service, an optionally enabled worker and the encryption key. It will **refuse to overwrite** an existing application or database.

## 5. Verify the services — inside the container

```bash
cat /opt/upwatch/VERSION
systemctl is-active postgresql upwatch-web upwatch-worker
curl -fsS http://127.0.0.1:8080/healthz
journalctl -u upwatch-web -n 50 --no-pager
journalctl -u upwatch-worker -n 50 --no-pager
```

Expected version: `1.0.0-rc1`. In a normal production installation both upWATCH! units should be active.

**Clone/test warning:** If you create a clone of an already configured installation, stop and disable the cloned worker before connecting the clone to the real network. Otherwise the clone may probe the same destinations and deliver duplicate SMTP alerts. A *fresh* container with an empty monitor database does not inherit those monitored endpoints.

## 6. Review network access — inside the container / firewall

**Network access is configured interactively during installation.** The example below is only for a subsequent manual configuration change:

```ini
# /etc/upwatch/upwatch.env — replace both example values
UPWATCH_LAN_HOST=10.30.40.50
UPWATCH_LAN_CLIENT_CIDRS=10.30.40.0/24
```

If you change the configuration later, restart the relevant enabled service(s):

```bash
systemctl restart upwatch-web
# If the worker is intentionally enabled:
systemctl restart upwatch-worker
```

For a public domain, configure an **isolated HTTPS reverse proxy** and choose proxy-only or combined LAN + proxy access with the appropriate `UPWATCH_DOMAIN`, `TRUSTED_PROXY_CIDRS` and monitor target allowlist. See the main [README](../README.md#configuration-and-networking).

**Proxmox firewall:** Allow inbound TCP 8080 only from management subnets or the reverse proxy. Allow the necessary outbound TCP/UDP/ICMP traffic to the monitored targets. Do not expose TCP 8080 directly to the public Internet.

## 7. Automatic ICMP / Ping setup in unprivileged LXC

The installer tests ICMP echo requests **as the `upwatch` service user**, not as root. When the kernel has Debian's standard `65534 65534` restricted group range and the service user cannot ping, the installer automatically:

1. Looks up the current numeric GID of `upwatch` (never hardcodes `991`).
2. Applies the narrow `net.ipv4.ping_group_range = GID GID` setting for this container.
3. Saves it in `/etc/sysctl.d/90-upwatch-icmp.conf` for subsequent boots.
4. Retests ping as `upwatch` and fails the installation if it is still unavailable.

If ping already works, no kernel configuration is changed. The installer **does not broaden a custom group range, grant `CAP_NET_RAW`, change container privilege mode, or modify the Proxmox host**. If the host denies setting this sysctl or a non-default policy is present, setup stops with a diagnostic message rather than ignoring broken ICMP checks. Contact the Proxmox administrator to adjust that policy.

To check after installation and after a container reboot:

```bash
id -g upwatch
sysctl net.ipv4.ping_group_range
runuser -u upwatch -- ping -n -c 2 -W 2 127.0.0.1
# Also test an IP you control on the monitoring network.
```

**Note:** The installer can verify permission to ping the container loopback address, not whether an external destination will reply or permit ICMP through its firewall.

## 8. First application acceptance

1. Sign in and enroll MFA.
2. Add HTTP, TCP and ICMP test monitors for endpoints you control.
3. Run UDP DNS/NTP checks and inspect their returned results; lack of a UDP reply is not automatically UP.
4. Create a Viewer user and assign exactly one group/tag; sign in as that user and confirm the restricted view.
5. Create multiple independent dependencies, e.g. two upstream proxies with distinct downstream monitors.
6. Configure SMTP and verify DOWN and recovery notifications only if sending real test messages is acceptable.
7. Confirm report refresh, report CSV, audit CSV, and scaled measurement-history graphs.
8. Confirm maintenance windows and alert suppression.
9. Verify that SNMPv3 remains labeled **not yet sufficiently end-to-end tested** until real device tests are completed.
10. Verify that the web layout works on your preferred mobile browser; desktop responsive testing does not fully replace a physical-device check.

## 9. Backups and Proxmox snapshots

- Back up the CT using your normal Proxmox backup policy.
- Also preserve application-level PostgreSQL dumps and **both** `/etc/upwatch/upwatch.env` and `/etc/upwatch/vault.key`.
- Use an isolated restore CT to confirm the application can be recovered; prevent its monitoring worker from issuing production alerts.
- Snapshots are helpful before changes but are **not a replacement** for recoverable, preferably off-host backups.

Example application database backup **inside the container**, as root:

```bash
mkdir -m 0700 -p /root/upwatch-backup
runuser -u postgres -- pg_dump -Fc upwatch > /root/upwatch-backup/upwatch.dump
cp -p /etc/upwatch/upwatch.env /etc/upwatch/vault.key /root/upwatch-backup/
chmod -R go-rwx /root/upwatch-backup
```

## 10. Troubleshooting

```bash
systemctl status postgresql upwatch-web upwatch-worker --no-pager
journalctl -u upwatch-web --since '15 minutes ago' --no-pager
journalctl -u upwatch-worker --since '15 minutes ago' --no-pager
runuser -u postgres -- pg_isready -h /var/run/postgresql
id -g upwatch
sysctl net.ipv4.ping_group_range
```

Common causes:

- **HTTP 421:** Browser host not authorized; configure the exact LAN host or HTTPS domain.
- **HTTP 400/403 with proxy:** Proxy IP, forwarded scheme or origin settings are inconsistent.
- **Ping fails in the worker:** Service-user ICMP permission or outbound firewall policy.
- **Private targets blocked:** `MONITOR_ALLOWED_CIDRS` does not include the resolved destination(s).
- **No email:** Inspect SMTP configuration, notification outbox and worker logs.
- **No current readings:** Confirm the worker is running and review stale status indicators.

Do not disclose vault keys, passwords, recovery codes or unredacted logs.

## 11. Release limitations

This is `v1.0.0-rc1`, a **release candidate**. The first clean-install package has not yet completed fresh-CT acceptance, SNMPv3 still requires device-level validation, and **no updater** is included. Do not treat this guide as a warranty of production readiness.
