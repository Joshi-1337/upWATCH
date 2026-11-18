# Installation A — Standalone Debian 13 (physical Linux server or VM)

This guide is for **upWATCH! v1.0.0-rc1**, released as `upWATCH-rc1-v1.0.0_DE-EN_guided`. It installs the software on a **fresh independent Debian GNU/Linux 13 server**, without Proxmox. The installer is **not distribution-independent**, even though the application is written in Python.

> [!WARNING]
> **New installation only.** Do not run `install.sh` on a machine already running upWATCH!. There is no upgrade or import procedure in this release.

## 1. Requirements

| Component | Requirement |
| --- | --- |
| OS | Debian GNU/Linux **13**; systemd as init process |
| Privileges | Interactive **root** terminal |
| CPU/RAM | Example planning size: **2 vCPU / 2 GB RAM** for a small test deployment; not benchmarked minimums |
| Storage | Start with approximately **10 GB** for a test; increase according to check frequency/history retention |
| Database | Local PostgreSQL, provisioned by installer (usually PostgreSQL 17 on Debian 13) |
| Runtime | Python 3, venv, pip dependencies, provisioned by installer |
| Packages | Accessible Debian APT repositories, PyPI, functional DNS and outbound HTTPS |
| Inbound | TCP **8080** only from approved client IPs or a private HTTPS reverse proxy |
| Outbound | Network access appropriate for monitored ICMP/TCP/UDP/DNS/HTTPS services |
| Time | Accurate UTC system clock (essential for TOTP/MFA) |

The installer installs `python3`, `python3-venv`, `ca-certificates`, `iputils-ping`, `postgresql`, `postgresql-client`, and `tzdata`. It creates a virtual environment with `gunicorn`, `cryptography`, `pysnmp`, `dnspython`, `psycopg[binary]`, and `qrcode`. **Node.js is not needed.**

## 2. Prepare a fresh Debian installation

Install Debian 13 normally on your physical server or VM. Configure networking, DNS, gateway, SSH (if required) and an appropriate firewall. Do not expose TCP 8080 directly to the public internet.

Become root and check the machine:

```bash
cat /etc/os-release
ps -p 1 -o comm=
ip -brief address
ip route
getent hosts deb.debian.org
date -u
timedatectl status
```

The OS should report Debian 13, PID 1 should be `systemd`, and internet/DNS access must work. If the clock is incorrect, configure time synchronization **before** enrolling MFA. The installer displays local and UTC time and checks synchronization, but cannot independently prove the clock is accurate.

## 3. Copy and verify the release

Download the release ZIP from the GitHub release and transfer it to `/root` via SCP/SFTP or another approved method. The commands below use the proposed public archive name. Substitute the real name if the published asset differs.

```bash
apt-get update
apt-get install -y unzip
cd /root
unzip upWATCH-rc1-v1.0.0_DE-EN_guided.zip
cd upwatch
sha256sum -c SHA256SUMS.txt
```

All checksums must return `OK`. If they do not, stop and obtain a valid release copy.

## 4. Start the guided installer

```bash
bash install.sh
```

The installer runs interactively and uses colored headings and prompts in compatible terminals (honoring `NO_COLOR`). The installer itself is **English**. The application UI later supports both **German and English**.

### Time zone and time check

The installer displays the system's local time, UTC time and time zone, and lets you select an IANA time zone (`Europe/Berlin`, `UTC`, etc.). The time zone controls local presentation; **correct UTC time depends on a synchronized host clock**. If synchronization cannot be verified, the installer asks for explicit acknowledgement.

### Guided network access: five steps

**Step 1 — Access mode.** Choose one:

| Choice | Mode | Intended use |
| --- | --- | --- |
| `1` | **Localhost only** | Browser on the host or an SSH tunnel; web service binds to loopback |
| `2` | **Private LAN** | HTTP access from explicitly approved internal clients |
| `3` | **HTTPS reverse proxy only** | Client connects to an HTTPS hostname through a separate proxy |
| `4` | **Private LAN + HTTPS reverse proxy** | Both LAN and HTTPS proxy access |

**Step 2 — Direct LAN.** For modes 2/4, enter the upWATCH **server's private address** (not your workstation IP), and then the allowed browser client IPs/subnets. Examples: `10.20.30.15` (one IPv4 host), `10.20.30.0/24` (subnet), or an IPv6 host/prefix. The installer normalizes single IPs to `/32` or `/128`. At `Add another? [y/N]:`, press Enter for **No** or type `y` to add another address.

