"""Probe before adopting: does a proposed lesson actually make Zephyr better?

The Propose-Probe-Compose idea from Google Research's Harness2 (github.com/google-research/harness2),
scaled down for a voice assistant:
  propose  the nightly review suggests new lessons;
  probe    Mark's recent real requests are replayed twice, with today's lessons and with the new
           ones added. The TV, Spotify, YouTube and memory are stand-ins: nothing happens in the room
           and nothing is written to the vault;
  compose  a judge compares each pair of answers blind (A/B order shuffled), and the lessons are
           adopted only if they win at least as often as they lose.
Cost: about 2 short Haiku requests per probed question plus one judge request.
"""
import os
import random
import shutil
import tempfile

import anthropic

from brain import MODEL, Brain
from memory import Memory

STAND_IN_RESULTS = {
    'on': 'Turning on the TV.', 'off': 'Off. The screen sleeps after five minutes.', 'off_fully': 'Off.',
    'art': 'Art Mode.', 'home': 'Going home.', 'status': 'The TV is on. YouTube is on screen. Volume is 10.',
    'keys': 'Pressed.', 'mute': 'Muted.', 'youtube_control': 'Paused.', 'spotify_control': 'Skipped.',
}

JUDGE_TOOL = {
    'name': 'verdicts',
    'description': 'One verdict per numbered pair.',
    'input_schema': {
        'type': 'object',
        'properties': {'winners': {'type': 'array', 'items': {'type': 'string', 'enum': ['A', 'B', 'tie']}}},
        'required': ['winners'],
        'additionalProperties': False,
    },
    'strict': True,
}

JUDGE_PROMPT = """Zephyr is a spoken voice assistant in Mark's living room; she controls the TV and \
chats. For each numbered request below, two versions of her reply are shown (A and B), each with the \
actions she took. Pick the better reply, or "tie" if they are equally good.

Better means, in order: does what Mark asked (right action, right number); short and natural for \
speech; no narrating her reasoning or rules; no needless questions; warm, not robotic. Ignore tiny \
wording differences: call those a tie.

{pairs}"""


def stand_in_tv(actions):
    def record(name, result):
        def call(*args, **kwargs):
            actions.append(f'{name}{args or ""}{kwargs or ""}')
            return result
        return call
    tv = {k: record(k, v) for k, v in STAND_IN_RESULTS.items()}
    tv['volume'] = lambda level=None, change=None: actions.append(f'volume({level}, {change})') or f"Volume's at {level if level is not None else 10 + (change or 0)}."
    tv['open'] = lambda app: actions.append(f'open({app})') or f'Opening {app}.'
    tv['youtube'] = lambda q: actions.append(f'youtube({q})') or f'Playing {q.title()}.'
    tv['spotify'] = lambda q, kind: actions.append(f'spotify({q}, {kind})') or f'Playing {q} on Spotify.'
    tv['setting'] = lambda s, v: actions.append(f'setting({s}, {v})') or f"{v} {s.split('_')[0]}'s on."
    return tv


def answer(api_key, vault_copy, question, client=None):
    """Zephyr's reply to one request, with stand-ins for everything that would act in the room."""
    actions = []
    brain = Brain(api_key, 'Zephyr', stand_in_tv(actions), Memory(vault_copy))
    if client is not None:
        brain.client = client
    try:
        reply = brain.ask(question)
    except Exception as e:                       # a probe failure must never break the nightly review
        reply = f'(error: {e})'
    return reply, actions


def probe(candidate_lessons, vault, api_key, questions, client=None, judge=None, seed=None):
    """Returns {'wins', 'losses', 'ties', 'pairs'} for the candidate lessons against today's."""
    rng = random.Random(seed)
    work = tempfile.mkdtemp(prefix='zephyr-probe-')
    try:
        base_dir, cand_dir = os.path.join(work, 'base'), os.path.join(work, 'cand')
        for d in (base_dir, cand_dir):
            shutil.copytree(os.path.join(vault, 'Zephyr'), os.path.join(d, 'Zephyr'))
        cand_memory = Memory(cand_dir)
        for lesson in candidate_lessons:
            cand_memory.add_lesson(lesson, source='probe')
        pairs = []
        for q in questions:
            # the copies' conversation notes must not grow during the probe
            base = answer(api_key, base_dir, q, client)
            cand = answer(api_key, cand_dir, q, client)
            flip = rng.random() < 0.5
            pairs.append({'question': q, 'base': base, 'cand': cand, 'cand_is': 'A' if flip else 'B'})
    finally:
        shutil.rmtree(work, ignore_errors=True)

    text = '\n\n'.join(
        f'{i + 1}. Mark: "{p["question"]}"\n'
        f'   A: "{(p["cand"] if p["cand_is"] == "A" else p["base"])[0]}"  actions: {(p["cand"] if p["cand_is"] == "A" else p["base"])[1]}\n'
        f'   B: "{(p["base"] if p["cand_is"] == "A" else p["cand"])[0]}"  actions: {(p["base"] if p["cand_is"] == "A" else p["cand"])[1]}'
        for i, p in enumerate(pairs))
    judge = judge or anthropic.Anthropic(api_key=api_key, timeout=120.0)
    response = judge.messages.create(
        model=MODEL, max_tokens=1000, tools=[JUDGE_TOOL], tool_choice={'type': 'tool', 'name': 'verdicts'},
        messages=[{'role': 'user', 'content': JUDGE_PROMPT.format(pairs=text)}])
    winners = next(b for b in response.content if b.type == 'tool_use').input['winners']
    result = {'wins': 0, 'losses': 0, 'ties': 0, 'pairs': pairs}
    for p, w in zip(pairs, winners, strict=False):
        p['winner'] = w
        if w == 'tie':
            result['ties'] += 1
        elif w == p['cand_is']:
            result['wins'] += 1
        else:
            result['losses'] += 1
    return result


def adopt(result):
    """Compose: keep the lessons only if they help at least as often as they hurt. Replies vary a
    little run to run even with identical instructions, so a neutral lesson scores about even; a
    harmful one loses clearly (live trial 26 Oct: "always ask to confirm first" lost 1-3)."""
    probed = result['wins'] + result['losses'] + result['ties']
    return probed > 0 and result['wins'] >= result['losses']
