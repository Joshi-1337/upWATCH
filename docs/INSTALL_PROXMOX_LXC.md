# Installation B — Proxmox VE, unprivileged Debian 13 LXC

This guide creates a **new unprivileged Debian 13 container** for **upWATCH! v1.0.0-rc1**. The application and installer are the same as on standalone Debian; only container provisioning, network isolation and time/ICMP policies differ.

> [!IMPORTANT]
> **SNMPv3 has not been sufficiently tested against real-world devices in this release.** Do not assume SNMP authentication/privacy and interface counters are production-validated. The release is a **fresh-install-only RC**; do not run its installer over an existing container installation.

## 1. Requirements

- A working Proxmox VE host capable of running **unprivileged LXC containers** with Debian 13 and systemd.
- Rights to create containers, attach the appropriate bridge/VLAN, configure a firewall and start a terminal session.
- Internet access from the container to Debian repositories and Python package repositories during installation.
- DNS and an appropriate gateway for the container; suitable access to each intended monitoring target.
- Correct **UTC clock and time synchronization on the Proxmox host**, especially for TOTP MFA.
- Sizing suggestion for a small test: **2 vCPU, 2 GB RAM, ~10 GB storage**; not a validated minimum.

## 2. Download a Debian 13 template (Proxmox UI)

1. Sign in to the Proxmox VE web interface.
2. Open storage supporting container templates (often `local`) → **CT Templates**.
3. Download the available **Debian 13** standard template suitable for your system architecture. Template filenames vary; do not copy an outdated filename from an example.
4. Verify free disk capacity and DNS/network connectivity on the host.

## 3. Create the container (Proxmox UI)

Select **Create CT** and configure:

| Setting | Recommendation |
| --- | --- |
| CT ID | Any unused ID, e.g. `201` for a laboratory instance |
| Hostname | `upwatch` or a name appropriate to your environment |
| OS template | Your downloaded **Debian 13** template |
| Unprivileged container | **Enabled** |
| Authentication | Strong root credentials or authorized SSH keys |
| Disk | Start at ~10 GB for a trial and plan room for monitoring history |
| CPU | 2 vCPU as an example starting point |
| RAM | 2 GB as an example starting point |
| Network | Correct bridge, VLAN, gateway and either static IP or DHCP reservation |
| Firewall | Restrict access to TCP 8080 and only allow needed outbound probes |
| Features | Do not enable unnecessary privileged features or nested containers |

There is **no requirement to use a privileged container or grant `CAP_NET_RAW` to the whole service**. The installer attempts a narrowly scoped ICMP permission using the service account's GID when Debian's default kernel policy permits it.

Start the container and connect to its console or an SSH terminal as root.

## 4. Verify the fresh Debian container

Inside the **container**, not on the Proxmox host:

```bash
cat /etc/os-release
ps -p 1 -o comm=
ip -brief address
ip route
getent hosts deb.debian.org
date -u
timedatectl status
```

Confirm Debian 13, systemd, a correct server IP and working DNS. For private LAN access, note the container's actual assigned private IP.

**Time:** The container and Proxmox host share the kernel clock. Configuring `Europe/Berlin` or another time zone in the container does **not** synchronize time. Verify NTP/chrony or your configured time provider on the **Proxmox host** if the installer reports an uncertain synchronization state.

## 5. Transfer and install the ZIP

Copy the published ZIP to `/root` **inside the container** (for example using SCP/SFTP). Then:

```bash
apt-get update
apt-get install -y unzip
cd /root
unzip upWATCH-rc1-v1.0.0_DE-EN_guided.zip
cd upwatch
sha256sum -c SHA256SUMS.txt
bash install.sh
```

The ZIP filename is the proposed GitHub release asset name; substitute the actual name if different. The installer is interactive, runs as root and requires a **new** installation.

## 6. Complete the guided installer

The installer first offers a time zone choice and reports local/UTC time and the available clock synchronization state. It then runs five network setup screens:

1. **Access mode:** select **1 localhost**, **2 private LAN**, **3 HTTPS reverse proxy**, or **4 LAN + HTTPS reverse proxy**.
2. **LAN settings:** for modes 2/4, enter the **container's own LAN address**. Add allowed browser client IPs or subnets one at a time: `IP/CIDR: 10.20.30.0/24`, then `Add another? [y/N]:` (press Enter to finish). Do **not** enter the Proxmox host address unless it is actually the address through which the app is accessed.
3. **Proxy settings:** for modes 3/4, enter the public hostname and the actual **source IP/CIDR of your reverse proxy**. The proxy must terminate HTTPS and overwrite forwarded request headers. The installer does **not** provision an Nginx/Traefik/Caddy proxy for you.
4. **Monitoring targets:** add extra private networks the worker is permitted to probe. These are **destination** restrictions and are independent of the dashboard's allowed browser clients.
5. **Review:** confirm `Y`/Enter, enter `r` to restart network configuration, or `q` to cancel.

