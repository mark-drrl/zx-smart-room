"""Zephyr -> YouTube on the TV (TizenTube), through YouTube's own "Link with TV code" connection
(the Lounge API that YouTube remote apps use). Works with TizenTube because it runs YouTube's
real TV interface. Casting (DIAL) went to the stock YouTube app and opened Samsung's Multi View.

Link once (TizenTube > Settings > Link with TV code):
    python -P youtube_tv.py link 123456789012
The link is kept in Windows Credential Manager, next to the API keys.
"""
import asyncio
import json
import sys

import keyring
from pyytlounge import YtLoungeApi
from pyytlounge.models import AUTH_VERSION_V1, AuthState

SERVICE = 'zx-smart-room'
KEY = 'YOUTUBE_LOUNGE_AUTH'


def _save(api):
    # pyytlounge 3.4's store_auth_state() writes an old format its own loader rejects; use the
    # current serialized form instead.
    keyring.set_password(SERVICE, KEY, json.dumps(api.auth.serialize()))


def _load(api):
    data = json.loads(keyring.get_password(SERVICE, KEY))
    if 'version' not in data:                      # saved in the old format: convert it
        data = {'version': AUTH_VERSION_V1, 'screenId': data['screenId'], 'loungeIdToken': data['lounge_id_token'],
                'refreshToken': data['refresh_token'], 'expiry': 0}
    api.auth = AuthState()
    api.auth.deserialize(data)


def linked():
    return bool(keyring.get_password(SERVICE, KEY))


async def _link(code):
    async with YtLoungeApi('Zephyr') as api:
        if not await api.pair(code.replace(' ', '').replace('-', '')):
            raise SystemExit('That code did not work. Get a fresh one from TizenTube > Settings > Link with TV code.')
        _save(api)
        print(f'Linked to "{api.screen_name}". Saved in Windows Credential Manager.')


async def _do(action, video_id=None):
    async with YtLoungeApi('Zephyr') as api:
        _load(api)
        if not api.linked():
            await api.refresh_auth()
        if not await api.connect():
            await api.refresh_auth()
            if not await api.connect():
                raise RuntimeError('YouTube on the TV is not answering')
        if action == 'play_video':
            await api.play_video(video_id)
        elif action == 'pause':
            await api.pause()
        elif action == 'resume':
            await api.play()
        elif action == 'next':
            await api.next()
        elif action == 'previous':
            await api.previous()
        elif action == 'skip_ad':
            await api.skip_ad()
        _save(api)                                    # keep refreshed tokens


def play(video_id):
    asyncio.run(_do('play_video', video_id))


def control(action):
    asyncio.run(_do(action))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'link':
        asyncio.run(_link(sys.argv[2]))
    else:
        print(__doc__)
