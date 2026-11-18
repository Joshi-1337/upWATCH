# Changelog

## 1.0.0-rc1 — 2026-11-18

Initial standalone release candidate intended **only for fresh Debian 13 installations**.

- Consolidated endpoint monitoring, including UDP checks, SNMPv3 profiles, multi-user roles, and monitor dependencies.
- Worker heartbeat, stale-check detection, configurable failure/recovery thresholds.
- Scaled measurement history, availability reports with CSV export, audit log, and maintenance windows.
- SMTP delivery status and per-user notification recipients.
- Mobile and responsive layout refinements; historical hotfixes integrated into the baseline.
- German/English web interface with persistent per-browser language preference.
- **English-language installer and initial administrator prompts.**
- **Interactive time-zone selection and system clock/NTP status check** for accurate MFA and incident timestamps. Proxmox LXC host synchronization is documented.
- Historical updaters, migration files, development test suites, and internal release checklists removed from the distribution.

**Not yet accepted for stable:** Fresh installation of this exact archive on a clean Debian 13 target; comprehensive real-device SNMPv3 end-to-end validation.

- Guided first-install network configuration (LAN/HTTPS proxy/localhost), monitoring CIDR validation, service-user ICMP diagnostics, optional disabled worker for isolated test systems.
- Fixed duplicate language picker on login by removing duplicate `i18n.js` script and CSS insertions.

- Installer: four independent access modes, including simultaneous direct-LAN and HTTPS reverse proxy; individual IP/CIDR allowlist entry with No as the default continuation choice.

- Installer: automatically configure and verify dedicated service-user ICMP permissions on fresh Debian 13 systems where the default ping group policy can be replaced safely; reject custom or host-denied policies rather than continuing with broken ping.

- Replaced language dropdowns with synchronized DE/EN segmented toggles on sign-in and dashboard.
- Kept account system-information text consistently in English.
