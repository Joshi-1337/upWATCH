# upWATCH! – v1.0.0 RC1

**Erste eigenständige Installationsbasis** (`upWatch-rc1-v1.0.0-DE-EN-clear-wizard.zip`). Dieses Paket enthält den konsolidierten Funktionsstand einschließlich der bisherigen UI- und Berichtsfixes. Frühere interne Entwicklungsnummern sind **keine Voraussetzung** für die Installation.

> **Release Candidate:** Version `1.0.0-rc1` ist noch nicht als Stable freigegeben. SNMPv3 wurde noch nicht auf der Zielinfrastruktur Ende-zu-Ende getestet; die Neuinstallation dieses bereinigten Pakets muss auf einem isolierten System abgenommen werden.

## Funktionen

- HTTP(S)-, ICMP-, TCP-, UDP-DNS/NTP/Custom-, DNS-, WAN-DNS-, TLS-, SSH- und SNMPv3-Monitoring
- Rollen: Administrator, Standortmanager, Viewer; Gruppen- und Tag-Freigaben mit serverseitiger Autorisierung
- Beliebig viele unabhängige Monitor-Abhängigkeiten, alarmbezogene Unterdrückung
- TOTP-MFA mit QR-Code, Sitzungs- und CSRF-Schutz
- Messverläufe mit Achsen und Skalen, Verfügbarkeitsberichte und CSV-Exporte
- SMTP-Status, benutzerbezogene Alarmempfänger, Audit-Protokoll mit CSV
- Wartungsfenster, Fehlerschwellen, Heartbeat und Warnung bei veralteten Messwerten
- Responsives Dark-Mode-Dashboard, bestehende Widget-Abmessungen beibehalten

## Neuinstallation

**Voraussetzung:** Ein frisches Debian-13-System (VM, Host oder Proxmox-LXC), Root-Zugriff sowie Zugriff auf Debian-Paketquellen und PyPI. [REQUIREMENTS.md](REQUIREMENTS.md) und [INSTALLATION.md](INSTALLATION.md) enthalten alle Details.

```bash
unzip upWatch-rc1-v1.0.0-DE-EN-clear-wizard.zip
cd upwatch
sha256sum -c SHA256SUMS.txt
bash install.sh
```

Der Installer legt den Dienstbenutzer, die lokale PostgreSQL-Datenbank, eine Python-Venv, den Verschlüsselungsschlüssel und zwei systemd-Dienste an. Webzugang zunächst nur im geschützten LAN auf TCP 8080; für externen Zugriff HTTPS-Reverse-Proxy einrichten.

**Ausschließlich Neuinstallation:** Das Installationsskript verweigert bestehende upWATCH!-Installationen. Es gibt in dieser Basisdistribution bewusst **keinen Updater** und keine historischen Migrationsskripte. Für spätere Releases wird ein separater Upgradepfad definiert.

## Verzeichnisübersicht

| Datei/Pfad | Zweck |
| --- | --- |
| `install.sh` | Installation auf einem leeren Debian-13-System |
| `postgres_schema.sql` | Vollständiges Datenbankschema der Ausgangsversion |
| `web.py`, `worker.py`, Module, `probes/` | Laufzeitcode |
| `static/` | Oberfläche |
| `systemd/` | Systemdienste |
| `INSTALLATION.md`, `REQUIREMENTS.md`, `SECURITY.md`, `CHANGELOG.md` | Anwenderdokumentation |
| `SHA256SUMS.txt` | Prüfsummenprüfung des entpackten Archivs |

Nicht enthalten: historische Updater, alte Migrationsskripte, Testsuites, interne Checklisten und lokale Backups.

## Betrieb

```bash
cat /opt/upwatch/VERSION
systemctl status upwatch-web upwatch-worker
journalctl -u upwatch-web -n 100 --no-pager
journalctl -u upwatch-worker -n 100 --no-pager
```

Einstellungen liegen in `/etc/upwatch/upwatch.env`; der Tresorschlüssel in `/etc/upwatch/vault.key`. Für eine Wiederherstellung sind beide Konfigurationsdateien und ein PostgreSQL-Backup erforderlich.

**Lizenz:** Noch nicht festgelegt. Eine öffentliche Open-Source-Veröffentlichung erfordert eine Lizenzentscheidung und eine Sicherheitskontaktadresse.

## Languages / Sprachen

The web interface offers **Deutsch** and **English** from the sign-in screen and dashboard header. German is the default. Language selection is stored locally in the browser and can be changed without restarting the services. Translation applies to the interface; user-defined monitor names and incoming probe details are preserved. The installer and initial administrator prompts are in English; the web interface supports German and English. The installer can set an IANA time zone and checks time synchronization for MFA.

