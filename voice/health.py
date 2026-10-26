"""Post-deploy health check: is Zephyr really up, and can she reach everything she depends on?

    python -P health.py          exit 0 = healthy, 1 = Zephyr herself is down (deploy rolls back)

Zephyr's own process is critical. External services (TV, Corelynx, Claude, ElevenLabs, YouTube,
Spotify, SmartThings) are reported as warnings: a deploy can't fix the internet or a TV that's off.
Nothing here costs credits: Claude and ElevenLabs are checked with free endpoints.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def env():
    values = {}
    for line in open(os.path.join(HERE, 'corelynx.env'), encoding='utf-8'):
        if '=' in line and not line.lstrip().startswith('#'):
            k, v = line.split('=', 1)
            values[k.strip()] = v.strip()
    from keystore import fill
    return fill(values)


def zephyr_running():
    out = subprocess.run(['powershell', '-NoProfile', '-Command',
                          "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe'\" | "
                          "Where-Object { $_.CommandLine -like '*assistant.py*' } | Measure-Object | % Count"],
                         capture_output=True, text=True, timeout=30)
    return int((out.stdout or '0').strip() or 0)


def check(name, fn):
    started = time.time()
    try:
        detail = fn()
        return True, f'ok   {name:<12} {detail} ({time.time() - started:.1f}s)'
    except Exception as e:
        return False, f'WARN {name:<12} {type(e).__name__}: {str(e)[:120]}'


def main():
    e = env()
    lines, healthy = [], True

    # critical: Zephyr's process is alive and stays alive (a crash loop would restart it repeatedly)
    first = zephyr_running()
    time.sleep(8)
    second = zephyr_running()
    if first and second:
        lines.append('ok   Zephyr       running and stable for 8 s')
    else:
        healthy = False
        lines.append('FAIL Zephyr       not running (or crashed within 8 s)')

    def tv():
        with urllib.request.urlopen(f'http://{e.get("TV_IP", "192.168.1.97")}:8001/api/v2/', timeout=3) as r:
            return 'PowerState ' + json.load(r)['device'].get('PowerState', '?')

    def corelynx():
        import websocket
        ws = websocket.create_connection('wss://sttapi.corelynx.io/v1/stream', timeout=8, header=[
            'Authorization: Bearer ' + e['CORELYNX_API_KEY'], 'User-Agent: SmartRoom-Assistant/1.0'])
        ready = json.loads(ws.recv()).get('type')
        ws.close()
        if ready != 'ready':
            raise RuntimeError(f'stream said {ready}')
        return 'live stream ready'

    def claude():
        import anthropic
        from brain import MODEL
        anthropic.Anthropic(api_key=e['ANTHROPIC_API_KEY'], timeout=10).models.retrieve(MODEL)
        return MODEL

    def elevenlabs():
        if not e.get('ELEVENLABS_API_KEY'):
            return 'not configured (Kokoro voice)'
        req = urllib.request.Request('https://api.elevenlabs.io/v1/user/subscription',
                                     headers={'xi-api-key': e['ELEVENLABS_API_KEY']})
        with urllib.request.urlopen(req, timeout=10) as r:
            s = json.load(r)
        left = s['character_limit'] - s['character_count']
        if left < 2000:
            raise RuntimeError(f'only {left} credits left this month')
        return f'{left:,} credits left'

    def youtube():
        import youtube_tv
        if not youtube_tv.linked():
            raise RuntimeError('not linked (TizenTube > Settings > Link with TV code)')
        return 'linked'

    def spotify():
        from spotify import Spotify
        sp = Spotify()
        if not sp.ready:
            raise RuntimeError('not signed in (python -P spotify.py login)')
        sp._access()
        return 'signed in'

    def smartthings():
        if not os.path.exists(r'C:\dev\tools\smartthings\smartthings.exe'):
            raise RuntimeError('CLI missing')
        return 'CLI present'

    for name, fn in [('TV', tv), ('Corelynx', corelynx), ('Claude', claude), ('ElevenLabs', elevenlabs),
                     ('YouTube', youtube), ('Spotify', spotify), ('SmartThings', smartthings)]:
        ok, line = check(name, fn)
        lines.append(line)

    print('\n'.join(lines))
    sys.exit(0 if healthy else 1)


if __name__ == '__main__':
    main()
