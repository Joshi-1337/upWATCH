# User guide — Dashboard, widgets, monitoring and administration

This guide describes the features present in **upWATCH! v1.0.0-rc1 (DE/EN guided release)**. It is organized around what a user sees in the dashboard and what each monitor actually measures.

> [!IMPORTANT]
> **SNMPv3 is experimental in this release.** Real-device authentication, privacy, discovery and interface rate checks have **not** been sufficiently validated end to end. Never use it as the sole alerting source for a critical device.

## 1. Basic concepts

- **Endpoint / monitor:** A saved test for a network target (URL, hostname, IP, port, DNS zone, etc.).
- **Widget:** The on-screen representation of a monitor, showing its state and most recent measurements. The protocol selection controls **what the widget checks**; widgets do not all mean the same thing.
- **Web service (`upwatch-web`):** Hosts the dashboard, API, users and settings.
- **Worker (`upwatch-worker`):** Runs scheduled monitoring checks and notification processing. If it is stopped, no new check results arrive.
- **Group / tag:** Organizational labels for filtering and assigning monitor visibility. A group or tag can also grant a non-admin user visibility to a monitor.
- **Incident:** An event representing a confirmed outage according to the configured failure threshold.
- **Maintenance:** A period when monitoring can continue but notifications are suppressed.
- **Dependency:** A parent/child relationship used to reduce downstream alerts when an upstream service has failed.

## 2. Statuses and freshness

| Display | Meaning |
| --- | --- |
| **UP / Available** | The most recent check met the selected protocol's success criteria |
| **DOWN** | A check failed or reached a critical condition |
| **WARNING** | A noncritical condition (such as warning latency/certificate age) or an indeterminate UDP timeout |
| **Pending** | There is not yet a completed result |
| **Maintenance** | Alert suppression is active for planned or manual maintenance; checks may continue |
| **Dependency blocked / suppressed** | A parent failure makes a child alert potentially redundant |
| **Stale / Outdated** | Data is no longer fresh; a previous green result must not be interpreted as a current success |

Do not confuse the widget's **immediate check state** with **notification state**. Repeated failures/recoveries and settings determine when incident alarms are sent.

## 3. Dashboard controls

| Control | Action |
| --- | --- |
| **Search** | Find endpoints by displayed information |
| **Filter** | Narrow by state, group and tag; reset to return to all permitted items |
| **Focus / compact view** | Reduce clutter and focus on widgets |
| **Organize** | Create/edit groups, tags and visual assignments (according to role) |
| **Tools** | Open monitor logs, manual checks, dependencies, incidents and maintenance |
| **Add endpoint** | Create a new monitor; administrators and scoped Site Managers only |
| **DE / EN** | Switch the UI language; active choice is highlighted and stored in the browser |
| **Settings** | Account, user management, SMTP, SNMP profiles, audit, alert recipients, reports and appearance, subject to role |

Widgets can be arranged using the supported drag/reorder controls. On smaller screens some tools move to a compact menu; the underlying monitoring settings do not change. User-defined aliases and raw messages from external devices are not automatically translated.

## 4. Creating a monitor

Open **Add endpoint**, enter an **Alias** and **Target**, choose the **monitor type**, and configure the fields that appear for that type. Common fields include:

- **Interval (seconds):** How frequently the worker should check the target; accepted interval range is **10–86,400 seconds**.
- **Timeout (seconds):** How long a check may take; minimum **1 second**, must be smaller than the interval.
- **Warning/critical latency:** Optional millisecond thresholds. A successful but slow check can become WARNING or DOWN.
- **Consecutive failure threshold:** How many failures confirm a DOWN notification; typical default **3**, configurable **1–20**.
- **Recovery threshold:** How many consecutive successful checks are needed to confirm recovery; typical default **2**, configurable **1–20**.
- **DOWN/UP emails:** Optional notification settings, effective only if outgoing SMTP is correctly configured and authorized recipients exist.
- **Maintenance:** Suppress alerts while still collecting measurements.
- **Group/tags:** Organize widgets and control scoped user visibility.

Save and wait for the worker to run the check. If you deliberately disabled the worker on a test container, a saved monitor will not automatically produce measurements until the worker is enabled.

## 5. Monitor types and widget configuration

### ICMP / Ping

**Purpose:** Test IP host reachability, including routers, gateways, hypervisors and service hosts.

- **Target:** IPv4/IPv6 address or resolvable hostname, such as `10.20.30.1`.
- **Success:** An ICMP echo reply was received within the timeout.
- **Limit:** A successful ping does **not** prove HTTP, SSH or a specific application is healthy. Firewalls may block ping on a working host.
- **Requirement:** Linux service account must have working ICMP echo permissions. The installer checks and, for the standard Debian policy, configures these automatically; Proxmox host restrictions may still apply.

