"""How Zephyr understands what you say: commands, stop words, modes, and what she speaks."""
import numpy as np
import pytest

import assistant as A


@pytest.mark.parametrize('said, expected', [
    ('Zephyr, turn on the TV', 'turn_on'),
    ('turn on the T V', 'turn_on'),
    ('turn off the TV', 'turn_off'),
    ('tv off', 'turn_off'),
    ('turn the TV off completely', 'turn_off_fully'),
    ('shut down the tv', 'turn_off_fully'),
    ('go home', 'go_home'),
    ('art mode', 'go_art'),
    ('turn on art mode', 'go_art'),
])
def test_simple_tv_commands(said, expected):
    action = A.parse(said)
    assert action is not None and action.__name__ == expected


@pytest.mark.parametrize('said, expected', [
    ('Zephyr, forest mode', 'forest_mode'),
    ('campfire mode please', 'forest_mode'),
    ('good morning', 'good_morning'),
    ('Zephyr good night', 'good_night'),
    ('goodnight', 'good_night'),
    ('mirror my laptop', 'mirror_laptop'),
    ('Zephyr, mirror my laptop to the TV', 'mirror_laptop'),
    ('put my laptop on the tv', 'mirror_laptop'),
    ("Zephyr, I'm heading out", 'heading_out'),
    ("Zephyr I'm going", 'heading_out'),
    ("I'm going to work", 'heading_out'),
    ('heading to the gym now', 'heading_out'),
    ("okay I'm off", 'heading_out'),
    ('leaving now', 'heading_out'),
])
def test_scenes(said, expected):
    action = A.parse(said)
    assert action is not None and action.__name__ == expected
    assert A.quick_ack(action, said) == A.SCENE_ACKS[action]


def test_a_greeting_in_a_longer_sentence_is_not_a_scene():
    assert A.parse('good morning Zephyr how are you doing today') is None


def test_opening_an_app():
    action = A.parse('open YouTube')
    assert getattr(action, 'opens_app', False)
    assert A.quick_ack(action, 'open YouTube') == 'Opening YouTube.'


@pytest.mark.parametrize('said', [
    'go to Spotify, play a song, and lower the volume to eight',   # several steps: Claude's job
    'volume up a bit',
    'play September by Earth Wind and Fire',
    'turn on the lamp',                                             # not the TV
    'what should I watch tonight',
    'need a YouTube video that plays',                             # wants something specific
    'I want something on YouTube',
])
def test_everything_else_goes_to_claude(said):
    assert A.parse(said) is None


def test_name_is_removed_from_requests():
    assert A.without_name('Zephyr, what time is it?') == 'what time is it'
    assert A.without_name('Hey Zephyr') == ''


@pytest.mark.parametrize('heard', ['Zephyr, open Spotify.', 'Zafer volume to 12.', 'Jeffrey turn on my T V.',
                                   'Suffer. Go to Spotify.', 'Zapper volume to thirty-five.'])
def test_name_spellings_corelynx_produces(heard):
    assert A.NAME_LOOSE.search(heard)


@pytest.mark.parametrize('heard', ['So, So like right now.', 'Barbara', "Sarah, what's going on?",
                                   'enter the temple'])
def test_chatter_is_not_her_name(heard):
    assert not A.NAME_LOOSE.search(heard)


@pytest.mark.parametrize('said, mode', [
    ('Zephyr, stop listening for now', 'pause'), ('Zephyr, go dark', 'pause'), ("Don't listen.", 'pause'),
    ('Zephyr wake up', 'resume'), ("I'm back", 'resume'),
])
def test_pause_and_resume(said, mode):
    assert bool(A.PAUSE.search(said)) == (mode == 'pause')
    assert bool(A.RESUME.search(said)) == (mode == 'resume')


@pytest.mark.parametrize('said', ['stop', 'never mind', "that's all thanks", 'thank you', 'cancel'])
def test_ending_a_conversation(said):
    assert A.DONE.match(said)


@pytest.mark.parametrize('said', ['no worries, tell me a joke', 'what should I watch tonight', 'Zephyr, good night', 'good night'])
def test_real_questions_do_not_end_it(said):
    assert not A.DONE.match(said)


def test_social_mode_commands():
    assert A.SOCIAL_ON.search('Zephyr, social mode') and not A.SOCIAL_OFF.search('Zephyr, social mode')
    for off in ['Zephyr, stop social mode', 'end social mode', 'social mode off']:
        assert A.SOCIAL_OFF.search(off)


def test_voice_commands():
    assert A.LEARN_VOICE.search('Zephyr, learn my voice.')
    assert A.FORGET_VOICE.search('Zephyr, forget my voice')
    assert not A.LEARN_VOICE.search('learn my vocabulary')