The installer configures PostgreSQL, Python dependencies, a protected encryption key, and systemd services, then asks whether to start the worker. Choose `n` for a deliberately isolated lab clone and `Y` for a new active monitor deployment.

Create the initial administrator when prompted. Enroll TOTP MFA from the application afterward.

### ICMP / Ping in unprivileged containers

The installer tests ping **as the `upwatch` service user**, not merely as root:

- If ping works, it makes no change.
- If the active ping-group policy is Debian's usual `65534 65534`, it discovers the `upwatch` GID, applies a narrowly scoped rule and saves it in `/etc/sysctl.d/90-upwatch-icmp.conf`, then rechecks ping.
- If a **custom** group policy is active, an existing rule would be overwritten, or the host denies a sysctl change, it **fails with an explicit error** instead of changing the host's security policy.

It does not turn the container privileged, enable raw-network capabilities globally or change the Proxmox host. A host-enforced restriction may still require intervention by the host administrator; truly automatic correction is not always possible under LXC isolation.

## 7. Verify after installation and reboot

Inside the container:

```bash
cat /opt/upwatch/VERSION
systemctl status postgresql upwatch-web upwatch-worker --no-pager
runuser -u upwatch -- /opt/upwatch/.venv/bin/python /opt/upwatch/health_probe.py
id -g upwatch
sysctl net.ipv4.ping_group_range
runuser -u upwatch -- ping -n -c 2 -W 2 127.0.0.1
```

Expected version: **`1.0.0-rc1`**. Web should be active. The worker is active only if enabled during setup. The installer may set a persistent ICMP rule; verify it **again after a container reboot** and test a permitted real target, not just loopback.

If using private LAN access, browse to `http://CONTAINER_IP:8080` from a **permitted** client. If using the reverse proxy, reach the configured external HTTPS hostname after the proxy has been set up separately.

**Firewall:** In the Proxmox and guest firewall, allow inbound TCP 8080 only from your trusted LAN client CIDRs and/or reverse proxy. Do not publicly forward 8080. Outbound DNS, ICMP, UDP, TCP and HTTPS depend on the monitor types you configure.

## 8. Safe test-clone behavior

If this is a test container:

- Keep it separate from production and use test administrator credentials.
- Choose **`n`** for `Enable monitoring worker now?` if real probes and notifications must not run.
- Do not copy a production SMTP configuration into an enabled clone until you have explicitly accepted duplicate-alert risk.
- Remember that a disabled worker also means **no fresh monitor readings**; the web application may still load normally.

To enable a deliberately disabled worker later, **only when intended**:

```bash
systemctl enable --now upwatch-worker
```

To keep it stopped:

```bash
systemctl disable --now upwatch-worker
```

A lab-clone policy that includes additional systemd conditions must be considered separately; these commands do not override the host's restrictions.

## 9. First-run functional tests

1. Log in as the administrator and enable MFA after verifying the host clock.
2. Create a safe ICMP monitor and verify it continues working after a reboot.
3. Add HTTP and TCP checks, and a UDP DNS or NTP probe against a server you control.
4. Configure groups/tags and a restricted Viewer to verify authorization.
5. Check multiple dependency relationships, manual/scheduled maintenance and notification behavior.
6. Configure SMTP only when test emails can safely be delivered.
7. Open scaled measurement charts, availability CSV and audit CSV; test DE/EN and mobile UI.
8. **SNMPv3 remains insufficiently live-tested** in this release.

## 10. Backups and troubleshooting

Proxmox backups/snapshots are useful, but keep an **application-level backup** of PostgreSQL, `/etc/upwatch/upwatch.env` and `/etc/upwatch/vault.key` as well. Verify restoring into an isolated CT before trusting recovery.

For diagnostics:

```bash
systemctl status postgresql upwatch-web upwatch-worker --no-pager
journalctl -u upwatch-web --since '15 minutes ago' --no-pager
journalctl -u upwatch-worker --since '15 minutes ago' --no-pager
runuser -u postgres -- pg_isready -h /var/run/postgresql
```

See [Operations and troubleshooting](OPERATIONS.md) and [Configuration and security](CONFIGURATION.md).

> This is a **release candidate** and the installation is **fresh-only**. If installation stops halfway, do not repeatedly rerun the installer over partial files. Diagnose the error and restore the fresh-CT snapshot or recreate the CT.
