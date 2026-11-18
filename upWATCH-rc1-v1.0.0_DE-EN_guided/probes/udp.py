"""Conservative UDP service probes: verified responses or indeterminate timeouts."""
import os
import socket
import struct
import time

import security

class UDPFailure(Exception):
    def __init__(self,code,detail,indeterminate=False):
        super().__init__(detail)
        self.code=code
        self.indeterminate=indeterminate


def _exchange(target,port,payload,timeout):
    # Pin the actual destination after SSRF policy resolution; never resolve twice.
    entries=socket.getaddrinfo(target,port,type=socket.SOCK_DGRAM)
    if not entries or any(not security.permitted(e[4][0]) for e in entries):
        raise UDPFailure('UDP_TARGET_BLOCKED','Ziel-IP durch Netzwerkregel gesperrt. MONITOR_ALLOWED_CIDRS prüfen.')
    end=time.monotonic()+timeout
    last_error=None
    for family,kind,proto,_,address in entries:
        remaining=end-time.monotonic()
        if remaining<=0:break
        try:
            with socket.socket(family,kind,proto) as sock:
                sock.settimeout(remaining)
                sock.connect(address)
                sock.send(payload)
                reply=sock.recv(4096)
                if not reply:raise UDPFailure('UDP_NO_RESPONSE','UDP: keine Nutzdaten empfangen',True)
                return reply
        except socket.timeout:
            raise UDPFailure('UDP_NO_RESPONSE','UDP: keine Antwort – offen oder gefiltert nicht unterscheidbar',True)
        except ConnectionRefusedError:
            raise UDPFailure('UDP_PORT_UNREACHABLE','UDP-Port meldet nicht erreichbar')
        except OSError as exc:
            last_error=exc
    if last_error:raise UDPFailure('UDP_NETWORK_ERROR',f'UDP-Netzwerkfehler: {last_error}')
    raise UDPFailure('UDP_NO_RESPONSE','UDP: keine Antwort innerhalb des Timeouts',True)


def run(target,cfg,timeout):
    profile=cfg.get('udp_profile','dns')
    port=int(cfg.get('port',53))
    if profile=='dns':
        import dns.message
        import dns.rdatatype
        import dns.rcode
        name=cfg.get('udp_dns_name','example.com')
        question=dns.message.make_query(name,'A')
        response=_exchange(target,port,question.to_wire(),timeout)
        try:
            parsed=dns.message.from_wire(response,ignore_trailing=False)
            if not parsed.flags & 0x8000 or parsed.id!=question.id or len(parsed.question)!=1 or parsed.question[0]!=question.question[0]:
                raise ValueError('Unpassende DNS-Antwort')
            if parsed.rcode()!=dns.rcode.NOERROR:
                raise UDPFailure('UDP_DNS_RCODE',f'DNS-Server antwortet: {dns.rcode.to_text(parsed.rcode())}')
        except (dns.exception.DNSException,ValueError) as exc:
            raise UDPFailure('UDP_DNS_INVALID',f'Ungültige DNS-Antwort: {exc}') from exc
        return f'UDP DNS · gültige Antwort für {name}'
    if profile=='ntp':
        # Client mode 3, version 4; the transmit timestamp is a random token.
        request=bytearray(48)
        request[0]=0x23
        nonce=struct.pack('!I',int(time.time())+2208988800)+os.urandom(4)
        request[40:48]=nonce
        reply=_exchange(target,port,bytes(request),timeout)
        if len(reply)<48 or reply[0]&7!=4 or (reply[0]>>3)&7 not in (3,4) or reply[24:32]!=nonce:
            raise UDPFailure('UDP_NTP_INVALID','NTP-Antwort ungültig oder passt nicht zur Anfrage')
        if reply[1]==0 or reply[1]>15 or (reply[0]>>6)==3:
            raise UDPFailure('UDP_NTP_UNSYNC','NTP-Server nicht synchronisiert')
        return f'UDP NTP · gültige Zeitantwort (Stratum {reply[1]})'
    if profile=='custom':
        payload=bytes.fromhex(cfg.get('udp_payload_hex',''))
        prefix=bytes.fromhex(cfg.get('udp_expect_hex',''))
        if not payload or not prefix or len(payload)>512 or len(prefix)>512:
            raise UDPFailure('INVALID_PROBE_CONFIG','UDP-Anfrage und erwartete Antwort erforderlich')
        reply=_exchange(target,port,payload,timeout)
        if not reply.startswith(prefix):
            raise UDPFailure('UDP_RESPONSE_MISMATCH','UDP-Antwort entspricht nicht dem erwarteten Präfix')
        return f'UDP · gültige Antwort ({len(reply)} Bytes)'
    raise UDPFailure('INVALID_PROBE_CONFIG','UDP-Profil unbekannt')