def test_stage_directions_are_never_spoken():
    assert A.speakable('[no response]') == ''
    assert A.speakable('Done, ten. [laughs]') == 'Done, ten.'
    assert A.speakable('Okay.') == 'Okay.'


def test_failure_detection():
    assert A.failed("I couldn't wake the TV.")
    assert A.failed('The TV is still on.')
    assert not A.failed('Turning on the TV.')
    assert not A.failed("Volume's at 8.")


def _session(text):
    s = A.LiveSession.__new__(A.LiveSession)
    s.order, s.live, s.deltas = [1], {1: text}, {}
    return s


def test_end_of_turn_waits_for_unfinished_thoughts():
    assert _session('Zephyr volume to').dangling()
    assert _session('play some').dangling()
    assert _session('I need a YouTube video that').dangling()
    assert _session('something calm, but').dangling()
    assert not _session('Zephyr, volume to eight.').dangling()


def test_end_of_turn_needs_a_finished_sentence():
    assert _session('Zephyr, what is a good movie for tonight?').sounds_finished()
    assert not _session('See?').sounds_finished()           # one word: probably a misheard name
    assert not _session('Zephyr,').sounds_finished()


def test_volume_is_capped(monkeypatch):
    sent = {}
    monkeypatch.setattr(A, 'tv_is_off', lambda: False)
    monkeypatch.setattr(A, 'get_volume', lambda: 10)
    monkeypatch.setattr(A, '_upnp', lambda action, args='': sent.update(args=args))
    assert A.set_volume(level=250) == "Volume's at 60."
    assert '<DesiredVolume>60</DesiredVolume>' in sent['args']
    assert A.set_volume(change=-20) == "Volume's at 0."


@pytest.mark.parametrize('title, spoken', [
    ('4K Campfire by the River - Relaxing Fireplace & Nature Sounds - Robin Birdsong  - UHD Video - 2160p',
     'Campfire by the River'),
    ('Cozy Fireplace | 4K HDR 60fps', 'Cozy Fireplace'),
    ('Interstellar - Trailer - Official Warner Bros. UK', 'Interstellar'),
    ('lofi hip hop radio 📚 beats to relax/study to', 'lofi hip hop radio beats to relax/study to'),
    ('🔥 Cozy Fireplace', 'Cozy Fireplace'),
])
def test_video_titles_are_shortened_for_speech(title, spoken):
    assert A.short_title(title) == spoken


def test_long_directions_are_not_cut_off():
    """Two seconds of talking, a 0.5 s thinking pause, more talking: it must keep recording.
    A quick command followed by the same pause ends straight away."""
    import queue
    sec = A.FRAME / A.RATE

    class FakeVAD:
        def __init__(self, pattern): self.pattern, self.i = pattern, -1
        def reset(self): pass
        def accept_waveform(self, x): self.i += 1
        def is_speech_detected(self): return self.pattern[min(self.i, len(self.pattern) - 1)]

    def frames_for(pattern):
        q = queue.Queue()
        for _ in pattern:
            q.put(A.np.zeros(A.FRAME, A.np.int16))
        return q

    talk = lambda s: [True] * int(s / sec)
    pause = lambda s: [False] * int(s / sec)
    directions = talk(3.0) + pause(0.5) + talk(2.0) + pause(2.0)
    A._vad = FakeVAD(directions)
    cmd, _ = A.record_command(frames_for(directions), [], 300.0, finished=lambda: True)
    assert len(cmd) * sec > 5.0                      # heard the part after the thinking pause

    quick = talk(1.0) + pause(2.0)
    A._vad = FakeVAD(quick)
    cmd, _ = A.record_command(frames_for(quick), [], 300.0, finished=lambda: True)
    assert len(cmd) * sec < 1.4                      # short command: ends fast
    A._vad = None
    A.RHYTHM.gaps.clear()
    A.RHYTHM.rates.clear()


def test_rhythm_adapts_to_how_fast_you_talk():
    fast, slow = A.Rhythm(), A.Rhythm()
    assert fast.end_silence(False) == A.END_SILENCE_S and fast.voice_speed() == 1.0   # before learning
    for _ in range(6):
        fast.gaps.append(0.28)
        slow.gaps.append(0.55)
        fast.note_rate(12, 3.0)
        slow.note_rate(6, 3.0)          # 4 vs 2 words a second
    assert fast.end_silence(False) < slow.end_silence(False)     # quick talker, quicker reply
    assert fast.end_silence(True) < slow.end_silence(True)
    assert 0.45 <= fast.end_silence(False) and slow.end_silence(True) <= 1.2   # never extreme
    assert fast.voice_speed() > 1.0 > slow.voice_speed() >= 0.95


