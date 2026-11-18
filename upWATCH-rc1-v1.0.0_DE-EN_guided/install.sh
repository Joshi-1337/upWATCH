#!/usr/bin/env bash
# Fresh v1.0.0 RC1 installation on Debian 13 (host, VM, or LXC).
set -Eeuo pipefail
umask 077
cd "$(dirname "$(readlink -f "$0")")"
[[ $EUID -eq 0 && -t 0 ]] || { echo 'An interactive root shell is required.' >&2; exit 1; }
. /etc/os-release
[[ ${ID:-} == debian && ${VERSION_ID:-} == 13 && -d /run/systemd/system ]] || { echo 'Debian 13 with systemd is required.' >&2;exit 1; }
[[ ! -e /opt/upwatch && ! -e /etc/upwatch/upwatch.env && ! -e /etc/systemd/system/upwatch-web.service ]] || { echo 'Existing upWATCH! installation detected. This is a fresh-install-only release; use a clean Debian 13 system.' >&2; exit 1; }
# ANSI colors are only enabled for interactive terminals that support them.
# Respect NO_COLOR (https://no-color.org/) and keep redirected logs plain.
UI_RESET='' UI_TITLE='' UI_PROMPT='' UI_OK='' UI_WARNING=''
if [[ -t 1 && -z ${NO_COLOR+x} && ${TERM:-dumb} != dumb ]]; then
  UI_RESET=$'\033[0m'
  UI_TITLE=$'\033[1;36m'    # bold cyan: section headings
  UI_PROMPT=$'\033[1;33m'   # bold yellow: interactive questions
  UI_OK=$'\033[1;32m'       # bold green: accepted entries
  UI_WARNING=$'\033[1;31m'  # bold red: errors (reserved)
fi

# Print an emphasized question while preserving read -r semantics.
ui_ask() {
  local question=$1 variable=$2
  read -r -p "${UI_PROMPT}${question}${UI_RESET}" "$variable"
}

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3 python3-venv ca-certificates iputils-ping postgresql postgresql-client tzdata
# Time zone affects local dashboard timestamps; accurate UTC clock affects TOTP/MFA.
# A Proxmox LXC shares the host's system clock and must be synchronized on the host.
check_system_time() {
  echo
  printf '%s=== System time and time zone ===%s\n' "$UI_TITLE" "$UI_RESET"
  local current_zone current_utc container sync_state requested_zone answer
  current_zone=$(timedatectl show -p Timezone --value 2>/dev/null || true)
  if [[ -z $current_zone ]]; then
    current_zone=$(cat /etc/timezone 2>/dev/null || true)
  fi
  [[ -n $current_zone ]] || current_zone='UTC (not detected)'
  printf 'Current time zone: %s\n' "$current_zone"
  printf 'Current local time: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z %z')"
  printf 'Current UTC time: %s\n' "$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
  container='no'
  if command -v systemd-detect-virt >/dev/null 2>&1 && systemd-detect-virt --quiet --container; then
    container='yes'
    echo 'Container environment detected: the system clock is managed by the host.'
  fi
  echo 'Enter an IANA time zone (e.g. Europe/Berlin or UTC), or press Enter to keep the current setting.'
  ui_ask 'Time zone [keep current]: ' requested_zone
  if [[ -n $requested_zone ]]; then
    # Only zoneinfo identifiers are accepted; never allow paths or traversal.
    if [[ ! $requested_zone =~ ^[A-Za-z0-9_+.-]+(/[A-Za-z0-9_+.-]+)*$ || $requested_zone == *'..'* || ! -f /usr/share/zoneinfo/$requested_zone ]]; then
      echo "ERROR: Invalid IANA time zone: $requested_zone" >&2
      exit 1
    fi
    if ! timedatectl set-timezone "$requested_zone" 2>/dev/null; then
      # Setting the local timezone via /etc/localtime remains possible in many LXCs
      # where timedatectl's D-Bus method is unavailable.
      ln -sfn "/usr/share/zoneinfo/$requested_zone" /etc/localtime
      printf '%s\n' "$requested_zone" > /etc/timezone
    fi
    echo "Time zone configured: $requested_zone"
  fi
  printf 'Effective local time: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z %z')"
  sync_state=$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)
  case "$sync_state" in
    yes)
      echo 'OK: System time synchronization is reported as active.' ;;
    no)
      echo 'WARNING: systemd reports that the clock is NOT synchronized.' >&2 ;;
    *)
      echo 'WARNING: Clock synchronization could not be verified automatically.' >&2 ;;
  esac
  if [[ $container == yes ]]; then
    echo 'LXC note: verify NTP/chrony synchronization on the Proxmox HOST; changing the container time zone does not synchronize its clock.'
  elif [[ $sync_state != yes ]]; then
    echo 'Check the host NTP service (systemd-timesyncd, chrony, or equivalent) before using TOTP/MFA.'
  fi
  if [[ $sync_state != yes ]]; then
    echo 'Incorrect UTC time can invalidate authenticator codes and distort monitoring timestamps.'
    ui_ask 'Continue despite unverified/unsynchronized clock? [y/N]: ' answer
    [[ $answer == y || $answer == Y ]] || { echo 'Installation stopped. Correct time synchronization first.' >&2; exit 1; }
  fi
  echo 'Time configuration check completed.'
  echo
}
check_system_time
systemctl enable --now postgresql
pg_isready -q -h /var/run/postgresql || { echo "PostgreSQL cluster is not ready." >&2; exit 1; }
runuser -u postgres -- psql -XAt -d postgres -c 'SELECT 1' | grep -qx 1
if ! getent passwd upwatch >/dev/null; then useradd --system --home-dir /opt/upwatch --shell /usr/sbin/nologin --user-group upwatch; fi
install -d -o root -g upwatch -m 0750 /opt/upwatch
install -d -o upwatch -g upwatch -m 0700 /var/lib/upwatch
install -d -o root -g upwatch -m 0750 /etc/upwatch
runuser -u postgres -- psql -XAt -d postgres -c "SELECT 1 FROM pg_roles WHERE rolname='upwatch'" | grep -qx 1 || runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 -d postgres -c 'CREATE ROLE upwatch LOGIN'
# A fresh cluster can default to SQL_ASCII; never inherit its encoding.  C locale
# plus template0 permits an explicitly UTF8 database even on such clusters.
if runuser -u postgres -- psql -XAt -d postgres -c "SELECT 1 FROM pg_database WHERE datname='upwatch'" | grep -qx 1; then
  echo 'Database upwatch already exists. Only fresh installations are supported.' >&2
  exit 1
