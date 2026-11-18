"""Check this LXC's public egress IPv4 against IONOS-compatible A records.

Wildcard DNS is checked using a fresh concrete hostname, not an A query for '*'.
The probe is read-only: it never calls an IONOS update URL or API.
"""
import ipaddress
import secrets
import time
import dns.exception
from probes.ddns_bundle import resolver_for, public_ip

class WanDnsError(Exception):
    def __init__(self, code, detail, metrics=None):
        super().__init__(detail)
        self.code = code
        self.metrics = metrics or {}

def run(target, config, timeout, *, resolver=None, wan_lookup=None, token_factory=None):
    start = time.monotonic()
    resolver = resolver or resolver_for(config.get('resolver', 'system'))
    wan_lookup = wan_lookup or public_ip
    token_factory = token_factory or (lambda: secrets.token_hex(8))
    remaining = timeout - (time.monotonic() - start)
    if remaining <= 0:
        raise WanDnsError('WAN_DNS_TIMEOUT', 'Gesamtzeitlimit überschritten')
    try:
        public = str(ipaddress.IPv4Address(wan_lookup(min(remaining, 5.0))))
        if not ipaddress.ip_address(public).is_global:
            raise ValueError('Keine global erreichbare IPv4')
    except Exception as exc:
        raise WanDnsError('WAN_IP_LOOKUP_FAILED', 'Öffentliche IPv4 nicht ermittelbar: ' + type(exc).__name__) from exc

    # A wildcard cannot be proved by querying the literal *.example.com record.
    # Use a fresh, otherwise nonexistent label for every run to avoid cached answers.
    target = target.rstrip('.').lower()
    wildcard = target.startswith('*.')
    random_name = 'uw-' + token_factory() + '.' + target[2:] if wildcard else target
    names = [random_name] + [name.rstrip('.').lower() for name in config.get('subdomains', []) if name.rstrip('.').lower() != random_name]
    metrics = {'egress_ipv4': public, 'resolver': config.get('resolver', 'system'),
               'wildcard': wildcard, 'wildcard_sample': random_name if wildcard else None,
               'subdomains': [], 'checked_count': len(names)}
    failures = []
    for name in names:
        entry = {'name': name, 'status': 'up'}
        remaining = timeout - (time.monotonic() - start)
        if remaining <= 0:
            entry.update(status='down', error='DNS_TIMEOUT')
            failures.append(name + ': Zeitlimit überschritten')
        else:
            try:
                # Resolving A follows a CNAME chain automatically, when one exists.
                answers = resolver.resolve(name, 'A', lifetime=min(remaining, 3.0), raise_on_no_answer=True)
                ips = sorted({str(ipaddress.IPv4Address(rr.address)) for rr in answers})
                entry['ipv4'] = ips
                if ips != [public]:
                    entry.update(status='down', error='IP_MISMATCH')
                    failures.append(name + ': A-Record weicht von ' + public + ' ab')
            except (dns.exception.DNSException, TimeoutError, ValueError, AttributeError) as exc:
                entry.update(status='down', error='DNS_LOOKUP_FAILED')
                failures.append(name + ': DNS-Fehler ' + type(exc).__name__)
        metrics['subdomains'].append(entry)
    metrics['failed_count'] = len(failures)
    if failures:
        metrics['failures'] = failures
        raise WanDnsError('WAN_DNS_MISMATCH', '; '.join(failures[:2]) + (f' (+{len(failures)-2} weitere)' if len(failures)>2 else ''), metrics)
    return metrics, f'IPv4 {public} · {len(names)} DNS-Namen korrekt'