def test_pause_after_the_name_does_not_end_the_request():
    """'Zephyr,' ... 0.7 s pause ... 'volume at ten.' must be heard in full (cut at 0.6 s on 26 Oct)."""
    import queue
    sec = A.FRAME / A.RATE

    class FakeVAD:
        def __init__(self, pattern): self.pattern, self.i = pattern, -1
        def reset(self): pass
        def accept_waveform(self, x): self.i += 1
        def is_speech_detected(self): return self.pattern[min(self.i, len(self.pattern) - 1)]

    pattern = [False] * int(0.7 / sec) + [True] * int(1.0 / sec) + [False] * int(2.0 / sec)
    q = queue.Queue()
    for _ in pattern:
        q.put(A.np.zeros(A.FRAME, A.np.int16))
    name = [A.np.zeros(A.FRAME, A.np.int16)] * 10          # the audio holding "Zephyr"
    A._vad = FakeVAD(pattern)
    said = int(1.7 / sec)                                  # the transcript only reads as complete once it's said
    cmd, heard = A.record_command(q, name, 300.0, giveup=2.0, finished=lambda: A._vad.i >= said)
    A._vad = None
    A.RHYTHM.gaps.clear()
    A.RHYTHM.rates.clear()
    assert heard and (len(cmd) - len(name)) * sec >= 1.6   # waited through the 0.7 s pause and heard the 1 s request


def test_silence_announcements_are_not_spoken():
    assert A.speakable("That's a fragment with no clear request, so I'll stay quiet.") == ''
    assert A.speakable('Leave the lights on, I like them.') != ''


def test_busy_note_for_the_home_keeper(tmp_path, monkeypatch):
    note = tmp_path / '.zephyr-busy'
    monkeypatch.setattr(A, 'BUSY_FILE', str(note))
    A.busy(40)
    first = float(note.read_text())
    assert 35 < first - A.time.time() <= 41                     # stored to the whole second
    A.busy(5)                                                   # a shorter request never cuts it short
    assert float(note.read_text()) == first


@pytest.mark.parametrize('app, closes', [(A.HOME_APP, True), (A.YOUTUBE_APP, True), (A.MOONLIGHT_APP, False)])
def test_opening_anything_else_ends_the_laptop_stream(monkeypatch, app, closes):
    """Home or another app closes Moonlight, so Sunshine turns the TV's virtual screen off."""
    closed = []
    monkeypatch.setattr(A, 'busy', lambda s: None)
    monkeypatch.setattr(A, 'app_state', lambda a: {'running': a == A.MOONLIGHT_APP, 'visible': False})
    monkeypatch.setattr(A, 'tv_close', lambda a: closed.append(a) or True)
    monkeypatch.setattr(A.urllib.request, 'urlopen', lambda req, timeout: type('R', (), {'read': lambda self: b'true'})())
    assert A.tv_open(app)
    assert closed == ([A.MOONLIGHT_APP] if closes else [])


def test_good_night_puts_the_screen_timeout_back(monkeypatch, tmp_path):
    """Screens go dark through Windows' own timeout (not the sleep-button message, which put the laptop
    into standby and froze Zephyr), and the original timeout comes back afterwards."""
    timeout = {'now': 0}
    monkeypatch.setattr(A, 'SCREEN_TIMEOUT_NOTE', str(tmp_path / 'note'))
    monkeypatch.setattr(A, '_screen_timeout', lambda s=None: timeout['now'] if s is None else timeout.update(now=s))
    monkeypatch.setattr(A.threading, 'Timer', lambda delay, fn: type('T', (), {'start': lambda self: None})())
    A.laptop_screens(False)
    assert timeout['now'] == A.SCREEN_OFF_AFTER_S
    A.laptop_screens(False)                     # said twice: still remembers the real original
    A.restore_screen_timeout()
    assert timeout['now'] == 0
    A.restore_screen_timeout()                  # nothing left to put back
    assert timeout['now'] == 0


@pytest.mark.parametrize('heard, command', [
    ('Let her go home.', 'go home'),
    ('With effort, go home.', 'go home'),
    ('Is that fair? Good night.', 'Good night'),
    ('Nefer, what time is it?', 'what time is it'),
])
def test_misheard_name_at_the_start_still_counts(heard, command):
    assert A.NAME_AT_START.search(heard) or A.NAME_LOOSE.search(heard)
    assert A.without_name(heard) == command


def test_everyday_words_later_in_a_sentence_are_not_the_name():
    assert not A.NAME_AT_START.search('you should let her go home')
    assert A.without_name('you should let her go home') == 'you should let her go home'


def test_speakerphone_boost_lifts_quiet_speech_and_never_clips():
    quiet = np.full(10, 400, dtype=np.int16)
    loud = np.array([30000, -32768], dtype=np.int16)
    assert abs(int(A.soft_gain(quiet, 2.5)[0]) - 1000) < 20          # ~2.5x while quiet
    boosted = A.soft_gain(loud, 2.5)
    assert boosted.max() <= 32767 and boosted.min() >= -32767       # rounds off, no wrap-around