fi
runuser -u postgres -- createdb --owner=upwatch --template=template0 --encoding=UTF8 --lc-collate=C --lc-ctype=C upwatch
DB_ENCODING=$(runuser -u postgres -- psql -XAt -d postgres -c "SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname='upwatch'")
[[ $DB_ENCODING == UTF8 ]] || { echo "ERROR: Database encoding must be UTF8 (found: $DB_ENCODING)." >&2; exit 1; }
# Refuse an existing non-empty database; never destroy someone else's data.
runuser -u upwatch -- psql -XAt -d upwatch -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'" | grep -qx 0 || { echo 'PostgreSQL database is not empty.' >&2;exit 1; }
cp -a web.py worker.py storage.py access.py postgres_schema.sql health_probe.py security.py manage.py vault.py notifications.py operational.py mfa.py maintenance.py snmp_metrics.py snmp_discovery.py incidents.py diagnostics.py probes static VERSION /opt/upwatch/
chown -R root:upwatch /opt/upwatch
find /opt/upwatch -type d -exec chmod 0750 {} +
find /opt/upwatch -type f -exec chmod 0640 {} +
python3 -m venv /opt/upwatch/.venv
( umask 022; /opt/upwatch/.venv/bin/python -m pip install --disable-pip-version-check 'gunicorn>=23,<24' 'cryptography>=43,<47' 'pysnmp>=7.1,<7.2' 'dnspython>=2.6,<3' 'psycopg[binary]>=3.2,<4' 'qrcode>=8,<9' )
/opt/upwatch/.venv/bin/python -m pip check
# Capture the exact installed versions for reproducible incident analysis.
install -d -o root -g root -m 0700 /var/lib/upwatch/release-backups
/opt/upwatch/.venv/bin/python -m pip freeze > /var/lib/upwatch/release-backups/initial-python-packages.txt
chmod 0600 /var/lib/upwatch/release-backups/initial-python-packages.txt
chgrp -R upwatch /opt/upwatch/.venv
chmod -R g+rX /opt/upwatch/.venv
# Guided access setup. Never mix browser clients, trusted proxies and probe targets.
# Inputs are validated and displayed for approval before any configuration is written.
validate_network_input() {
  local field="$1" value="$2"
  FIELD="$field" VALUE="$value" python3 - <<'PYVALID'
import ipaddress, os, re, sys
field, value = os.environ['FIELD'], os.environ['VALUE'].strip()
try:
    if field == 'lan':
        ip = ipaddress.ip_address(value)
        if not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
            raise ValueError('Use a private, non-loopback address assigned to this server')
    elif field == 'domain':
        if len(value) > 253 or '.' not in value or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p) for p in value.split('.')):
            raise ValueError('Enter a DNS hostname without https:// or a port')
    else:
        raise ValueError('Unsupported validation type')
