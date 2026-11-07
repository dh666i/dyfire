"""External read-only checks; failures use GitHub Actions' native notifications."""
import json
import time
import urllib.request


def check(base='https://dyfire.cn'):
    results=[]
    for path in ('/health/ready','/health/execution','/api/version'):
        ok=False;started=time.monotonic()
        for attempt in range(3):
            try:
                request=urllib.request.Request(base+path,headers={'User-Agent':'dyfire-availability/1','Cache-Control':'no-cache'})
                with urllib.request.urlopen(request,timeout=12) as response:
                    value=json.loads(response.read(4096))
                    ok=response.status==200 and (value.get('status')=='ready' if 'health/' in path else bool(value.get('version')))
                if ok:break
            except Exception:pass
            if attempt<2:time.sleep(2)
        results.append({'endpoint':path,'healthy':ok,'seconds':round(time.monotonic()-started,2)})
    return results


if __name__=='__main__':
    results=check();print(json.dumps({'checks':results},indent=2))
    raise SystemExit(0 if all(item['healthy'] for item in results) else 1)
