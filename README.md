# upWATCH!

**Self-hosted service and infrastructure monitoring · German & English · PostgreSQL · Debian 13**

**Release:** `v1.0.0-rc1`  
**Suggested GitHub release asset:** `upWATCH-rc1-v1.0.0_DE-EN_guided.zip`  
**Status:** Release candidate — fresh installation only

upWATCH! is a self-hosted monitoring application with a responsive dark dashboard. It checks network endpoints and services, records measurement history and incidents, sends email notifications, and gives different users access to selected groups and tags. Its backend uses Python, a separate monitoring worker, and local PostgreSQL; the web interface offers a **DE / EN** language toggle.

> [!IMPORTANT]
> **Release-candidate disclaimer:** This is not a certified high-availability, security, or SLA monitoring service. **SNMPv3, including real-device OID queries, authentication/privacy combinations, discovery, and interface measurements, has NOT yet undergone sufficient end-to-end testing.** Do not use SNMPv3 as your sole alerting mechanism for critical infrastructure. A working installer and development tests do not guarantee correct operation in every deployment. Always test in your environment and maintain independent alerting for critical systems.

> [!WARNING]
> **Fresh installation only.** The installer rejects existing upWATCH! installations. This baseline release has **no updater or database migration path**. Back up existing installations separately; never install this package over them.

## Documentation

| Guide | What it covers |
| --- | --- |
| [Standalone Debian 13 installation](docs/INSTALL_DEBIAN_13.md) | Physical server or independent virtual machine, requirements, guided setup, first login |
| [Proxmox VE / Debian 13 LXC](docs/INSTALL_PROXMOX_LXC.md) | Container creation, unprivileged setup, ICMP, access and verification |
| [User guide and every monitor type](docs/USER_GUIDE.md) | Dashboard controls, all monitor widgets, roles, alerts, histories, reports, dependencies |
| [Configuration and security](docs/CONFIGURATION.md) | All network access modes, `.env`, firewall, HTTPS reverse proxy, retention |
| [Operations and troubleshooting](docs/OPERATIONS.md) | Service checks, backups, recovery planning, diagnostics, known limits |

## Supported platform and prerequisites

- **Official installer target:** Fresh **Debian GNU/Linux 13 with systemd** on a physical Linux computer, a VM, or an **unprivileged Proxmox LXC**.
- The Python application may be portable, but **other Linux distributions are not supported by this installer**. Do not assume Ubuntu, Fedora, Alpine or older Debian releases work without adaptation.
- Root access during installation; working DNS and internet access to Debian repositories and PyPI.
- Python 3, `venv`, PostgreSQL and Python dependencies are installed by the installer; **Node.js is not required**.
- Accessible TCP **8080** from specifically approved clients or a secure HTTPS reverse proxy, plus suitable outbound connections to monitored services.
- Accurate UTC time for TOTP/MFA and monitoring timestamps.
- **Suggested small test setup:** 2 vCPU, 2 GB RAM and ~10 GB disk. These are planning examples, **not performance-tested minimum requirements**.

See the [complete requirements and installation guide](docs/INSTALL_DEBIAN_13.md).

## How upWATCH! works

```text
Browser (DE/EN) ──► upwatch-web (Gunicorn / Python :8080)
                         │
                         ▼
                  PostgreSQL (local)
                         ▲
                         │
                 upwatch-worker ──► ICMP / TCP / UDP / HTTP / DNS / ...
                         │
                         └────────► SMTP notification outbox
```

1. An administrator creates one or more **monitors** (also called endpoints/widgets), specifying a target, protocol, interval, timeout and optional expectations.
2. The independent **worker** runs checks and stores results, rollups and status transitions in PostgreSQL.
3. The **web service** shows live states, history, diagnostics and incidents and enforces user access rules. It does **not** perform periodic checks on its own.
4. Configured failure/recovery thresholds limit alert flapping. Maintenance windows and parent/child dependencies can suppress unnecessary notifications while checks continue.
5. Notifications are queued and delivered through configured SMTP; the delivery screen distinguishes sent, pending and failed items.

If the worker is disabled (for example in a safe lab clone), the dashboard can still open, but **monitors will not be checked**. Stale indicators help distinguish old data from current success.

## Monitoring widgets

The application has these built-in **monitor types**. Each created monitor appears as a dashboard widget.

