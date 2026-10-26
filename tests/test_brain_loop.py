"""Claude's reply loop (brain.ask_stream) against a fake Claude: no network, no cost.

Covers the behaviour that broke in real use: fast TV confirmations, never repeating a
confirmation, cutting recently used openers, tool errors, refusals and API failures."""
import types

import anthropic
import pytest

import brain as B
import memory as M


class FakeStream:
    def __init__(self, texts, final):
        self.texts, self.final = texts, final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for t in self.texts:
            yield types.SimpleNamespace(type='text', text=t)

    def get_final_message(self):
        return self.final


def tool_use(name, inp, id='t1'):
    return types.SimpleNamespace(type='tool_use', name=name, input=inp, id=id)


def text(t):
    return types.SimpleNamespace(type='text', text=t)


def reply(texts, stop='end_turn', tools=()):
    return FakeStream(texts, types.SimpleNamespace(stop_reason=stop, content=[text(''.join(texts))] + list(tools)))


class FakeClaude:
    """Plays back scripted turns and records every request."""

    def __init__(self, turns):
        self.turns, self.requests = list(turns), []
        self.messages = self

    def stream(self, **kw):
        self.requests.append(kw)
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


def make(turns, memory=None, tv_result="Volume's at 12."):
    calls = []
    tv = {k: (lambda *a, k=k, **kw: calls.append((k, a, kw)) or tv_result) for k in
          ['on', 'off', 'off_fully', 'art', 'home', 'open', 'volume', 'mute', 'keys', 'status', 'youtube',
           'youtube_control', 'spotify', 'spotify_control', 'setting']}
    b = B.Brain('test-key', 'Zephyr', tv, memory)
    b.client = FakeClaude(turns)
    return b, calls


def test_plain_answer_is_streamed():
    b, _ = make([reply(['Tok', 'yo.'])])
    assert b.ask('capital of Japan?') == 'Tokyo.'


def test_tv_action_result_is_spoken_without_a_second_claude_call():
    b, calls = make([reply([], 'tool_use', [tool_use('tv_volume', {'mode': 'set', 'amount': 12})])])
    assert b.ask('volume to twelve') == "Volume's at 12."
    assert calls[0][0] == 'volume' and len(b.client.requests) == 1          # one round trip, not two
    assert b.history[-1] == {'role': 'assistant', 'content': "Volume's at 12."}


def test_slow_actions_get_an_instant_lead_in():
    b, _ = make([reply([], 'tool_use', [tool_use('spotify_play', {'query': 'jazz', 'kind': 'playlist'})])],
                tv_result='Playing the playlist "Jazz" on Spotify.')
    assert b.ask('play jazz') == 'Putting it on.  Playing the playlist "Jazz" on Spotify.'


def test_a_confirmation_claude_already_said_is_not_repeated():
    b, _ = make([reply(["Volume's at 15."], 'tool_use', [tool_use('tv_volume', {'mode': 'set', 'amount': 15})])],
                tv_result="Volume's at 15.")
    assert b.ask('volume 15') == "Volume's at 15."                          # once, not twice (26 Oct)


def test_failed_actions_go_back_to_claude_to_explain():
    b, _ = make([reply([], 'tool_use', [tool_use('tv_setting', {'setting': 'picture_mode', 'value': 'Movie'})]),
                 reply(["The TV didn't respond, try again in a sec."])],
                tv_result="Couldn't change the picture mode.")
    # the instant "Sure." covers the ~2 s SmartThings takes; then Claude explains the failure
    assert b.ask('movie mode') == "Sure.  The TV didn't respond, try again in a sec."
    assert len(b.client.requests) == 2


def test_bad_tool_input_is_reported_to_claude_not_run():
    b, calls = make([reply([], 'tool_use', [tool_use('tv_volume', {'mode': 'set', 'amount': 'loud'})]),
                     reply(['What level?'])])
    assert b.ask('volume loud') == 'What level?' and not calls
    results = [m['content'][0] for m in b.history if m['role'] == 'user' and isinstance(m['content'], list)]
    assert results[0]['is_error'] and 'Failed' in results[0]['content']


def test_refusal_is_answered_briefly():
    b, _ = make([reply([], 'refusal')])
    assert b.ask('something odd') == "Sorry, I can't help with that one."


@pytest.mark.parametrize('error, says', [
    (anthropic.APIConnectionError(request=None), "can't reach Claude"),
])
def test_api_failures_are_spoken_and_history_rolled_back(error, says):
    b, _ = make([error])
    assert says in b.ask('hello')
    assert b.history == []                                                   # nothing half-saved


def test_recently_used_opener_is_cut(tmp_path):
    m = M.Memory(str(tmp_path))
    m.log_turn('tell me a joke', 'Ha, okay. Why did the chicken...')
    b, _ = make([reply(['Ha, ', 'okay. ', 'What do you call a sleeping bull? A bulldozer.'])], memory=m)
    assert b.ask('another joke') == 'What do you call a sleeping bull? A bulldozer.'


def test_fresh_opener_is_kept(tmp_path):
    m = M.Memory(str(tmp_path))
    m.log_turn('tell me a joke', 'Ha, okay. Why did the chicken...')
    b, _ = make([reply(['Ooh, ', 'nice one. Sure.'])], memory=m)
    assert b.ask('play something') == 'Ooh, nice one. Sure.'


def test_memory_lessons_and_recent_talk_reach_claude(tmp_path):
    m = M.Memory(str(tmp_path))
    m.remember('Mark has no Netflix account')
    m.add_lesson('Zafer is your name')
    m.log_turn('what time is it', "It's ten.")
    b, _ = make([reply(['Sure.'])], memory=m)
    b.ask('hi')
    system = b.client.requests[0]['system']
    assert system[0].get('cache_control')                                     # fixed part is cached
    extra = system[1]['text']
    assert 'no Netflix' in extra and 'Zafer is your name' in extra and "It's ten." in extra


def test_lesson_tool_writes_to_obsidian(tmp_path):
    m = M.Memory(str(tmp_path))
    b, _ = make([reply([], 'tool_use', [tool_use('learn_lesson', {'lesson': 'Fifteen means 15'})]),
                 reply(['Got it.'])], memory=m)
    b.ask('no, I said fifteen')
    assert m.lessons()[0].startswith('Fifteen means 15')