except ValueError as exc:
    print(f'Invalid value: {exc}.', file=sys.stderr)
    sys.exit(1)
PYVALID
}

normalize_allow_entry() {
  local value="$1"
  VALUE="$value" python3 - <<'PYIP'
import ipaddress, os, sys
raw = os.environ['VALUE'].strip()
try:
    if not raw or any(c in raw for c in ',; \t\r\n'):
        raise ValueError('Enter one IP address or one CIDR network per line')
    net = ipaddress.ip_network(raw, strict=False)
    if net.is_multicast or net.is_unspecified or net.is_link_local:
        raise ValueError('Multicast, unspecified and link-local ranges are not allowed')
    print(net.with_prefixlen)
except ValueError as exc:
    print(f'Invalid IP/CIDR: {exc}.', file=sys.stderr)
    sys.exit(1)
PYIP
}

wizard_section() {
  printf '\n%s\n' '------------------------------------------------------------'
  printf '  %s%s%s\n' "$UI_TITLE" "$1" "$UI_RESET"
  printf '%s\n' '------------------------------------------------------------'
}

# Collect comma-separated values for the environment, but request one entry at
# a time. Enter at the continuation prompt always means NO. Optional lists can
# be empty by pressing Enter on the first entry.
collect_allowlist() {
  local title="$1" result_var="$2" required="$3"
  local entry normalized again
  local -a entries=()
  echo "$title"
  echo '  One IPv4/IPv6 address or CIDR per entry (e.g. 10.0.0.5 or 10.0.0.0/24).'
  [[ $required == no ]] && echo '  Optional: press Enter without an entry to skip.'
  while :; do
    ui_ask '  IP/CIDR: ' entry
    if [[ -z $entry && $required == no && ${#entries[@]} -eq 0 ]]; then
      echo '  No entries added.'
      break
    fi
    if ! normalized=$(normalize_allow_entry "$entry"); then
      echo '  Please try again.' >&2
      continue
    fi
    if [[ " ${entries[*]} " == *" $normalized "* ]]; then
      echo '  Already included; duplicate skipped.'
    else
      entries+=("$normalized")
      printf '  %sAdded:%s %s\n' "$UI_OK" "$UI_RESET" "$normalized"
    fi
    ui_ask '  Add another? [y/N]: ' again
    [[ ${again,,} == y ]] || break
  done
  local joined
  joined=$(IFS=,; echo "${entries[*]}")
  printf -v "$result_var" '%s' "$joined"
}

configure_network() {
  local mode lan_host lan_clients public_domain proxy_cidrs target_cidrs
  local need_lan need_proxy selection confirmed
  while :; do
    mode='' lan_host='' lan_clients='' public_domain='' proxy_cidrs='' target_cidrs=''
    need_lan=no need_proxy=no
    wizard_section 'STEP 1/5 - How should users access upWATCH!?'
    echo '  1) Localhost only                - access from this machine'
    echo '  2) Private LAN                  - selected IPs/subnets via HTTP'
    echo '  3) HTTPS reverse proxy only     - access using a public hostname'
    echo '  4) Private LAN + HTTPS proxy    - both methods'
    echo
    while :; do
      ui_ask '  Choose access mode [2]: ' selection
      mode=${selection:-2}
      case "$mode" in
        1) break ;;
        2) need_lan=yes; break ;;
        3) need_proxy=yes; break ;;
        4) need_lan=yes; need_proxy=yes; break ;;
        *) echo '  Enter 1, 2, 3 or 4.' ;;
      esac
    done
    wizard_section 'STEP 2/5 - Direct LAN access'
    if [[ $need_lan == yes ]]; then
      echo '  This is the IP address OF THE upWATCH SERVER (not your PC).'
      printf '  Detected server addresses: %s\n' "$(hostname -I 2>/dev/null || echo 'unknown')"
      while :; do
        ui_ask '  Server LAN IP: ' lan_host
        if validate_network_input lan "$lan_host"; then break; fi
      done
      echo
      echo '  Which computers/networks may open the dashboard over LAN?'
      collect_allowlist '  Allowed browser clients:' lan_clients yes
    else
      echo '  Skipped - no direct LAN browser access selected.'
    fi
    wizard_section 'STEP 3/5 - HTTPS reverse proxy'
    if [[ $need_proxy == yes ]]; then
      echo '  Enter the publicly used hostname, without https://.'
      while :; do
        ui_ask '  Public hostname: ' public_domain
        public_domain=${public_domain,,}
        if validate_network_input domain "$public_domain"; then break; fi
      done
      echo
      echo '  Only enter the IP of YOUR REVERSE PROXY (not all internet users).'
      collect_allowlist '  Trusted proxy source IPs/CIDRs:' proxy_cidrs yes
      echo '  IMPORTANT: Reverse proxy must overwrite forwarded headers and use HTTPS.'
    else
      echo '  Skipped - no reverse proxy selected.'
    fi
    wizard_section 'STEP 4/5 - Monitoring targets'
    echo '  Which private IPs/networks may the monitoring worker PROBE?'
    echo '  This is NOT a list of users allowed to open the dashboard.'
    echo '  Public targets follow the application security rules.'
    collect_allowlist '  Additional allowed monitoring targets:' target_cidrs no
    wizard_section 'STEP 5/5 - Review configuration'
    case "$mode" in
      1) echo '  Access mode:             Localhost only' ;;
      2) echo '  Access mode:             Private LAN' ;;
      3) echo '  Access mode:             HTTPS proxy only' ;;
      4) echo '  Access mode:             Private LAN + HTTPS proxy' ;;
    esac
    [[ $need_lan == yes ]] && {
      printf '  Server LAN IP:           %s\n' "$lan_host"
      printf '  Allowed browser clients: %s\n' "$lan_clients"
    }
    [[ $need_proxy == yes ]] && {
      printf '  Public hostname:         %s\n' "$public_domain"
      printf '  Trusted proxy sources:   %s\n' "$proxy_cidrs"
    }
    printf '  Additional probe nets:   %s\n' "${target_cidrs:-none}"
    echo
    echo '  Security: Private LAN access is unencrypted HTTP; restrict TCP 8080'
    echo '  with a firewall. A reverse proxy must terminate HTTPS securely.'
    echo
    ui_ask '  Apply these settings? [Y/r=restart/q=quit]: ' confirmed
    case "${confirmed,,}" in
      ''|y|yes) break ;;
      r|restart) echo '  Restarting network setup...' ;;
      q|quit) echo '  Installation cancelled before network configuration.' >&2; return 1 ;;
      *) echo '  Please enter Y, r or q. Restarting network setup...' ;;
    esac
  done
  ACCESS_MODE="$mode"
  install -d -o root -g upwatch -m 0750 /etc/upwatch
  {
    printf '%s\n' '# upWATCH! v1.0.0-rc1 - generated by install.sh' \
      'UPWATCH_POSTGRES_DSN="dbname=upwatch user=upwatch host=/var/run/postgresql"' \
      'ALLOWED_HOSTS=localhost,127.0.0.1,[::1]'
    if [[ $need_lan == yes ]]; then
      printf 'UPWATCH_LAN_HOST=%s\nUPWATCH_LAN_CLIENT_CIDRS=%s\n' "$lan_host" "$lan_clients"
    fi
    if [[ $need_proxy == yes ]]; then
      printf 'UPWATCH_DOMAIN=%s\nTRUSTED_PROXY_CIDRS=%s\n' "$public_domain" "$proxy_cidrs"
    fi
    if [[ -n $target_cidrs ]]; then
      printf 'MONITOR_ALLOWED_CIDRS=%s\n' "$target_cidrs"
    fi
  } > /etc/upwatch/upwatch.env
  chown root:upwatch /etc/upwatch/upwatch.env
  chmod 0640 /etc/upwatch/upwatch.env
  echo 'OK: Network settings written to /etc/upwatch/upwatch.env.'
}