**Step 3 — Reverse proxy.** For modes 3/4, enter the **public hostname only**, without `https://`, and the IPs/CIDRs **of your actual trusted reverse proxy**. Do not use `0.0.0.0/0` or list arbitrary internet clients as trusted proxies. The proxy must terminate HTTPS, overwrite incoming `Forwarded`/`X-Forwarded-*` values, and allow only approved upstream connections.

**Step 4 — Monitoring targets.** Add explicitly approved additional private IPs/CIDRs **that the worker is allowed to probe**. This is different from browser access. You can leave the first entry empty to use the application's default target policy. Choose ranges only as broad as necessary.

**Step 5 — Review.** Read the complete summary. `Y`/Enter applies it; `r` restarts network setup; `q` cancels. The installer writes protected network settings into `/etc/upwatch/upwatch.env`.

### Automated installation tasks

The installer then configures local PostgreSQL and its UTF-8 database, creates the `upwatch` service user, installs Python dependencies, generates `/etc/upwatch/vault.key`, and sets up the `upwatch-web` and `upwatch-worker` systemd services.

The installer also tests **ICMP as the service user**. If it fails under Debian's usual `65534 65534` ping-group restriction, it uses the actual `upwatch` GID to configure a narrow, persistent rule and retests. Existing custom sysctl policies are not widened or overwritten. If the kernel forbids the change, the installer stops with a diagnostic rather than pretending ping is working.

The installer asks **`Enable monitoring worker now? [Y/n]`**. For a live monitoring installation, normally choose `Y`. Choose `n` for a deliberately isolated test instance; no periodic monitoring will run until the worker is enabled.

Create the initial administrator using the interactive prompts. The installer requires a strong password (minimum 14 characters at initial creation).

## 5. Verify the installation

```bash
cat /opt/upwatch/VERSION
systemctl status postgresql upwatch-web upwatch-worker --no-pager
runuser -u upwatch -- /opt/upwatch/.venv/bin/python /opt/upwatch/health_probe.py
id -g upwatch
sysctl net.ipv4.ping_group_range
runuser -u upwatch -- ping -n -c 2 -W 2 127.0.0.1
```

The installed version should be `1.0.0-rc1`. `upwatch-web` should be active. If you deliberately disabled `upwatch-worker`, an inactive worker is **expected**.

For failures:

```bash
journalctl -u upwatch-web -n 60 --no-pager
journalctl -u upwatch-worker -n 60 --no-pager
```

## 6. Open the web UI

- **Private LAN:** `http://YOUR_SERVER_LAN_IP:8080` from an allowed browser client.
- **Localhost only:** `http://127.0.0.1:8080` on the host or via a correctly secured SSH port forward.
- **HTTPS proxy:** `https://YOUR_PUBLIC_HOSTNAME` after separately configuring the external HTTPS reverse proxy and firewall.

The application includes **DE / EN** switching on the login screen and dashboard. Log in with the administrator created by the installer.

## 7. First-run configuration and acceptance

1. Under **Account**, configure TOTP MFA and store recovery codes securely. Verify a valid authenticator code; an inaccurate clock can invalidate codes.
2. Create a **Ping**, **TCP** or **HTTP** monitor to a target you own. Check UP/DOWN and test the configured interval and failure threshold.
3. Set up **Settings → Email / SMTP**, send a test and inspect the **delivery status**. Queued mail does not guarantee delivery.
4. Organize endpoints into **groups and tags**, then create a **Viewer** and **Site Manager** with restricted scopes.
5. Test **Dependencies**, maintenance windows, scaled history, availability reports/CSV and audit log/CSV.
6. Check both languages and the mobile layout.
7. **SNMPv3 is not sufficiently validated on real devices in this release.** Verify separately if you choose to use it.

For descriptions of every widget and administrative control, see [User guide](USER_GUIDE.md).

## 8. Maintenance and backup

For configuration changes, consult [Configuration and security](CONFIGURATION.md). Back up the PostgreSQL database **and** `/etc/upwatch/upwatch.env` and `/etc/upwatch/vault.key` together. See [Operations](OPERATIONS.md) for a tested-command starting point and recovery precautions.

> **Important:** Do not rerun this fresh installer if a partial installation fails. Capture the error and restore the pre-installation snapshot or reprovision the host before another fresh-install attempt.
