"""Zephyr's requests, live in the terminal: what you said, what she did, how long it took, and why
anything was dropped.

    python voice/watch.py            the last 15 requests, then follow new ones
    python voice/watch.py -n 50      the last 50
    python voice/watch.py --raw      the raw log lines instead
"""
import argparse
import os
import sys
import time

import logview

COLOURS = {'done': '32', 'ended': '36', 'failed': '31', 'ignored': '33', 'missed': '35', 'rejected': '90', 'listening': '34'}
MARKS = {'done': 'OK', 'ended': 'END', 'failed': 'FAIL', 'ignored': 'IGN', 'missed': 'MISS', 'rejected': 'NOTYOU', 'listening': '...'}


def paint(text, code):
    return f'\x1b[{code}m{text}\x1b[0m'


def show(req):
    status = req['status']
    head = paint(f"{req['time']}  {MARKS[status]:<6}", COLOURS[status])
    said = logview.said(req) or '(nothing heard)'
    line = f'{head} "{said}"'
    if req['reply']:
        line += paint(f"  ->  {req['reply'][:140]}", '1')
    details = []
    if (t := logview.response_s(req)) is not None:
        details.append(f'answered in {t:.1f} s' + (' (Claude)' if req['kind'] == 'claude' else ''))
    if req['recorded'] is not None:
        details.append(f"recorded {req['recorded']:.1f} s ({req['ended_by']})")
    if req['voice'] is not None:
        details.append(f"voice {req['voice']:.2f}")
    if req['spotter'] is not None:
        details.append(f"name {req['spotter']:.2f}")
    for tool in req['tools']:
        details.append(f"{tool['name']} {tool['input']}")
    if req['why']:
        details.append(req['why'])
    print(line)
    print(paint('            ' + ' | '.join(details), '90'))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-n', type=int, default=15, help='how many past requests to show')
    ap.add_argument('--raw', action='store_true', help='raw log lines instead of requests')
    args = ap.parse_args()
    os.system('')                                   # turn on colours in the Windows console
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    if args.raw:
        lines = logview.read_lines(with_previous=False)
        print('\n'.join(lines[-args.n:]))
        seen = len(lines)
        while True:
            time.sleep(1)
            lines = logview.read_lines(with_previous=False)
            if len(lines) < seen:
                seen = 0                            # the log rotated
            for line in lines[seen:]:
                print(line)
            seen = len(lines)

    requests, _ = logview.parse(logview.read_lines())
    s = logview.stats(requests[-100:])
    print(paint(f"Last 100: {s['success']}% handled, median Claude reply {s['median_reply_s']} s, "
                f"{s['counts']['missed']} missed, {s['counts']['ignored']} ignored, {s['counts']['rejected']} not your voice", '1'))
    for req in requests[-args.n:]:
        show(req)
    shown = {(r['day'], r['time'], logview.said(r)) for r in requests}
    print(paint('-- watching for new requests (Ctrl+C to stop) --', '90'))
    while True:
        time.sleep(1)
        requests, _ = logview.parse(logview.read_lines())
        for req in requests[-10:]:
            key = (req['day'], req['time'], logview.said(req))
            if req['status'] != 'listening' and key not in shown:
                show(req)
                shown.add(key)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