configure_network

chown root:upwatch /etc/upwatch/upwatch.env
chmod 0640 /etc/upwatch/upwatch.env
runuser -u upwatch -- /opt/upwatch/.venv/bin/python - <<'PY'
import psycopg, dns.resolver, cryptography, pysnmp, gunicorn
from qrcode.image.svg import SvgPathImage
import sys
sys.path.insert(0,'/opt/upwatch')
from storage import create_schema, db, assert_utf8
assert_utf8()
create_schema()
with db() as c:
    c.execute("INSERT INTO widget_tags(id,name,color) VALUES('tag-produktiv','Produktiv','#48a9e6') ON CONFLICT DO NOTHING")
PY
/opt/upwatch/.venv/bin/python - <<'PY'
from cryptography.fernet import Fernet
from pathlib import Path
with Path('/etc/upwatch/vault.key').open('xb') as f:f.write(Fernet.generate_key()+b'\n')
PY
chown root:upwatch /etc/upwatch/vault.key
chmod 0640 /etc/upwatch/vault.key
# Configure ICMP echo sockets for the dedicated service group when needed.
# Do not widen the kernel group range or give the worker CAP_NET_RAW.
# Non-default host/container policies are left untouched and fail closed.
configure_service_icmp() {
  echo
  printf '%s=== ICMP echo access (automatic) ===%s\n' "$UI_TITLE" "$UI_RESET"
  local gid raw first last rule_file temp_file old_range
  gid=$(id -g upwatch)
  if runuser -u upwatch -- ping -n -c 1 -W 2 127.0.0.1 >/dev/null 2>&1; then
    echo 'OK: ICMP works as the upwatch service user; no change needed.'
    return 0
  fi

  raw=$(sysctl -n net.ipv4.ping_group_range 2>/dev/null) || {
    echo 'ERROR: Cannot read net.ipv4.ping_group_range. The container/host must permit ICMP echo sockets.' >&2
    return 1
  }
  read -r first last <<< "$raw"
  if [[ ! $first =~ ^[0-9]+$ || ! $last =~ ^[0-9]+$ ]]; then
    echo "ERROR: Invalid kernel ping group range: $raw" >&2
    return 1
  fi
  old_range="$first $last"
  printf 'Service GID: %s; current kernel range: %s\n' "$gid" "$old_range"

  if (( first <= gid && gid <= last )); then
    echo 'ERROR: The service GID is already permitted, but ICMP still fails.' >&2
    echo 'Check LXC restrictions, ping availability and loopback/network policies.' >&2
    return 1
  fi
  # 65534..65534 is Debian's usual restricted default. Overwriting a
  # custom range would silently revoke access; expanding it to include a
  # distant GID would grant ping access to unrelated users. Neither is safe.
  if [[ $first != 65534 || $last != 65534 ]]; then
    echo "ERROR: Custom ping group policy ($old_range) detected; refusing to replace or widen it." >&2
    echo 'An administrator must reconcile this existing host/container policy.' >&2
    return 1
  fi

  rule_file=/etc/sysctl.d/90-upwatch-icmp.conf
  if [[ -e $rule_file ]]; then
    echo "ERROR: Existing ICMP configuration file found: $rule_file; refusing to overwrite." >&2
    return 1
  fi
  echo "Configuring ICMP echo access for upwatch GID $gid (replaces the Debian default GID 65534 rule)."
  # Use a mode-restricted temporary file, then atomically put it into place.
  temp_file=$(mktemp /etc/sysctl.d/.upwatch-icmp.XXXXXXXX)
  printf 'net.ipv4.ping_group_range = %s %s\n' "$gid" "$gid" > "$temp_file"
  chmod 0644 "$temp_file"
  if ! sysctl -w "net.ipv4.ping_group_range=$gid $gid" >/dev/null 2>&1; then
    rm -f -- "$temp_file"
    echo 'ERROR: Kernel denied ICMP group configuration. Check Proxmox/LXC host restrictions.' >&2
    return 1
  fi
  if ! mv -n -- "$temp_file" "$rule_file"; then
    rm -f -- "$temp_file"
    sysctl -w "net.ipv4.ping_group_range=$old_range" >/dev/null 2>&1 || true
    echo 'ERROR: Unable to persist ICMP group configuration. Previous range restored if permitted.' >&2
    return 1
  fi
  # Also verify persistent rule content: mv -n can report success on GNU
  # coreutils even when it deliberately leaves an existing destination alone.
  if [[ -e $temp_file || $(cat "$rule_file" 2>/dev/null) != "net.ipv4.ping_group_range = $gid $gid" ]]; then
    rm -f -- "$temp_file"
    sysctl -w "net.ipv4.ping_group_range=$old_range" >/dev/null 2>&1 || true
    echo 'ERROR: ICMP rule could not be persisted safely.' >&2
    return 1
  fi
  if ! runuser -u upwatch -- ping -n -c 1 -W 2 127.0.0.1 >/dev/null 2>&1; then
    rm -f -- "$rule_file"
    sysctl -w "net.ipv4.ping_group_range=$old_range" >/dev/null 2>&1 || true
    echo 'ERROR: ICMP still fails after configuration. New rule removed; prior range restored if permitted.' >&2
    return 1
  fi
  echo "OK: ICMP as upwatch works; permanent rule: $rule_file"
}
configure_service_icmp
# Register/start services BEFORE interactive administrator creation. If the
# operator aborts the prompt, both units remain registered and can recover.
install -m 0644 systemd/upwatch-web.service /etc/systemd/system/upwatch-web.service
if [[ "$ACCESS_MODE" == 1 ]]; then
  # Localhost-only mode must never expose the listener to external interfaces.
  sed -i 's/--bind 0\.0\.0\.0:8080/--bind 127.0.0.1:8080/' /etc/systemd/system/upwatch-web.service
