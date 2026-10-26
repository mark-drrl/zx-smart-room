"""Probe-before-adopt (probe.py) with a fake Claude and a fake judge: nothing leaves the laptop."""
import types

import memory as M
import probe as P
from test_brain_loop import FakeClaude, reply


class FakeJudge:
    def __init__(self, pick):
        self.pick, self.prompt = pick, None
        self.messages = self

    def create(self, **kw):
        self.prompt = kw['messages'][0]['content']
        n = self.prompt.count('Mark: "')
        return types.SimpleNamespace(content=[types.SimpleNamespace(
            type='tool_use', input={'winners': [self.pick(i) for i in range(n)]})])


def vault(tmp_path):
    m = M.Memory(str(tmp_path))
    m.add_lesson('Zafer is your name')
    return str(tmp_path)


def test_probe_counts_wins_for_the_candidate_whichever_side_it_is_on(tmp_path):
    """Answers are shown to the judge in random A/B order; a judge that always prefers the
    candidate's answer must be counted as a win every time."""
    import re
    questions = ['volume to ten', 'tell me a joke', 'play jazz', 'what time is it']
    claude = FakeClaude([reply([text]) for _ in questions for text in ('BASE answer.', 'CAND answer.')])

    def prefers_candidate(prompt):
        pairs = re.findall(r'A: "([^"]*)".*?\n\s*B: "([^"]*)"', prompt)
        return ['A' if 'CAND' in a else 'B' for a, b in pairs]
    judge = FakeJudge(None)
    judge.create = lambda **kw: types.SimpleNamespace(content=[types.SimpleNamespace(
        type='tool_use', input={'winners': prefers_candidate(kw['messages'][0]['content'])})])
    result = P.probe(['Keep replies short'], vault(tmp_path), 'k', questions, client=claude, judge=judge, seed=3)
    assert {p['cand_is'] for p in result['pairs']} == {'A', 'B'}       # both orders really occurred
    assert (result['wins'], result['losses'], result['ties']) == (4, 0, 0) and P.adopt(result)


def test_probe_never_touches_the_real_vault_or_tv(tmp_path):
    v = vault(tmp_path)
    before = sorted(p.name for p in (tmp_path / 'Zephyr').rglob('*'))
    lessons_before = M.Memory(v).lessons()
    claude = FakeClaude([reply([], 'tool_use', [types.SimpleNamespace(type='tool_use', name='tv_power',
                                                                       input={'state': 'off_fully'}, id='t')])
                         for _ in range(2)])
    P.probe(['A new lesson'], v, 'k', ['shut down the tv'], client=claude, judge=FakeJudge(lambda i: 'tie'))
    assert sorted(p.name for p in (tmp_path / 'Zephyr').rglob('*')) == before
    assert M.Memory(v).lessons() == lessons_before                      # candidate never written for real


def test_adoption_rule():
    assert P.adopt({'wins': 3, 'losses': 1, 'ties': 6})
    assert P.adopt({'wins': 0, 'losses': 0, 'ties': 10})                # harmless: fine to keep
    assert not P.adopt({'wins': 2, 'losses': 3, 'ties': 5})             # worse more often than better
    assert P.adopt({'wins': 3, 'losses': 3, 'ties': 4})                 # even: normal run-to-run variation
    assert not P.adopt({'wins': 1, 'losses': 3, 'ties': 6})             # the harmful lesson from the live trial
    assert not P.adopt({'wins': 0, 'losses': 0, 'ties': 0})             # nothing probed: hold back


def test_stand_in_tv_records_actions_without_acting():
    actions = []
    tv = P.stand_in_tv(actions)
    assert tv['volume'](level=12) == "Volume's at 12."
    assert tv['off_fully']() == 'Off.'
    assert actions == ['volume(12, None)', 'off_fully']
