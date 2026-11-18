"""Simple local HTTP process readiness check, including reverse-proxy-only deployments."""
import json,sys,urllib.request,urllib.error
request=urllib.request.Request('http://127.0.0.1:8080/healthz',headers={'Host':'localhost'})
try:
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=3) as response:
        payload=json.load(response)
        if response.status!=200 or payload.get('web')!='ok':sys.exit(1)
except urllib.error.HTTPError:
    sys.exit(1)
except (urllib.error.URLError,TimeoutError,ValueError):sys.exit(1)
