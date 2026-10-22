"""Zephyr -> YouTube on the TV (TizenTube), through YouTube's own "Link with TV code" connection
(the Lounge API that YouTube remote apps use). Works with TizenTube because it runs YouTube's
real TV interface. Casting (DIAL) went to the stock YouTube app and opened Samsung's Multi View.

The connection is kept open between videos (measured: connecting costs ~2.3 s, sending a video
on an open connection ~1.7 s), and re-made automatically if YouTube on the TV was closed.

Link once (TizenTube > Settings > Link with TV code):
    python -P youtube_tv.py link 123456789012
The link is kept in Windows Credential Manager, next to the API keys.
"""
import asyncio
import json
import sys
import threading

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


class YouTubeRemote:
    """One long-lived connection to YouTube on the TV, on its own event loop thread."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.api = None

    def _run(self, coro, timeout=20):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _connect(self, fresh=False):
        if fresh and self.api:
            try:
                await self.api.__aexit__(None, None, None)
            except Exception:
                pass
            self.api = None
        if self.api is None:
            self.api = YtLoungeApi('Zephyr')
            await self.api.__aenter__()
            _load(self.api)
        if self.api.connected():
            return
        if not self.api.linked():
            await self.api.refresh_auth()
        if not await self.api.connect():
            await self.api.refresh_auth()
            if not await self.api.connect():
                raise RuntimeError('YouTube on the TV is not answering')
        _save(self.api)                                   # keep refreshed tokens

    async def _do(self, action, *args, attempts=1):
        """Runs an action; if the connection went stale (TizenTube was closed), reconnects and
        retries. attempts > 1 keeps retrying while YouTube on the TV is still starting up."""
        last = None
        for i in range(attempts):
            try:
                await self._connect(fresh=i > 0)
                await getattr(self.api, action)(*args)
                return
            except Exception as e:
                last = e
                await asyncio.sleep(1.0 if i else 0)
        raise RuntimeError(f'YouTube on the TV did not respond: {last}')

    def warm_up(self):
        """Connect in the background so the first request doesn't pay for it."""
        asyncio.run_coroutine_threadsafe(self._connect(), self.loop)

    def play(self, video_id, attempts=2):
        self._run(self._do('play_video', video_id, attempts=attempts), timeout=8 + 4 * attempts)

    def control(self, action):
        name = {'pause': 'pause', 'resume': 'play', 'next': 'next', 'previous': 'previous', 'skip_ad': 'skip_ad'}[action]
        self._run(self._do(name, attempts=2))


async def _link(code):
    async with YtLoungeApi('Zephyr') as api:
        if not await api.pair(code.replace(' ', '').replace('-', '')):
            raise SystemExit('That code did not work. Get a fresh one from TizenTube > Settings > Link with TV code.')
        _save(api)
        print(f'Linked to "{api.screen_name}". Saved in Windows Credential Manager.')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'link':
        asyncio.run(_link(sys.argv[2]))
    else:
        print(__doc__)
