"""Claude's tools: inputs are checked before anything touches the TV."""
import pytest

import brain as B

TV_KEYS = ['on', 'off', 'off_fully', 'art', 'home', 'open', 'volume', 'mute', 'keys', 'status', 'youtube',
           'youtube_control', 'spotify', 'spotify_control', 'setting', 'scene']


def make_brain(calls):
    def recorder(key):
        def call(*a, **kw):
            calls.append((key, a, kw))
            return 'Done.'
        return call
    return B.Brain('test-key', 'Zephyr', {k: recorder(k) for k in TV_KEYS})


def test_every_tool_has_a_strict_schema():
    names = [t['name'] for t in B.TOOLS]
    assert len(names) == len(set(names))
    for t in B.TOOLS:
        assert t['strict'] is True and t['input_schema']['additionalProperties'] is False


def test_fast_tools_exist():
    names = {t['name'] for t in B.TOOLS}
    assert B.FAST_TOOLS <= names
    assert set(B.SLOW_ACK) <= B.FAST_TOOLS


@pytest.mark.parametrize('tool, args', [
    ('tv_power', {'state': 'on'}), ('tv_power', {'state': 'off_fully'}), ('scene', {'name': 'forest'}),
    ('tv_volume', {'mode': 'set', 'amount': 12}), ('tv_volume', {'mode': 'down', 'amount': 3}),
    ('tv_mute', {'muted': True}), ('tv_remote', {'keys': ['KEY_UP', 'KEY_ENTER']}),
    ('tv_open_app', {'app': 'YouTube'}), ('spotify_play', {'query': 'September', 'kind': 'track'}),
    ('spotify_control', {'action': 'next'}), ('youtube_play', {'query': 'lofi'}),
    ('youtube_control', {'action': 'pause'}), ('tv_setting', {'setting': 'picture_mode', 'value': 'Movie'}),
])
def test_valid_tool_calls_reach_the_tv(tool, args):
    calls = []
    make_brain(calls)._run_tool(tool, args)
    assert calls


@pytest.mark.parametrize('tool, args', [
    ('tv_power', {'state': 'explode'}), ('scene', {'name': 'disco'}), ('tv_volume', {'mode': 'set', 'amount': 'loud'}),
    ('tv_remote', {'keys': ['rm -rf']}), ('tv_remote', {'keys': []}), ('tv_open_app', {'app': 'Netflux'}),
    ('spotify_play', {'query': '', 'kind': 'track'}), ('tv_setting', {'setting': 'firmware', 'value': 'x'}),
    ('unknown_tool', {}), ('tv_mute', 'not a dict'), ('learn_lesson', {'lesson': '  '}),
])
def test_bad_tool_calls_are_refused(tool, args):
    calls = []
    with pytest.raises(ValueError):
        make_brain(calls)._run_tool(tool, args)
    assert not calls


def test_volume_amount_is_clamped():
    calls = []
    make_brain(calls)._run_tool('tv_volume', {'mode': 'set', 'amount': 999})
    assert calls[0][2] == {'level': 100}


def test_failure_words_are_recognised():
    assert B.FAILED.search("Couldn't change the picture mode.")
    assert not B.FAILED.search("Volume's at 8.")
