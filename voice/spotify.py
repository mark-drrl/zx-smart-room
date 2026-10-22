"""Spotify for Zephyr: search, play on the TV, pause/skip/shuffle (needs Spotify Premium).

Uses Spotify's official Web API. Sign-in is the PKCE flow (no client secret), done once:
    python -P spotify.py login
That opens Spotify in the browser; after you approve, the long-lived sign-in (refresh token) is
kept in Windows Credential Manager next to the API keys. Nothing secret goes in files.

Playing on the TV: the TV's Spotify app has to be open to show up as a Spotify Connect device,
so Zephyr opens it first, waits for it to appear, then tells Spotify to play there.
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

import keyring

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICE = 'zx-smart-room'
REDIRECT = 'http://127.0.0.1:8888/callback'
SCOPES = 'user-read-playback-state user-modify-playback-state user-read-currently-playing'
API = 'https://api.spotify.com/v1'


def client_id():
    env = {}
    for line in open(os.path.join(HERE, 'corelynx.env'), encoding='utf-8'):
        if '=' in line and not line.lstrip().startswith('#'):
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()
    return env.get('SPOTIFY_CLIENT_ID', '')


class Spotify:
    def __init__(self, cid=None):
        self.cid = cid or client_id()
        self.token, self.expires = None, 0.0

    @property
    def ready(self):
        return bool(self.cid and keyring.get_password(SERVICE, 'SPOTIFY_REFRESH_TOKEN'))

    # ---- sign-in

    def _token_request(self, fields):
        req = urllib.request.Request('https://accounts.spotify.com/api/token',
                                     data=urllib.parse.urlencode(fields).encode(),
                                     headers={'Content-Type': 'application/x-www-form-urlencoded'})
        with urllib.request.urlopen(req, timeout=10) as r:
            got = json.load(r)
        self.token, self.expires = got['access_token'], time.time() + got.get('expires_in', 3600) - 60
        if got.get('refresh_token'):                     # Spotify may rotate it: always keep the newest
            keyring.set_password(SERVICE, 'SPOTIFY_REFRESH_TOKEN', got['refresh_token'])

    def login(self):
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        state = secrets.token_urlsafe(16)
        got = {}

        class Catch(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                got.update({k: v[0] for k, v in q.items()})
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                ok = 'code' in got and got.get('state') == state
                self.wfile.write(('<h2 style="font-family:sans-serif">' + (
                    'Zephyr is connected to Spotify. You can close this tab.' if ok else
                    'Spotify sign-in did not complete. Close this tab and try again.') + '</h2>').encode())

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(('127.0.0.1', 8888), Catch)
        threading.Thread(target=server.handle_request, daemon=True).start()
        webbrowser.open('https://accounts.spotify.com/authorize?' + urllib.parse.urlencode({
            'client_id': self.cid, 'response_type': 'code', 'redirect_uri': REDIRECT, 'scope': SCOPES,
            'code_challenge_method': 'S256', 'code_challenge': challenge, 'state': state}))
        end = time.time() + 180
        while 'code' not in got and 'error' not in got and time.time() < end:
            time.sleep(0.3)
        server.server_close()
        if got.get('state') != state or 'code' not in got:
            sys.exit(f'Spotify sign-in failed: {got.get("error", "timed out")}')
        self._token_request({'grant_type': 'authorization_code', 'code': got['code'], 'redirect_uri': REDIRECT,
                             'client_id': self.cid, 'code_verifier': verifier})
        print('Spotify connected; sign-in saved in Windows Credential Manager.')

    def _access(self):
        if not self.token or time.time() > self.expires:
            self._token_request({'grant_type': 'refresh_token', 'client_id': self.cid,
                                 'refresh_token': keyring.get_password(SERVICE, 'SPOTIFY_REFRESH_TOKEN')})
        return self.token

    # ---- Web API

    def call(self, method, path, body=None, params=None):
        url = API + path + ('?' + urllib.parse.urlencode(params) if params else '')
        req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Authorization': 'Bearer ' + self._access(),
                                              'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode('utf-8', 'replace')
            raise RuntimeError(f'Spotify {e.code}: {detail}')

    def search(self, query, kind):
        """kind: track, artist, album or playlist. Returns (uri, description) of the best match."""
        got = self.call('GET', '/search', params={'q': query, 'type': kind, 'limit': 5})
        items = [i for i in (got.get(kind + 's') or {}).get('items') or [] if i]
        if not items:
            return None, None
        best = items[0]
        if kind == 'track':
            return best['uri'], f'"{best["name"]}" by {", ".join(a["name"] for a in best["artists"])}'
        if kind == 'album':
            return best['uri'], f'the album "{best["name"]}" by {best["artists"][0]["name"]}'
        if kind == 'artist':
            return best['uri'], best['name']
        return best['uri'], f'the playlist "{best["name"]}"'

    def tv_device(self, name_hint, wait_s=0.0):
        end = time.time() + wait_s
        while True:
            devices = self.call('GET', '/me/player/devices').get('devices', [])
            for d in devices:
                if d.get('type') == 'TV' or name_hint.lower() in (d.get('name') or '').lower():
                    return d
            if time.time() >= end:
                return None
            time.sleep(1)

    def play(self, uri, device_id):
        body = {'uris': [uri]} if uri.startswith('spotify:track:') else {'context_uri': uri}
        self.call('PUT', '/me/player/play', body=body, params={'device_id': device_id})

    def control(self, action, device_id=None):
        params = {'device_id': device_id} if device_id else None
        if action == 'pause':
            self.call('PUT', '/me/player/pause', params=params)
        elif action == 'resume':
            self.call('PUT', '/me/player/play', params=params)
        elif action == 'next':
            self.call('POST', '/me/player/next', params=params)
        elif action == 'previous':
            self.call('POST', '/me/player/previous', params=params)
        elif action in ('shuffle_on', 'shuffle_off'):
            self.call('PUT', '/me/player/shuffle', params={'state': str(action == 'shuffle_on').lower(), **(params or {})})

    def now_playing(self):
        got = self.call('GET', '/me/player/currently-playing')
        item = (got or {}).get('item')
        if not item:
            return 'Nothing is playing on Spotify.'
        who = ', '.join(a['name'] for a in item.get('artists', []))
        return f'"{item["name"]}" by {who}' + (' (paused)' if not got.get('is_playing') else '')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'login':
        if not client_id():
            sys.exit('Add SPOTIFY_CLIENT_ID=... to corelynx.env first.')
        Spotify().login()
    else:
        print(__doc__)
