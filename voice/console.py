"""Zephyr's console in the browser: every request as it comes in (what you said, what she did, how
long it took, why anything was dropped), the stats, the raw log, and the kept recordings of misses.

    python voice/console.py          opens http://127.0.0.1:8765

Only this laptop can open it (it listens on 127.0.0.1, not the network)."""
import json
import os
import re
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import logview

PORT = int(os.environ.get('ZEPHYR_CONSOLE_PORT', '8765'))
DIAG = os.path.join(logview.HERE, 'diag')
PAGE = os.path.join(logview.HERE, 'console.html')


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, kind='application/json', code=200):
        data = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == '/':
            with open(PAGE, encoding='utf-8') as f:
                return self._send(f.read(), 'text/html; charset=utf-8')
        if url.path == '/api/requests':
            lines = logview.read_lines()
            requests, events = logview.parse(lines)
            limit = int(q.get('limit', ['300'])[0])
            for r in requests:
                r['said'], r['response_s'] = logview.said(r), logview.response_s(r)
                r['clip_ok'] = bool(r['clip']) and os.path.exists(os.path.join(DIAG, r['clip']))
            last = next((logview.LINE.match(x) for x in reversed(lines) if logview.LINE.match(x)), None)
            return self._send(json.dumps({
                'requests': requests[-limit:], 'events': events[-50:],
                'stats': logview.stats(requests[-100:]),
                'last_line': f'{last.group(1)} {last.group(2)}' if last else None}))
        if url.path == '/api/log':
            lines = logview.read_lines(with_previous=False)
            needle = q.get('q', [''])[0].lower()
            if needle:
                lines = [x for x in lines if needle in x.lower()]
            return self._send(json.dumps(lines[-int(q.get('lines', ['400'])[0]):]))
        m = re.fullmatch(r'/clip/(\d{6}\.wav)', url.path)
        if m and os.path.exists(os.path.join(DIAG, m.group(1))):
            with open(os.path.join(DIAG, m.group(1)), 'rb') as f:
                return self._send(f.read(), 'audio/wav')
        self._send('not found', 'text/plain', 404)

    def log_message(self, *args):
        pass                                  # keep the terminal quiet


def main():
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    url = f'http://127.0.0.1:{PORT}'
    print(f'Zephyr console on {url}  (Ctrl+C to stop)')
    if '--no-browser' not in sys.argv:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
