# Sicherheit und Offenlegung

Dieses Paket ist ein **Release Candidate**, keine zertifizierte Sicherheitslösung.

- HTTPS am vertrauenswürdigen Reverse-Proxy erzwingen; Port 8080 nicht ungeschützt im Internet freigeben.
- `ALLOWED_HOSTS`, `UPWATCH_DOMAIN`, `TRUSTED_PROXY_CIDRS` und `MONITOR_ALLOWED_CIDRS` auf die eigene Infrastruktur begrenzen; Monitoring-Targets sind potenzielle SSRF-Ziele.
- Administratoren mit MFA sichern. Gruppen-/Tag-Freigaben **serverseitig** durchsetzen; direkte APIs, Exporte und Historien bei jedem Release auf IDOR prüfen.
- Der SMTP-Versand erfolgt je nach globaler Konfiguration und Benutzer-Alarmempfängern. Empfängersichtbarkeit vor Freigabe prüfen.
- Backups brauchen PostgreSQL-Dump, `/etc/upwatch/vault.key` und `/etc/upwatch/upwatch.env`. Tresorschlüssel und Zugangsdaten niemals in öffentliche Repositories übernehmen.
- Administrative Änderungen und Audit-Daten nach geltenden Datenschutz- und Löschvorgaben behandeln.
- Auf dem Testklon Monitoring-Worker deaktiviert lassen, um keine Doppelalarme auszulösen.
- SNMPv3 wurde noch nicht Ende-zu-Ende live verifiziert; keine abgesicherte Kompatibilitätsgarantie.
- Sicherheitskontakt und Software-Lizenz muss der Rechteinhaber **vor** der öffentlichen Veröffentlichung definieren. Bis dahin keine ungeklärten Nutzungsrechte suggerieren.
