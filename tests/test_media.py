"""YouTube, Spotify and voice ID decision logic, with fakes for the TV, the services and the model."""
import numpy as np
import pytest

import assistant as A


class FakeYouTube:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def play(self, video_id, confirm=False, attempts=3):
        if self.fail:
            raise RuntimeError('the TV kept playing something else')
        self.sent.append((video_id, confirm, attempts))


@pytest.fixture
def tv(monkeypatch):
    state = {'off': False, 'showing': True, 'opened': [], 'woke': 0}
    monkeypatch.setattr(A, 'busy', lambda seconds: None)      # never write the real keeper note in tests
    monkeypatch.setattr(A, 'tv_is_off', lambda: state['off'])
    monkeypatch.setattr(A, 'tv_showing', lambda app, wait: state['showing'])
    monkeypatch.setattr(A, 'tv_open', lambda app: state['opened'].append(app) or True)
    monkeypatch.setattr(A, 'youtube_find', lambda q: {'id': 'vid1', 'title': '4K Campfire by the River - Relaxing | UHD'})

    def turn_on():
        state['woke'] += 1
        state['off'] = False
        return 'Turning on the TV.'
    monkeypatch.setattr(A, 'turn_on', turn_on)
    return state


def test_youtube_on_screen_switches_fast_without_confirming(tv, monkeypatch):
    yt = FakeYouTube()
    monkeypatch.setattr(A, 'youtube', lambda: yt)
    assert A.youtube_play('campfire') == 'Playing Campfire by the River.'
    assert yt.sent == [('vid1', False, 4)] and tv['opened'] == []


def test_youtube_from_another_app_opens_it_and_confirms(tv, monkeypatch):
    tv['showing'] = False
    yt = FakeYouTube()
    monkeypatch.setattr(A, 'youtube', lambda: yt)
    A.youtube_play('campfire')
    assert tv['opened'] == [A.YOUTUBE_APP] and yt.sent[0][1] is True        # the 26 Oct lost-video case


def test_youtube_after_waking_the_tv_retries_longer(tv, monkeypatch):
    tv['off'] = True
    yt = FakeYouTube()
    monkeypatch.setattr(A, 'youtube', lambda: yt)
    A.youtube_play('jazz')
    assert tv['woke'] == 1 and yt.sent[0][1:] == (True, 7)


def test_youtube_failure_is_reported_honestly(tv, monkeypatch):
    monkeypatch.setattr(A, 'youtube', lambda: FakeYouTube(fail=True))
    assert "didn't take the video" in A.youtube_play('jazz')


def test_youtube_not_linked(tv, monkeypatch):
    monkeypatch.setattr(A, 'youtube', lambda: None)
    assert 'TV code' in A.youtube_play('jazz')


def test_youtube_nothing_found(tv, monkeypatch):
    monkeypatch.setattr(A, 'youtube', lambda: FakeYouTube())
    monkeypatch.setattr(A, 'youtube_find', lambda q: None)
    assert A.youtube_play('zzz') == 'Nothing found on YouTube for "zzz".'


# ---- Spotify


class FakeSpotify:
    ready = True

    def __init__(self, devices):
        self.devices, self.played, self.opened = devices, [], 0

    def search(self, q, kind):
        return ('spotify:track:1', '"September" by Earth, Wind & Fire') if q != 'nothing' else (None, None)

    def tv_device(self, hint, wait_s=0.0):
        return self.devices.pop(0) if self.devices else None

    def play(self, uri, device_id):
        self.played.append((uri, device_id))


def test_spotify_plays_on_the_tv(tv, monkeypatch):
    sp = FakeSpotify([{'id': 'tv1', 'type': 'TV'}])
    monkeypatch.setattr(A, 'spotify', lambda: sp)
    assert A.spotify_play('September', 'track') == 'Playing "September" by Earth, Wind & Fire on Spotify.'
    assert sp.played == [('spotify:track:1', 'tv1')] and tv['opened'] == []


def test_spotify_opens_the_tv_app_when_the_tv_is_not_a_speaker_yet(tv, monkeypatch):
    sp = FakeSpotify([None, {'id': 'tv1', 'type': 'TV'}])
    monkeypatch.setattr(A, 'spotify', lambda: sp)
    A.spotify_play('September', 'track')
    assert tv['opened'] == [A.SPOTIFY_APP] and sp.played


def test_spotify_nothing_found_and_no_tv(tv, monkeypatch):
    monkeypatch.setattr(A, 'spotify', lambda: FakeSpotify([]))
    assert 'Nothing found' in A.spotify_play('nothing', 'track')
    assert "didn't show up" in A.spotify_play('September', 'track')


def test_spotify_body_for_tracks_and_playlists():
    import spotify as S
    sent = []
    sp = S.Spotify(cid='x')
    sp.call = lambda method, path, body=None, params=None: sent.append(body)
    sp.play('spotify:track:1', 'd')
    sp.play('spotify:playlist:9', 'd')
    assert sent == [{'uris': ['spotify:track:1']}, {'context_uri': 'spotify:playlist:9'}]


# ---- Voice ID


class FakeVoiceID:
    """VoiceID with the speech model replaced: 'voices' are fixed directions, plus a little noise."""

    def __new__(cls):
        import voice_id as V
        v = object.__new__(V.VoiceID)
        v.rate, v.samples, v.threshold = 16000, None, None
        rng = np.random.default_rng(0)

        def embed(pcm):
            base = np.zeros(512, np.float32)
            base[int(pcm[0]) % 512] = 1.0                    # the "speaker" is encoded in the first sample
            e = base + rng.normal(0, 0.02, 512).astype(np.float32)   # same voice ~0.8 alike, others ~0
            return e / np.linalg.norm(e)
        v.embed = embed
        return v


def clip(speaker, seconds=2.0):
    x = np.zeros(int(16000 * seconds), np.int16)
    x[0] = speaker
    return x


def test_voice_id_learns_you_and_rejects_others(tmp_path, monkeypatch):
    import voice_id as V
    monkeypatch.setattr(V, 'PROFILE', str(tmp_path / 'vp.npz'))
    v = FakeVoiceID()
    own, bar = v.enrol([clip(7) for _ in range(6)])
    assert 0.30 <= bar <= 0.65 and own > bar
    assert v.is_you(clip(7))[0] and not v.is_you(clip(99))[0]
    assert v.is_you(clip(99, seconds=0.3)) == (True, None)          # too short to judge: let through


def test_voice_id_keeps_learning_but_not_from_doubtful_matches(tmp_path, monkeypatch):
    import voice_id as V
    monkeypatch.setattr(V, 'PROFILE', str(tmp_path / 'vp.npz'))
    v = FakeVoiceID()
    v.enrol([clip(7) for _ in range(6)])
    v.learn(clip(7), 0.9)
    assert len(v.samples) == 7
    v.learn(clip(99), 0.4)                                          # doubtful: never added
    v.learn(clip(7, seconds=0.5), 0.9)                              # too short: never added
    assert len(v.samples) == 7
    for _ in range(50):
        v.learn(clip(7), 0.9)
    assert len(v.samples) == 40                                     # capped, originals kept
