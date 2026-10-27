"""Reads Zephyr's log (assistant.log) and groups its lines into requests: what you said, what she
heard, what she did, how long it took, and why it was dropped. Used by watch.py (terminal) and
console.py (browser)."""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.environ.get('ZEPHYR_LOG') or os.path.join(HERE, 'assistant.log')

LINE = re.compile(r'^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d)  (.*)$')
WAKE = re.compile(r'^wake word \(score [\d.]+, "(.+?)" spotted ([\d.]+)')
RECORDED = re.compile(r'^recorded ([\d.]+) s, ended by ([a-z ]+?)(?: \(|$)')
VOICE = re.compile(r'^voice match ([\d.]+)')
NOT_YOU = re.compile(r'^not your voice \(match ([\d.]+) < ([\d.]+)')
TRANSCRIPT = re.compile(r'^transcript \((\w+), ([\d.]+) s after you stopped\): "(.*)"$')
NO_NAME = re.compile(r'^name not in transcript \(spotter ([\d.]+)\)')
TOOL = re.compile(r'^Claude used (\w+) (\{.*?\}) -> (.*)$')
HEARD = re.compile(r'^heard: "(.*)" -> (.*)$')
CLAUDE = re.compile(r'^Claude \(voice started after ([\d.]+) s, done ([\d.]+) s\): ?(.*)$')
DIAG = re.compile(r'^diag (\S+\.wav)')
PROBLEM = re.compile(r'crashed|failed|error|exception|timed out', re.I)
STARTED = re.compile(r'^\w+ listening on "(.*)"')
FAILED_REPLY = re.compile(r"couldn'?t|can'?t|isn'?t|didn'?t|not connected|nothing found|no app|is off|failed|still on", re.I)
SILENT_REPLY = re.compile(r"stay quiet|stay silent|nothing for me|not (meant )?for me|leave (it|that)|fragment|^$", re.I)


def read_lines(path=LOG, with_previous=True):
    """The log's lines, oldest first (including the rotated assistant.log.1 when asked)."""
    lines = []
    for p in ([path + '.1'] if with_previous else []) + [path]:
        try:
            with open(p, encoding='utf-8', errors='replace') as f:
                lines.extend(f.read().splitlines())
        except OSError:
            pass
    return lines


def _new(day, clock, spotter=None):
    return {'day': day, 'time': clock, 'spotter': spotter, 'recorded': None, 'ended_by': None, 'voice': None,
            'voice_bar': None, 'transcripts': [], 'heard': None, 'reply': None, 'kind': None, 'tools': [],
            'reply_after': None, 'transcript_after': None, 'clip': None, 'status': 'listening', 'why': None,
            'follow_up': False}


def _finish(req, status, why=None):
    req['status'], req['why'] = status, why


def parse(lines):
    """Lines -> (requests, events). Requests: one per time she heard her name (newest last).
    Events: start-ups and problems that aren't part of a request."""
    requests, events, open_req = [], [], None
    for raw in lines:
        m = LINE.match(raw)
        if not m:
            continue
        day, clock, msg = m.groups()
        if (w := WAKE.match(msg)):
            open_req = _new(day, clock, float(w.group(2)))
            requests.append(open_req)
            continue
        if (h := HEARD.match(msg)):
            # a slow TV action logs "heard" in the background, possibly after the next wake word:
            # attach it to the request whose transcript it is
            text, outcome = h.groups()
            req = next((r for r in reversed(requests[-6:]) if text in r['transcripts']), None) or open_req
            if req is None:
                req = _new(day, clock)
                requests.append(req)
            req['heard'] = text
            c = CLAUDE.match(outcome)
            if c:
                req['kind'], req['reply_after'], req['reply'] = 'claude', float(c.group(1)), c.group(3)
            else:
                req['kind'], req['reply'] = 'command', outcome
                req['reply_after'] = 0.0
            if outcome == 'conversation ended':
                _finish(req, 'ended')
            elif outcome.endswith('pausing'):
                _finish(req, 'done')
            elif c and SILENT_REPLY.search(req['reply'].strip()):
                _finish(req, 'ignored', 'Claude found no request in it')
            elif FAILED_REPLY.search(req['reply']):
                _finish(req, 'failed')
            else:
                _finish(req, 'done')
            continue
        if (s := STARTED.match(msg)):
            events.append({'day': day, 'time': clock, 'kind': 'start', 'text': f'Zephyr started, listening on {s.group(1)}'})
            continue
        if open_req is None:
            if PROBLEM.search(msg):
                events.append({'day': day, 'time': clock, 'kind': 'problem', 'text': msg})
            continue
        req = open_req
        if (r := RECORDED.match(msg)):
            if req['heard'] is not None or req['status'] != 'listening':
                # she's listening for a follow-up (no name needed): a request of its own
                req = open_req = _new(day, clock)
                req['follow_up'] = True
                requests.append(req)
            req['recorded'], req['ended_by'] = float(r.group(1)), r.group(2)
        elif (v := VOICE.match(msg)):
            req['voice'] = float(v.group(1))
        elif (n := NOT_YOU.match(msg)):
            req['voice'], req['voice_bar'] = float(n.group(1)), float(n.group(2))
            _finish(req, 'rejected', f'not your voice ({n.group(1)} < {n.group(2)})')
        elif (t := TRANSCRIPT.match(msg)):
            req['transcripts'].append(t.group(3))
            req['transcript_after'] = float(t.group(2))
        elif (nn := NO_NAME.match(msg)):
            _finish(req, 'ignored', f'name not in what she heard (spotter {float(nn.group(1)):.2f})')
        elif (tl := TOOL.match(msg)):
            req['tools'].append({'name': tl.group(1), 'input': tl.group(2), 'result': tl.group(3)})
        elif (d := DIAG.match(msg)):
            req['clip'] = d.group(1)
        elif PROBLEM.search(msg):
            events.append({'day': day, 'time': clock, 'kind': 'problem', 'text': msg})
    for req in requests[:-1]:
        if req['status'] == 'listening':
            heard_any = any(t.strip() for t in req['transcripts'])
            if req['follow_up'] and not heard_any:
                _finish(req, 'quiet', 'no follow-up')          # she listened on after answering; nothing said
            else:
                _finish(req, 'missed', 'heard words but did nothing' if heard_any else 'heard nothing after the name')
    # silent follow-ups, and other voices after she answered, are just the room: not requests
    requests = [r for r in requests if r['status'] != 'quiet' and not (r['follow_up'] and r['status'] == 'rejected')]
    return requests, events


def said(req):
    """What you said, as best she heard it."""
    if req['heard']:
        return req['heard']
    return next((t for t in reversed(req['transcripts']) if t.strip()), '')


def response_s(req):
    """Seconds from you stopping talking to her voice starting (None if she never answered)."""
    if req['reply_after'] is None:
        return None
    return round((req['transcript_after'] or 0) + req['reply_after'], 1)


def stats(requests):
    done = [r for r in requests if r['status'] in ('done', 'ended')]
    times = sorted(t for r in done if (t := response_s(r)) is not None and r['kind'] == 'claude')
    counts = {s: sum(r['status'] == s for r in requests) for s in ('done', 'ended', 'failed', 'ignored', 'missed', 'rejected')}
    return {'requests': len(requests), 'counts': counts,
            'success': round(100 * len(done) / len(requests)) if requests else None,
            'median_reply_s': times[len(times) // 2] if times else None,
            'slow_recordings': sum(r['ended_by'] == 'nothing said' for r in requests)}