### TCP (port checker)

**Purpose:** Confirm that a TCP listener is accepting connections.

- **Target:** Hostname or IP, e.g. `db.example.net`; **port** e.g. `5432`, `22` or `443`.
- **Success:** TCP connection completes.
- **Limit:** This is a transport test, not proof the higher-level protocol works. Use HTTP, SMTP, SSH, etc. for protocol-level checks when appropriate.

### UDP (service response checker)

**Purpose:** Verify selected UDP services without falsely reporting a silent port as open.

- **Profiles:**
  - **DNS:** Choose host/port, DNS test name; requires a valid DNS reply.
  - **NTP:** Choose host/port, usually UDP `123`; validates an NTP response matching the request.
  - **Custom:** Supply a **hexadecimal request payload** and the expected **hexadecimal reply prefix**; useful only for services with a known request/response protocol.
- **Success:** The response is received and passes validation.
- **No response:** **WARNING / indeterminate**, not UP. A silent UDP port might be open, filtered or unavailable.
- **Definite error:** Can result in DOWN, depending on the response/error.
- **Limit:** A universal protocol-independent UDP open-port test is not reliable.

### HTTP / HTTPS

**Purpose:** Monitor websites, APIs and web application endpoints.

- **Target:** Full URL with `http://` or `https://`, e.g. `https://service.example.net/health`.
- **Success:** Expected HTTP status (default **200**) and optional content condition are satisfied.
- **Body checks:** No condition, text **contains**, text **does not contain**, or JSON field/path comparison.
- **Limit:** A 200 OK does not prove an entire application is healthy unless you use a meaningful health endpoint. HTTP redirects are not automatically followed by this checker. Response bodies are size-limited.

### DNS

**Purpose:** Check DNS resolution and optionally verify expected records.

- **Target:** Domain/hostname, such as `service.example.net`.
- **Record types:** `A`, `AAAA`, `MX`, `TXT`, `NS`, `CNAME`.
- **Success:** Requested record exists; if expected values are supplied, all specified values must appear among the answers.
- **Limit:** Resolution results can depend on resolver, caching, propagation and split-horizon DNS.

### DDNS / CNAME bundle

**Purpose:** Validate a dynamic-DNS hostname and the CNAME aliases that should point to it.

- **Target:** Central DDNS hostname, e.g. `dynamic.example.net`.
- **Subdomains:** One alias hostname per line, up to **30**; all should point via CNAME to the target.
- **Resolver choice:** System resolver, Cloudflare or Google, subject to network policy.
- **Optional WAN comparison:** Compare the DDNS IPv4 address with the detected public egress IPv4.
- **Limit:** External egress IP can differ from the public address of a particular router or site when VPNs, proxies, CGNAT or multiple WAN links are used.

### WAN DNS / wildcard DNS

**Purpose:** Validate WAN-IP-related DNS records, including wildcard hostnames.

- **Target:** Wildcard hostname like `*.example.net` **or** a concrete hostname.
- **What is checked:** For a wildcard, the app tests a newly generated hostname under the wildcard; additional configured names are also resolved. Returned A records are compared with the measured **public IPv4 address of the upWATCH host's outbound connection**.
- **Success:** Checked names match that observed egress IPv4.
- **Limit:** The detected egress IP is **not necessarily the router IP you intend to publish**. With CGNAT, VPN, proxy or multi-WAN routing, interpret mismatches carefully. No DNS provider credentials are required for this read-only verification.

### TLS certificate

**Purpose:** Check a TLS endpoint and warn about certificate expiration.

- **Target:** TLS hostname, optionally a port (default `443`).
- **Success:** TLS handshake and certificate validation succeed, with sufficient remaining certificate lifetime.
- **Thresholds:** Warning/critical expiry thresholds are configurable; typical defaults in the engine are **30 days warning / 7 days critical**.
- **Limit:** This is not a full TLS security audit. It reports certificate/handshake conditions relevant to the monitor.

### SSH

**Purpose:** Check if a service presents an SSH protocol banner.

- **Target:** SSH host and port (typically `22`).
- **Success:** Banner starts with the SSH 2.0 protocol marker.
- **Limit:** The checker **does not authenticate**, run commands or prove that a particular account can log in.

### SMTP service

**Purpose:** Check an SMTP listener's protocol response.

- **Target:** Mail host and port (commonly `587`; choose the port appropriate to your endpoint).
- **Success:** SMTP `220` greeting and successful `EHLO` (`250`), with optional `STARTTLS` negotiation and TLS validation.
- **Limit:** This widget is **not** the outgoing SMTP notification sender and does **not** send a test email as its service probe. For notification delivery, see Settings → Email / SMTP.

### SNMPv3 — experimental