@pytest.mark.parametrize('written, said', [
    ("Volume's at 15.", "Volume's at fifteen."),
    ('Volume 42 now', 'Volume forty-two now'),
    ('It has 105 videos', 'It has one hundred and five videos'),
    ("It's 6:08 in the morning.", "It's 6:08 in the morning."),
    ('About 2.5 seconds', 'About 2.5 seconds'),
    ('Born in 1999', 'Born in 1999'),
])
def test_numbers_are_spoken_as_words(written, said):
    assert A.spoken_numbers(written) == said


@pytest.mark.parametrize('heard', ['Is that', 'Is that fair?', 'Zip.'])
def test_the_name_alone_cut_short_means_listen_for_the_command(heard):
    assert A.NAME_AT_START.search(heard)
    assert A.without_name(heard) == ''


def test_is_that_in_a_real_question_is_left_alone():
    assert A.without_name('is that the new Batman movie') == 'is that the new Batman movie'


@pytest.mark.parametrize('said', ["I'm going to watch a movie", "I'm heading to the kitchen to cook", 'going to bed soon maybe'])
def test_going_somewhere_else_is_not_leaving(said):
    action = A.parse(said)
    assert action is None or action.__name__ != 'heading_out'


@pytest.mark.parametrize('said, level', [
    ('Zephyr volume ten', 10), ('volume to twenty five', 25), ('Zephyr, volume at 15.', 15),
    ('set the volume to thirty', 30), ('volume twenty-five please', 25), ('TV volume 8', 8),
])
def test_volume_is_set_instantly(said, level):
    action = A.parse(said)
    assert action is not None and action.volume == level
    assert A.quick_ack(action, said) == f"Volume's at {level}."


@pytest.mark.parametrize('said', ['volume attempts', 'volume up a bit', 'what is the volume', 'volume 250',
                                  'volume twenty ten'])
def test_unclear_volume_goes_to_claude(said):
    action = A.parse(said)
    assert action is None or not hasattr(action, 'volume')


@pytest.mark.parametrize('narration', ["Mark's request has no clear name in front of it, but it's a direct ask.",
                                       "I'll act on it.",
                                       "Mishearing aside, Japanese Denim by Daniel Caesar is the one.", "Japanese Denim by Daniel Caesar, I'm guessing.",
                                       "The transcript has Robert and Danny as names, which doesn't clearly match a command."])
def test_name_narration_is_never_spoken(narration):
    assert A.speakable(narration) == ''


def test_a_command_said_in_one_breath_ends_quickly():
    """'Zephyr volume at ten' in one go: no new speech after the spotter fires, but the live transcript
    is complete, so recording stops at ~0.6 s instead of waiting out the 3.5 s give-up."""
    import queue
    sec = A.FRAME / A.RATE

    class FakeVAD:
        def reset(self): pass
        def accept_waveform(self, x): pass
        def is_speech_detected(self): return False

    q = queue.Queue()
    for _ in range(int(4 / sec)):
        q.put(A.np.zeros(A.FRAME, A.np.int16))
    A._vad = FakeVAD()
    cmd, heard = A.record_command(q, [], 300.0, giveup=3.5, finished=lambda: True)
    A._vad = None
    assert len(cmd) * sec < 1.0


def test_deepgram_session_builds_the_text_and_knows_when_you_stopped():
    import json as _json

    class FakeWS:
        def __init__(self, messages):
            self.messages, self.sent = list(messages), []
        def recv(self):
            if not self.messages:
                raise ConnectionError('closed')
            return _json.dumps(self.messages.pop(0))
        def send(self, m): self.sent.append(m)
        def send_binary(self, b): pass
        def close(self): pass

    def result(text, final=False, finalize=False):
        return {'type': 'Results', 'is_final': final, 'from_finalize': finalize, 'channel': {'alternatives': [{'transcript': text}]}}
    ws = FakeWS([result('Zephyr volume'), result('Zephyr volume to'), result('Zephyr, volume to 20.', final=True),
                 result('', final=True, finalize=True)])
    s = A.DeepgramSession(ws)
    s.finalized.wait(2)
    assert s.text() == 'Zephyr, volume to 20.'
    assert s.sounds_finished() and s.settled() and not s.dangling()
    assert any('Finalize' in m for m in ws.sent)
    mid = A.DeepgramSession(FakeWS([result('Zephyr can you play uh')]))
    A.time.sleep(0.1)
    assert mid.dangling() and not mid.settled()


def test_effort_at_the_start_is_the_name():
    assert A.without_name("Effort. I'm heading up.") == "I'm heading up"
