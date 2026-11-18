# upWATCH!

**Self-hosted service monitoring for Linux, with a responsive dashboard, incident tracking and role-based access.**

**Interface languages:** Deutsch / English · German is the default. Choose your language on the sign-in screen or dashboard. The selection is remembered by the browser.

**Release:** `v1.0.0-rc1` · **Status:** Release candidate · **Deployment:** Fresh installation only

upWATCH! monitors network services and endpoints from a central Linux server. It combines active checks, configurable notifications, historical measurements, availability reports, user permissions, maintenance windows and monitor dependencies in a dark, mobile-friendly web interface.

> [!IMPORTANT]
> **Release-candidate disclaimer.** This is not a certified high-availability or security product. The consolidated `v1.0.0-rc1` clean-install package still requires end-to-end installation acceptance on a fresh target system. **SNMPv3, including authentication, encryption, discovery and interface counters, has not yet been sufficiently tested against real-world devices.** Do not rely on SNMPv3 checks as the sole source of operational alerts until you have verified them in your own environment. Other functionality has undergone development and offline checks, but this is **not** a claim that every protocol, deployment topology or browser has been tested. Use a test instance before production and maintain independent alerting for critical infrastructure.

> [!WARNING]
> **New installations only.** The included `install.sh` rejects existing upWATCH! installations. This package does not contain an upgrade or migration tool. Never run the fresh installer over an existing instance.

## Contents