**Purpose:** Query network appliances using SNMPv3 credentials and inspect a configured OID or interface counters.

- **Setup:** Administrators create an SNMPv3 credential profile under **Settings → SNMPv3 Profiles**; select it from the monitor editor.
- **Modes:** OID/value query, or interface mode using an interface index and applicable SNMP counters. The application includes related discovery/measurement support.
- **Security:** SNMP secrets are stored encrypted using the application vault key.
- **Critical limitation:** Authentication/privacy combinations, supported devices, counter wrap and real-world interface rates **have not been sufficiently validated end to end**. Verify against devices under your control before operational use.

## 6. Groups, tags and multi-user permissions

Create **groups and tags** from the organization controls, and assign them to widgets. Administrators can create users in **Settings → Users** and select one of the roles:

| Role | Permissions |
| --- | --- |
| **Administrator** | All monitors; user management; global SMTP/SNMP/settings; exports and administrative audit |
| **Site Manager** | Create/edit/delete and manage dependent monitors **inside assigned scope**, read allowed history |
| **Viewer** | Read only assigned widgets, history and permitted reports |

For a non-admin user, visibility is granted when **the monitor's group OR at least one of its tags** is assigned to that user. A user with **no grants** sees no monitors. The user management UI offers checkboxes, select all/none, collapsible user forms and a preview of visible monitors. Authorization is enforced by the server; hiding a card on the client is not the security mechanism.

**Example:** A user given group `Site-A` and tag `Mail` can see Site-A monitors plus monitors carrying Mail, even if those are in another group. Grant tags carefully when segregating sites.

## 7. Dependencies and notification suppression

Open **Tools → Dependencies**. Select a **parent** monitor (e.g. a reverse proxy) and choose one or more **dependent child** monitors. You can create **multiple independent parent/child sets**:

```text
Proxy A ──┬── Application A
          └── Application B

Proxy B ──┬── Application C
          └── Application D
```

If an upstream monitor fails, the application's dependency logic can suppress downstream alerts that would otherwise be duplicates. The children may continue being checked and recording results. Cyclic dependencies are rejected. Test the exact alert behavior and recovery sequence for your topology before relying on suppression.

## 8. Maintenance windows

Use **Tools → Maintenance windows** or a monitor's manual maintenance setting to suppress notifications during planned work. One-time and recurring daily/weekly windows are supported. Monitoring data continues to be useful during a maintenance period. Confirm the configured start/end times and time zone, especially after any clock/time-zone changes.

## 9. SMTP notifications, recipients and delivery status

- **Settings → Email / SMTP:** configure the outgoing SMTP server, sender and global notification recipient; run the test feature.
- **Settings → Alert recipients:** administrators may configure per-user email recipients and DOWN/UP preferences. Per-user recipients are selected according to the user's current group/tag visibility.
- **Delivery history:** view the outbox by status (sent, pending, sending, failed), subject, recipient, attempt count and error details. A successfully queued notification is **not** the same as a delivered message.
- **Per-monitor settings:** enable DOWN and/or recovery notifications, and adjust consecutive failure/recovery thresholds.

Be careful when testing clones: a second enabled worker can duplicate notifications.

## 10. History, availability, incidents and audit

**Measurement history:** Open a widget's **history/trends** panel. Charts show date/time on the X-axis and labeled Y-axis values (milliseconds for latency, 0–100% for success ratios), gridlines and point details. Gaps should not be interpreted as successful checks.

**Availability reports:** In **Settings → Availability**, select **7, 30, 90 or 365 days**, click **Refresh report**, or download CSV. Figures include successful-check ratio, average latency and confirmed incident count/duration. The success percentage is a **ratio of successful checks**, **not a time-weighted contractual uptime/SLA percentage**.

**Incidents and events:** View confirmed outages, recovery and recent monitoring activity. A threshold-confirmed incident is different from one isolated failing probe.

**Audit log:** Administrators can inspect recorded user actions and download CSV. Protect audit data as operationally sensitive information. Avoid using passwords, secrets or recovery codes as object names.

## 11. Appearance, language and mobile layout

- **Settings → Appearance:** configure available visual customization, including the logo/appearance where exposed by the UI.
- **DE / EN toggle:** choose German or English at login and in the dashboard header. The active language is highlighted and stored in the browser.
- **Mobile:** Widgets stack and controls reflow for smaller screens; some settings tabs scroll horizontally. A desktop responsive test does not replace checking your specific iPhone/Android browser.

## 12. Backup, data retention and safe operations

Monitoring data is stored in PostgreSQL. Configurable retention is applied by the worker to raw checks, hourly/daily summaries, events, audits and delivery history. **Disabling the worker also stops its background retention tasks.** Keep database dumps and `/etc/upwatch/vault.key` together for recovery. See [Operations](OPERATIONS.md).
