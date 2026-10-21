"""One-time pairing with the TV's remote-control channel (Python client, like most Samsung tools).
The TV should show an "Allow" pop-up; once allowed, the token is saved to tv-token.txt for Jarvis.
Sends no key presses. Run: python pair_tv.py
"""
import base64
import json
import os
import ssl
import sys
import time

import websocket

HERE = os.path.dirname(os.path.abspath(__file__))
env = {}
for line in open(os.path.join(HERE, 'corelynx.env'), encoding='utf-8'):
    if '=' in line and not line.lstrip().startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

name = base64.b64encode(b'Jarvis').decode()
url = f"wss://{env['TV_IP']}:8002/api/v2/channels/samsung.remote.control?name={name}"
print('Waiting for Allow on the TV (30 s)...', flush=True)
try:
    ws = websocket.create_connection(url, timeout=30, sslopt={'cert_reqs': ssl.CERT_NONE},
                                     origin=f"https://{env['TV_IP']}:8002")
except Exception as e:
    print('Could not connect:', e)
    sys.exit(1)

deadline = time.time() + 30
while time.time() < deadline:
    try:
        msg = json.loads(ws.recv())
    except Exception as e:
        print('No answer from the TV:', type(e).__name__)
        break
    event = msg.get('event')
    print('TV says:', event, flush=True)
    if event == 'ms.channel.connect':
        token = (msg.get('data') or {}).get('token')
        if token:
            with open(os.path.join(HERE, 'tv-token.txt'), 'w', encoding='utf-8') as f:
                f.write(token)
            print('Paired. Token saved to voice/tv-token.txt')
        else:
            print('Connected, but the TV sent no token.')
        break
    if event == 'ms.channel.unauthorized':
        print('The TV refused (Deny was chosen, or remote access is off).')
        break
ws.close()
