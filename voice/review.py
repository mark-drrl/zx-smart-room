"""Zephyr's nightly self-review (continuous improvement).

Reads the last day of assistant.log, measures how she did (speed, mishearings, ignored voices,
interruptions, fallbacks, errors), and asks Claude to find mistakes in the conversations. Then:
  - new lessons go to <vault>/Zephyr/Lessons.md, which she reads before every reply;
  - a report goes to <vault>/Zephyr/Reports/<date>.md, with ideas for code changes.
Runs from the "Zephyr Nightly Review" scheduled task; run by hand with:  python -P review.py [hours]
Cost: one Claude Haiku request, a fraction of a cent.
"""
import datetime
import json
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import anthropic  # noqa: E402

from brain import SYSTEM  # noqa: E402
from keystore import fill  # noqa: E402
from memory import Memory  # noqa: E402

MODEL = 'claude-haiku-5-5'
LINE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})  (.*)$')

REVIEW_TOOL = {
    'name': 'submit_review',
    'description': 'Submit the review of the day.',
    'input_schema': {
        'type': 'object',
        'properties': {
            'summary': {'type': 'string', 'description': 'Two or three plain sentences: how the day went.'},
            'lessons': {'type': 'array', 'items': {'type': 'string'},
                        'description': 'New lessons for Zephyr herself, phrased as instructions to her.'},
            'code_ideas': {'type': 'array', 'items': {'type': 'string'},
                           'description': 'Problems only a code change can fix, for the developer.'},
        },
        'required': ['summary', 'lessons', 'code_ideas'],
        'additionalProperties': False,
    },
    'strict': True,
}

PROMPT = """You are reviewing one day of logs from Zephyr, a voice assistant that controls a Samsung \
Frame TV in Mark's living room (speech-to-text -> Claude -> text-to-speech). Find what went wrong \
and how she should behave differently.

Look for: misheard words she acted on wrongly, numbers she got wrong, replies that were too long or \
asked needless questions, times she answered chatter that wasn't meant for her, requests she failed \
or refused that she could have done, and anything Mark had to repeat or correct.

Lessons: only new, specific, durable ones that change her behaviour next time (e.g. "When the \
transcript says 'Zafer', that is your name"). At most 5. Skip anything already in her current lessons. \
No lessons for one-off glitches. A lesson must never contradict her standing instructions below: those were written deliberately (for example, she silently corrects "volume 250" to fifty on purpose).
Code ideas: problems in timing, detection or reliability that need a developer, with the evidence. At most 5.

Her standing instructions:
{rules}

Her current lessons:
{lessons}

Measurements for the day:
{metrics}

The day's log (her side of each exchange):
{log}"""


def read_log(hours):
    since = datetime.datetime.now() - datetime.timedelta(hours=hours)
    rows = []
    for name in ('assistant.log.1', 'assistant.log'):
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                m = LINE.match(line.rstrip('\n'))
                if m and datetime.datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S') >= since:
                    rows.append((m.group(1), m.group(2)))
    return rows


def measure(rows):
    msgs = [m for _, m in rows]
    starts = [float(x) for x in re.findall(r'voice started after ([\d.]+) s', ' '.join(msgs))]
    ends = [m.split('ended by ')[1].split(' ')[0] for m in msgs if 'ended by ' in m]
    count = lambda pat: sum(1 for m in msgs if re.search(pat, m))
    return {
        'conversations with Claude': count(r'-> Claude \('),
        'instant TV commands': count(r'^heard: .* -> (?!Claude)'),
        'TV actions by Claude': count(r'^Claude used '),
        'median time to first word (s)': round(statistics.median(starts), 2) if starts else None,
        'slowest time to first word (s)': max(starts) if starts else None,
        'wake-ups ignored (name not in transcript)': count(r'name not in transcript'),
        'voices rejected (not Mark)': count(r'not your voice'),
        'interruptions': count(r'^interrupted$'),
        'slow transcript fallbacks (upload)': count(r'transcript \(upload'),
        'voice fallbacks to Kokoro': count(r'using Kokoro'),
        'errors': count(r'error|crashed|failed'),
        'recordings ended by time limit': ends.count('time'),
    }


def condensed(rows, limit=60000):
    keep = re.compile(r'^(heard:|Claude used|transcript|not your voice|name not in|interrupted|error|.*failed)')
    text = '\n'.join(f'{t[11:16]} {m}' for t, m in rows if keep.match(m))
    return text[-limit:]


def main():
    hours = float(sys.argv[1]) if len(sys.argv) > 1 else 24
    env = {}
    for line in open(os.path.join(HERE, 'corelynx.env'), encoding='utf-8'):
        if '=' in line and not line.lstrip().startswith('#'):
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()
    fill(env)
    memory = Memory(env.get('OBSIDIAN_VAULT') or os.path.join(os.path.expanduser('~'), 'Documents', 'Zephyr Vault'))
    rows = read_log(hours)
    metrics = measure(rows)
    day = datetime.date.today().isoformat()
    reports = os.path.join(memory.dir, 'Reports')
    os.makedirs(reports, exist_ok=True)

    review = {'summary': 'No conversations in this period.', 'lessons': [], 'code_ideas': []}
    if metrics['conversations with Claude'] + metrics['instant TV commands'] > 0:
        client = anthropic.Anthropic(api_key=env['ANTHROPIC_API_KEY'], timeout=120.0)
        response = client.messages.create(
            model=MODEL, max_tokens=4000, tools=[REVIEW_TOOL], tool_choice={'type': 'tool', 'name': 'submit_review'},
            output_config={'effort': 'medium'},
            messages=[{'role': 'user', 'content': PROMPT.format(
                rules=SYSTEM.format(name='Zephyr'),
                lessons='\n'.join('- ' + l for l in memory.lessons()) or '(none yet)',
                metrics=json.dumps(metrics, indent=1), log=condensed(rows))}])
        block = next(b for b in response.content if b.type == 'tool_use')
        review = block.input

    added = [l for l in review['lessons'][:5] if memory.add_lesson(l, source='review') == 'Lesson saved.']
    with open(os.path.join(reports, day + '.md'), 'w', encoding='utf-8') as f:
        f.write(f'# Zephyr review {day}\n\n{review["summary"]}\n\n## Measurements (last {hours:g} h)\n\n')
        f.write('| | |\n|---|---|\n' + ''.join(f'| {k} | {v} |\n' for k, v in metrics.items()))
        f.write('\n## New lessons\n\n' + (''.join(f'- {l}\n' for l in added) or '- none\n'))
        f.write('\n## Ideas for code changes\n\n' + (''.join(f'- {c}\n' for c in review['code_ideas'][:5]) or '- none\n'))
    print(f'Review written: {os.path.join(reports, day + ".md")} ({len(added)} new lessons)')


if __name__ == '__main__':
    main()