- [Features](#features)
- [Requirements and platform support](#requirements-and-platform-support)
- [Installation](#installation)
- [First-time setup](#first-time-setup)
- [How to use upWATCH!](#how-to-use-upwatch)
- [Configuration and networking](#configuration-and-networking)
- [Operations, backups and troubleshooting](#operations-backups-and-troubleshooting)
- [Known limitations](#known-limitations)
- [Security and project status](#security-and-project-status)

## Features

### Monitoring methods

| Monitor | What it checks | Important detail |
| --- | --- | --- |
| **ICMP / Ping** | Host reachability through ICMP echo | Needs functional `ping` permissions for the `upwatch` service user; particularly relevant in unprivileged LXC containers. |
| **TCP** | Establishes a TCP connection to a configured port | A successful connection does not guarantee correct application-level behavior. |
| **UDP DNS** | Sends a DNS query over UDP and verifies the response | Requires a valid DNS reply, not merely a successful UDP send. |
| **UDP NTP** | Sends an NTP request and validates the response | Requires a valid, matching NTP response. |
| **UDP custom** | Sends a hexadecimal UDP payload and verifies a hexadecimal reply prefix | A timeout is **indeterminate**, not proof that the port is closed. |
| **HTTP / HTTPS** | HTTP status, optional response-body checks and JSON field comparison | Supports expected status codes and configurable body conditions. |
| **DNS** | A, AAAA, MX, TXT, NS and CNAME lookups | Optional expected-record comparisons. |
| **Dynamic DNS bundle** | Checks configured DNS names and optionally compares public/WAN IP information | Supports multiple names and selectable resolver modes. |
| **WAN DNS** | Checks a wildcard DNS zone or an individual hostname against WAN-IP observations | Useful for dynamic-DNS configurations. |
| **TLS certificate** | TLS connectivity, certificate validation and expiration warnings | Warning/critical thresholds are provided by the check. |
| **SSH** | TCP connection and SSH protocol banner | Does not authenticate to the SSH server. |
| **SMTP service** | SMTP greeting/EHLO, optionally STARTTLS | Different from the configurable SMTP *notification sender*. |
| **SNMPv3** | SNMPv3 OID values and interface counters | **Experimental in this RC; incomplete end-to-end device testing.** |

### Dashboard and operations

- Responsive dark dashboard, with monitor widgets, grouping, tags, filters, search, focus mode and configurable appearance/logo.
- Custom monitor aliases, intervals, timeouts and warning/critical latency thresholds.
- Configurable failure and recovery thresholds to prevent excessive state changes and alerts (defaults: three consecutive failures and two recoveries).
- **UP**, **DOWN** and **WARNING** states, plus stale-data indications when checks are overdue or the monitoring worker stops reporting.
- Historical check data and trends with labeled time and value axes; hourly and daily rollups.
- Availability reports for **7, 30, 90 or 365 days**, with CSV download. Reports show successful-check ratios and confirmed incident durations; **these are not time-weighted SLA guarantees**.
- Incident/event history, audit log with CSV export, and diagnostic information.
- Multiple monitor dependency relationships. If a parent fails, downstream alerts may be deferred; children continue to be checked.
- One-time, daily and weekly maintenance windows, suppressing alerts during planned work while retaining monitoring data.
- SMTP DOWN/UP notifications with an outbox, status/attempt counters and delivery history.
- Per-user alert recipient settings, subject to the user's permitted groups/tags; a global SMTP recipient is also supported.
- Data-retention settings for raw measurements, rollups, audit events and email history.

### Users and access control

Three roles are provided:

| Capability | Administrator | Site Manager | Viewer |
| --- | --- | --- | --- |
| View permitted monitors and histories | All | Assigned scope | Assigned scope |
| Create/edit/delete monitors | Yes | In assigned scope | No |
| Manage dependencies for accessible monitors | Yes | In assigned scope | No |
| Manage users, roles and visibility grants | Yes | No | No |
| Configure global SMTP, SNMP profiles and system settings | Yes | No | No |
| View administrative audit data | Yes | No | No |

An administrator assigns **groups and/or tags** to each non-admin user. Visibility uses **OR logic**: a monitor is visible if it belongs to an assigned group **or** carries an assigned tag. No grants means no visible monitors. Permissions are enforced by the API, not merely by hiding widgets in the browser. The management interface provides checkboxes, bulk selection and a visibility preview.

Accounts support password changes, username changes and TOTP-based multi-factor authentication (MFA) with a QR code and one-time recovery codes.

## Requirements and platform support

**Tested development target / supported installer target:** Debian GNU/Linux **13** with `systemd`. The installer checks both requirements explicitly. It is suitable for a fresh standalone Debian installation on physical hardware, in a VM or in an unprivileged Proxmox LXC container.

**Other Linux distributions:** The Python service may be portable, but the provided installer is **not distribution-independent**. Ubuntu, Fedora, Arch, Alpine, non-systemd environments and other Debian releases are **not supported by this installation script**. They would require an adapted, independently tested installation procedure.

| Component | Requirement |
| --- | --- |
| Operating system | Fresh Debian GNU/Linux 13 with systemd |
| Privileges | Interactive root shell for initial installation |
| Database | Local PostgreSQL (installed automatically; Debian 13 normally provides PostgreSQL 17) |
| Runtime | Python 3 and `venv`; package dependencies installed by the installer |
| Networking | DNS and access to Debian package repositories and PyPI during installation |
| Web service | TCP **8080** (Gunicorn; default bind `0.0.0.0`) |
| Monitoring traffic | Appropriate outbound TCP, UDP, ICMP and DNS access to the targets |
| Browser | Modern desktop or mobile browser; HTTPS recommended for user access |
| Suggested test resources | 2 vCPU, 2 GB RAM, 10 GB storage **as a starting point, not a validated minimum** |

Installed APT dependencies: `python3`, `python3-venv`, `ca-certificates`, `iputils-ping`, `postgresql`, `postgresql-client`.

The installer creates a Python virtual environment with `gunicorn`, `cryptography`, `pysnmp`, `dnspython`, `psycopg[binary]` and `qrcode`. **Node.js is not required at runtime.**

For complete environment notes, see [Standalone Debian installation](docs/INSTALL_LINUX.md) or [Proxmox LXC installation](docs/INSTALL_PROXMOX_LXC.md).

## Installation

Choose **one** of these guides:

1. **[Standalone Linux / Debian 13 host or VM](docs/INSTALL_LINUX.md)** — fresh self-contained installation on a Debian 13 server.
2. **[Proxmox VE + unprivileged Debian 13 LXC](docs/INSTALL_PROXMOX_LXC.md)** — create a new container, configure networking and install upWATCH!.

Both use the same package and the same installer. The following is the common installation command **inside the Debian 13 guest**, as root:

```bash
cd /root
unzip upWatch-rc1-v1.0.0-DE-EN-clear-wizard.zip
cd upwatch
sha256sum -c SHA256SUMS.txt
bash install.sh

The guided installer offers localhost-only, restricted LAN, HTTPS reverse proxy, or combined LAN + proxy access. LAN client IPs/CIDRs and trusted reverse proxy IPs/CIDRs are collected individually (Enter at the add-another prompt means No). It also configures permitted monitoring networks, clock/time zone checks and automatic narrow-scope ICMP permission configuration (when Debian defaults permit it). It can leave the monitoring worker disabled in an isolated test environment. No manual `.env` edit is required for the initial network setup.
```

The installer checks the current time zone and UTC clock, offers an optional IANA time-zone setting and warns if NTP synchronization cannot be confirmed. In Proxmox LXC, synchronize the clock on the Proxmox host; the container only controls its local time-zone setting. Dashboard timestamps may additionally use the browser's locale and time zone.

The installer:

1. Verifies Debian 13, systemd, root access and that no previous upWATCH! installation is present.
2. Installs PostgreSQL and Python dependencies.
3. Creates the `upwatch` system account and a dedicated PostgreSQL UTF-8 database.
4. Installs the application under `/opt/upwatch` and builds `/opt/upwatch/.venv`.
5. Creates `/etc/upwatch/upwatch.env` and the encryption key `/etc/upwatch/vault.key`.
6. Installs and starts `upwatch-web` and `upwatch-worker` as systemd services.
7. Prompts for the initial administrator username and a password of **at least 14 characters**.
8. Runs local readiness checks.

Do not re-run the installer blindly if it stops midway: it intentionally refuses partially installed systems. Diagnose and recover or start again from a clean snapshot.

## First-time setup

1. **Verify the services:**

   ```bash
   cat /opt/upwatch/VERSION
   systemctl is-active postgresql upwatch-web upwatch-worker
   journalctl -u upwatch-web -n 30 --no-pager
   journalctl -u upwatch-worker -n 30 --no-pager
   ```

   Expected version: `1.0.0-rc1`.

2. **Choose how browsers reach the application.** The initial host allowlist only contains loopback names. Configure either restricted direct-LAN access or an HTTPS reverse proxy before using it from another machine; see [Configuration and networking](#configuration-and-networking). The `/healthz` endpoint is accessible for a local service check regardless of browser host restrictions.
3. **Sign in** with the administrator created during installation.
4. **Enable MFA** under **Settings → Account**; scan the generated QR code and save the recovery codes securely.
5. **Create a test monitor**, wait for several check intervals and inspect its history.
6. **Configure SMTP** in Settings, send a test message and check the delivery queue.
7. **Create Viewer and Site Manager accounts**, assign groups/tags, and verify that each account only sees its permitted monitors.
8. **Configure dependencies and maintenance windows** as required.
9. **Confirm backups** before relying on the installation.

> The shipped web interface currently uses **German labels**. The English names in this README describe the corresponding functions.

## How to use upWATCH!

### Create and configure a monitor

Select **Add Endpoint** (`Endpunkt hinzufügen`), enter an alias and target, choose the monitoring method and configure its relevant fields. Specify the polling interval (10–86,400 seconds), timeout (at least 1 second and shorter than the interval), failure threshold (1–20) and recovery threshold (1–20). Optional latency thresholds can trigger warnings or critical states. Enable DOWN/UP email notifications if desired, then save.

Some protocol-specific details:

- For **TCP**, enter a hostname/IP and TCP port.
- For **UDP**, choose DNS, NTP or Custom. The Custom mode requires a hex-encoded request and expected response prefix. A UDP timeout does **not** prove availability or unavailability; it results in a warning/indeterminate outcome.
- For **HTTP(S)**, enter a complete URL and optionally define body-content or JSON checks.
- For **DNS**, choose the record type and optionally provide expected answers.
- For **SNMPv3**, create an SNMP profile in the dedicated **Settings → SNMPv3 Profiles** tab first. Treat results as provisional until device compatibility has been validated.

### Organize the dashboard

Use **Groups and Tags** to create group names and tag labels, then assign them to monitor widgets. Search and filter by status, group or tag. The focus/clean view reduces clutter. Administrators manage user visibility separately under **Settings → Users**; simply hiding a dashboard group does not change access permissions.

### Set up independent dependencies

Open **Dependencies** and choose one parent monitor with one or more child monitors. Repeat for additional parent monitors: for example, Proxy A may be the parent of three services, while Proxy B has a separate set of children. Cyclic relationships are rejected. Suppression affects notification handling; the dependent monitor is still checked and its status/history remains available.

### Configure notifications

Under **Settings → SMTP**, set the sending host, port, encryption mode (STARTTLS or implicit TLS), sender, login details and global recipient. Store credentials only through the UI; they are protected by the application's encrypted secret storage. Send a test message and inspect the delivery history (`pending`, `sending`, `sent`, `failed`). For targeted alerts, administrators use **Settings → Alert Recipients** and configure each user's address and DOWN/UP preferences; group/tag visibility applies.

### View history and reports

Open a widget's **Measurement History** (`Messverlauf`) to see latency and success trends with time and value scales. Open **Availability** (`Verfügbarkeit`) for the 7/30/90/365-day report and use **Download CSV** to export the selected period. Interpret the reported success percentage as the **proportion of successful checks**, not as a contractual uptime SLA or a time-weighted availability figure. Confirmed incident duration is presented separately.

### Audit, maintenance and diagnostics

Administrators can view user actions in **Settings → Audit Log** and export them to CSV. Use maintenance windows for planned work (once, daily or weekly); monitoring continues while applicable alerts are suppressed. Inspect incident/event views and monitor diagnostics when investigating failures. The dashboard highlights stale data if a check is overdue or the worker has stopped reporting.

## Configuration and networking

Configuration file: `/etc/upwatch/upwatch.env` (owned by root, readable by the `upwatch` group). Changes require restarting the relevant services:

```bash
systemctl restart upwatch-web upwatch-worker
```

### Local-LAN access (private network only)

The initial install allows localhost hostnames only. To enable a **specific private LAN IP** on port 8080, add settings like the following, replacing the examples with your actual private addresses:

```ini
UPWATCH_LAN_HOST=10.10.20.15
UPWATCH_LAN_CLIENT_CIDRS=10.10.20.0/24
```

Only clients in the specified networks can use the special direct-LAN origin. Restrict TCP 8080 with a firewall as well. Do not use this mode as an Internet-facing replacement for HTTPS.

### Public or external HTTPS reverse proxy

For Internet access, terminate TLS at a trusted reverse proxy and configure its **actual** IP address as a trusted proxy. Example placeholders:

```ini
UPWATCH_DOMAIN=monitor.example.net
TRUSTED_PROXY_CIDRS=10.10.20.10/32
# Optional: allow monitoring of internal targets when running in public mode
MONITOR_ALLOWED_CIDRS=10.10.20.0/24
```

`UPWATCH_DOMAIN` must be a bare fully qualified domain name, **not** a URL. Configuring it enables strict public-origin and secure-cookie protections. The proxy must forward the correct host and HTTPS-forwarding headers and sanitize/overwrite client-supplied forwarding headers. Block direct public access to TCP 8080 and ensure that only the trusted proxy can reach the public-mode web service. Do **not** copy the example CIDRs into a real deployment.

`MONITOR_ALLOWED_CIDRS` is for permitted monitoring targets; it is **not** the browser or proxy allowlist. Explicit network allowlisting is important for private targets behind a public dashboard.

### Data retention

Optional environment variables (days; defaults in parentheses):

```ini
UPWATCH_CHECK_RETENTION_DAYS=30
UPWATCH_HOURLY_RETENTION_DAYS=365
UPWATCH_DAILY_RETENTION_DAYS=1095
UPWATCH_AUDIT_RETENTION_DAYS=365
UPWATCH_EVENT_RETENTION_DAYS=365
UPWATCH_SENT_MAIL_RETENTION_DAYS=90
UPWATCH_FAILED_MAIL_RETENTION_DAYS=180
```

These are cleanup policies, not substitutes for backups. Higher monitor counts and shorter intervals increase the PostgreSQL storage requirements.

## Operations, backups and troubleshooting

```bash
# Inspect status and version
cat /opt/upwatch/VERSION
systemctl status postgresql upwatch-web upwatch-worker --no-pager

# View service logs
journalctl -u upwatch-web -n 100 --no-pager
journalctl -u upwatch-worker -n 100 --no-pager

# Check web health locally
curl -fsS http://127.0.0.1:8080/healthz

# Example PostgreSQL backup (run from a root shell)
runuser -u postgres -- pg_dump -Fc upwatch > /root/upwatch-db.dump
chmod 0600 /root/upwatch-db.dump
```

**A recoverable backup must include all three:** the PostgreSQL database, `/etc/upwatch/upwatch.env`, and `/etc/upwatch/vault.key`. Without the same vault key, encrypted credentials cannot be recovered. Preserve file permissions, keep backup copies off-host, and perform restore tests on an isolated environment. Do not publish these files, recovery codes, exported user data or logs containing secrets.

More commands and failure diagnosis are in [Standalone operations](docs/INSTALL_LINUX.md#verification-and-troubleshooting).

## Known limitations

- **SNMPv3 needs additional end-to-end validation** (real devices, auth/privacy modes, counters and discovery); see the release disclaimer.
- The **clean-install distribution itself** has not yet completed acceptance on a freshly reset target system.
- No upgrade or automated migration path is included in this initial baseline.
- The installer is restricted to Debian 13 + systemd, despite the application being implemented in Python on Linux.
- UDP without a valid reply is inherently **indeterminate**; it must not be interpreted as UP.
- Availability percentages are derived from sample success ratios, **not time-weighted SLAs**.
- Performance and scaling depend on check frequency, available resources and database retention. No universal maximum monitor count is claimed.
- The UI is presently in German; this documentation is in English.

## Security and project status

Passwords are hashed with scrypt; MFA uses TOTP and recovery codes. Sessions, CSRF checks, origin/host restrictions, role permissions and network-target policies are implemented, but no blanket security-audit certification is claimed. Use least-privilege accounts, MFA, HTTPS, firewalls, separate backups and a staged deployment process.

**License:** No open-source license has been selected in the supplied release package. Until an explicit license is added by the copyright holder, do not assume permission to redistribute or modify the software beyond applicable law. A public GitHub repository should include a deliberate license decision and a security-reporting contact before launch.

For deployment guidance, read [Standalone Debian installation](docs/INSTALL_LINUX.md) or [Proxmox LXC installation](docs/INSTALL_PROXMOX_LXC.md).

## Languages / Sprachen

The web interface supports **German (Deutsch)** and **English**. Select the language from the sign-in page or dashboard header; changes take effect without a restart. The preference is saved locally in the browser. Monitor aliases, tags, group names and raw probe diagnostics are not translated. Some technical or external diagnostic messages may remain in their original language.

[Deutsche Kurzanleitung](README_DE.md) is also included.
