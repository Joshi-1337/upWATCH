# Systemvoraussetzungen

## Unterstützte Zielumgebung

- **Debian GNU/Linux 13**, `systemd`, `amd64` oder andere von den benötigten Python-Paketen unterstützte Architektur
- Root-Zugriff für den Installer; stabiler Netzwerkzugang für `apt` und Python-Paketinstallation
- **Lokales PostgreSQL** (Debian 13 liefert üblicherweise PostgreSQL 17), UTF-8-Datenbank, Peer-Authentifizierung des Dienstbenutzers `upwatch`
- Python 3 mit `venv`; die Installation erstellt `/opt/upwatch/.venv`
- Ein Linux-Host, eine VM oder ein Proxmox-LXC; bei **unprivilegierten** LXC müssen Netzwerk- und ICMP-Rechte gesondert getestet werden

## Ressourcen

Eine feste allgemeingültige Mindestgröße ist für die Zahl und Häufigkeit der Monitore **nicht belastbar ermittelt**. Für kleine Testumgebungen können 2 vCPU, 2 GB RAM und 10 GB freier Speicher ein Ausgangspunkt sein, **keine getestete Garantie**. PostgreSQL-Retention, Anzahl Monitore und Polling-Intervalle bestimmen den tatsächlichen Bedarf.

## Pakete und Python-Abhängigkeiten

`install.sh` installiert mit APT: `python3`, `python3-venv`, `ca-certificates`, `iputils-ping`, `postgresql`, `postgresql-client`.

Im isolierten Python-Environment werden installiert:

| Python-Paket | Vom Installer festgelegter Bereich |
| --- | --- |
| `gunicorn` | `>=23,<24` |
| `cryptography` | `>=43,<47` |
| `pysnmp` | `>=7.1,<7.2` |
| `dnspython` | `>=2.6,<3` |
| `psycopg[binary]` | `>=3.2,<4` |
| `qrcode` | `>=8,<9` |

**Node.js ist im Produktivbetrieb nicht erforderlich.** Für den Installer gibt es keinen Frontend-Buildschritt.

## Netzwerk und Sicherheit

- Webdienst: **TCP 8080** (standardmäßig auf `0.0.0.0` gebunden); nur aus vertrauenswürdigen Netzen/reverse Proxy erreichbar machen
- Monitoring-Probes benötigen je nach Protokoll ausgehenden TCP-/UDP-/ICMP-Zugriff
- PostgreSQL wird über lokalen Unix-Socket genutzt; keine externe PostgreSQL-Freigabe nötig
- Für Internetzugriff **HTTPS-Reverse-Proxy** mit korrekt gesetzten Host-/Proxy-Freigaben verwenden
- In der Datei `/etc/upwatch/upwatch.env` sind `UPWATCH_DOMAIN`, `ALLOWED_HOSTS`, `TRUSTED_PROXY_CIDRS` sowie ggf. `MONITOR_ALLOWED_CIDRS` passend zur Umgebung zu setzen. **Keine fremden Beispiel-IP-Adressen blind übernehmen.**

### ICMP in unprivilegierten Proxmox-LXC

Bei unprivilegierten Containern kann `ping` als `root` funktionieren und als Dienstbenutzer `upwatch` scheitern. Der Installer prüft ICMP und passt bei Debian-Standardwerten `65534 65534` die Freigabe automatisch und dauerhaft auf die dynamisch ermittelte upwatch-GID an. Eigene Kernel-Richtlinien werden nicht überschrieben. Zur Kontrolle:

```bash
id -g upwatch
runuser -u upwatch -- ping -n -c 1 -W 2 127.0.0.1
sysctl net.ipv4.ping_group_range
```

Eine Anpassung von `net.ipv4.ping_group_range` nur nach Prüfung des bestehenden Gruppenbereichs und der Sicherheitsrichtlinien durchführen. Vorhandene Berechtigungen dürfen nicht unbeabsichtigt überschrieben werden. Im LXC kann die Hostkonfiguration relevant sein; **kein privilegierter Container und keine pauschalen RAW-Capabilities erforderlich**.

## Clock and time zone

Accurate system time is necessary for TOTP multi-factor authentication and consistent monitoring timestamps. The interactive installer detects the local and UTC time, offers an optional IANA zone setting (`UTC`, `Europe/Berlin`, etc.) and reports NTP synchronization status when available. Time-zone configuration changes *presentation*, not clock accuracy. In Proxmox LXC, configure/synchronize time on the Proxmox host; the container inherits its clock. Synchronization checks that cannot be confirmed require explicit acknowledgement.