| Widget / type | Purpose | What success means |
| --- | --- | --- |
| **ICMP / Ping** | Check if a host responds to ICMP echo | An ICMP response was received |
| **TCP** | Test a host and port | A TCP connection completed, not necessarily a healthy application |
| **UDP DNS** | Probe a UDP DNS listener | A valid DNS response was received |
| **UDP NTP** | Probe an NTP listener | A valid matching NTP reply was received |
| **UDP custom** | Send a hex payload, check reply prefix | The UDP response matches the configured prefix |
| **HTTP / HTTPS** | Check a web URL | Expected HTTP status and optional body/JSON condition match |
| **DNS** | Resolve DNS records | Requested record exists and optional expected values match |
| **DDNS / CNAME bundle** | Verify a DDNS hostname and several aliases | Configured CNAMEs point at the DDNS target; optional WAN comparison matches |
| **WAN DNS / wildcard** | Compare resolved A records to public egress IPv4 | Configured names resolve to that observed public IPv4 |
| **TLS certificate** | Inspect a TLS service/certificate | Certificate validates and expiry thresholds are not breached |
| **SSH** | Check the SSH protocol banner | A valid SSH-2.0 banner is received; **no login is attempted** |
| **SMTP service** | Inspect SMTP greeting/EHLO/STARTTLS | SMTP protocol response succeeds, not necessarily mail delivery |
| **SNMPv3** | Query OIDs or interface counters | Experimental; requires real-device validation before operational reliance |

Read [the full widget reference](docs/USER_GUIDE.md#monitor-types-and-widget-configuration) for configuration examples, interpretation and limitations.

## Main features

- **Responsive dashboard:** endpoint widgets, groups, tags, search, status filters, focus/compact view, sorting, custom appearance/logo and configurable widget layout.
- **Languages:** switch between **Deutsch** and **English** from login and dashboard; preference is remembered by the browser. Custom monitor names and some raw protocol diagnostics are not translated.
- **Monitoring states:** UP / DOWN / WARNING / pending; maintenance, dependency and stale-data indicators.
- **Flapping protection:** per-monitor failure and recovery thresholds, typically 3 failures and 2 recoveries by default.
- **History:** measurement charts with timestamps, labeled units, gaps and hourly/daily aggregation.
- **Availability reports:** 7/30/90/365-day windows, CSV export, check-success percentage, latency and confirmed incident duration. The percentage is **not time-weighted SLA availability**.
- **Dependencies:** multiple parent monitors and downstream relationships, with circular relationships rejected.
- **Maintenance:** manual maintenance flags and planned one-time/daily/weekly windows.
- **Email:** outgoing SMTP notifications, global recipient and per-user recipients, DOWN/UP preferences, delivery attempts and status history.
- **Access control:** administrator, site manager and viewer roles. Non-admin monitor visibility is assigned by **group OR tag** and enforced server-side.
- **Security:** TOTP MFA with QR setup and recovery codes; dedicated service account, protected configuration and encryption key, audit events and exports.
- **Operations:** PostgreSQL retention policies, worker freshness, backups and diagnostics.

## Quick start (fresh Debian 13)

Use **one** of the two installation guides linked above. On a clean Debian 13 system, as root, after copying the release ZIP to `/root`:

```bash
apt-get update
apt-get install -y unzip
cd /root
unzip upWATCH-rc1-v1.0.0_DE-EN_guided.zip
cd upwatch
sha256sum -c SHA256SUMS.txt
bash install.sh
```

> The ZIP name above is the **proposed published asset name**. If you downloaded a build with a different filename, substitute its actual filename in the `unzip` command. The archive contains the `upwatch/` directory.

The English guided installer checks time, offers a time zone, configures access policy, creates the PostgreSQL database, sets up user-scoped ICMP permissions where safely possible, installs web/worker services, and creates the first administrator.

**Four access modes:** (1) localhost, (2) private LAN clients, (3) HTTPS reverse proxy, or (4) private LAN + HTTPS reverse proxy. Allowed browser clients, trusted proxy addresses and permitted monitoring destinations are configured separately. An IP or CIDR is added one entry at a time; **“Add another? [y/N]” defaults to No**.

After installation, use `http://SERVER_IP:8080` for approved LAN access or your configured HTTPS hostname through a reverse proxy. Never publish raw port 8080 on the internet.

## First-use checklist

1. Sign in with the initial administrator account. Enable TOTP MFA and safely retain recovery codes.
2. Verify web and worker service status; if you intentionally disabled the worker, activate it only when safe.
3. Create an HTTP/TCP/ping monitor for a service under your control and verify UP and DOWN transitions.
4. Configure SMTP delivery and run a test; review the outbox rather than assuming queued mail was sent.
5. Create groups and tags, then assign restricted permissions to a test Viewer and Site Manager.
6. Test dependencies, maintenance windows, history graphs, availability CSV and audit CSV.
7. Check German/English UI and mobile layout on your real devices.
8. Treat SNMPv3 as **not yet sufficiently validated** until real equipment is tested.

## Security and project status

Read [configuration and security](docs/CONFIGURATION.md) and [operations and troubleshooting](docs/OPERATIONS.md). For backups, save **both the PostgreSQL database and `/etc/upwatch/vault.key`**, plus `/etc/upwatch/upwatch.env`; without the key, encrypted credentials may be unrecoverable.

**Licence / contact:** A software licence and public security contact have **not been specified in the supplied release**. Repository owners must choose and publish these before public distribution. No licence is implied.