fi
install -m 0644 systemd/upwatch-worker.service /etc/systemd/system/upwatch-worker.service
systemctl daemon-reload
echo
ui_ask 'Enable monitoring worker now? [Y/n] (choose n for an isolated test container): ' enable_worker
systemctl enable --now upwatch-web
if [[ ${enable_worker,,} == n ]]; then
  systemctl disable --now upwatch-worker 2>/dev/null || true
  echo 'Worker is disabled as requested; no active monitoring checks will run.'
else
  systemctl enable --now upwatch-worker
fi
runuser -u upwatch -- /opt/upwatch/.venv/bin/python /opt/upwatch/manage.py init
systemctl is-active --quiet upwatch-web || { echo 'Failed to start upWATCH! web.' >&2; exit 1; }
if [[ ${enable_worker,,} != n ]]; then
  systemctl is-active --quiet upwatch-worker || { echo 'Failed to start upWATCH! worker.' >&2; exit 1; }
fi
# Genuine local HTTP 200 (a proxy 403 is never a successful check).
ready=0
for attempt in $(seq 1 20); do
  if runuser -u upwatch -- /opt/upwatch/.venv/bin/python /opt/upwatch/health_probe.py >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
if [[ $ready != 1 ]]; then journalctl -u upwatch-web -n 50 --no-pager >&2 || true; echo 'HTTP health check did not become ready.' >&2; exit 1; fi
runuser -u upwatch -- /opt/upwatch/.venv/bin/python - <<'PYQR'
import sys,base64
sys.path.insert(0,'/opt/upwatch')
import mfa
x=mfa.qr_svg_data_uri(mfa.uri('release-test','JBSWY3DPEHPK3PXP'))
assert x.startswith('data:image/svg+xml;base64,')
assert b'<svg' in base64.b64decode(x.split(',',1)[1])
print('OK: MFA QR code generated as the service user.')
PYQR
# Post-installation operational checks: no developer self-test files required.
runuser -u upwatch -- /opt/upwatch/.venv/bin/python - <<'PYREADY'
import sys
sys.path.insert(0, '/opt/upwatch')
from storage import db, assert_utf8
assert_utf8()
with db() as c:
    assert c.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] >= 1
    for name in ('monitors','user_group_grants','user_tag_grants','alert_subscriptions'):
        assert c.execute('SELECT to_regclass(?)', (f'public.{name}',)).fetchone()[0], name
print('OK: PostgreSQL schema and user authorization tables verified.')
PYREADY
echo 'upWATCH! 1.0.0-rc1 installed. Network configuration, clock status and ICMP permissions were checked.'
echo 'Next: sign in, configure MFA, test monitoring and SMTP, and review INSTALLATION.md for the final acceptance checklist.'
