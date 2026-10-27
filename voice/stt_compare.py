"""Side by side: Corelynx vs Deepgram Nova-3 on the recordings Zephyr kept of her misses (voice/diag),
so a switch is decided on Mark's own voice, not on marketing.

    python voice/stt_compare.py            every kept clip
    python voice/stt_compare.py -n 10      the newest 10

Needs DEEPGRAM_API_KEY (python -P voice/keystore.py paste DEEPGRAM_API_KEY). Each clip is a few
seconds, so a full run costs well under a cent."""
import argparse
import glob
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import wave

import numpy as np

import assistant

DEEPGRAM_URL = 'https://api.deepgram.com/v1/listen'
# words Zephyr hears that a general model doesn't know, or gets wrong
KEYTERMS = ['Zephyr', 'TizenTube', 'Moonlight', 'boom bap', 'Spotify', 'YouTube', 'Art Mode', 'forest mode',
            'lofi', 'Frame TV']


def deepgram(wav_bytes, key):
    query = [('model', 'nova-3'), ('smart_format', 'true'), ('language', 'en')] + [('keyterm', k) for k in KEYTERMS]
    req = urllib.request.Request(DEEPGRAM_URL + '?' + urllib.parse.urlencode(query), data=wav_bytes, method='POST',
                                 headers={'Authorization': 'Token ' + key, 'Content-Type': 'audio/wav'})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    return data['results']['channels'][0]['alternatives'][0]['transcript'].strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-n', type=int, default=0, help='only the newest n clips')
    args = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    key = assistant.ENV.get('DEEPGRAM_API_KEY')
    if not key:
        sys.exit('No DEEPGRAM_API_KEY yet: copy it in the Deepgram console, then run\n'
                 '    python -P voice/keystore.py paste DEEPGRAM_API_KEY')
    clips = sorted(glob.glob(os.path.join(assistant.HERE, 'diag', '*.wav')), key=os.path.getmtime)
    clips = clips[-args.n:] if args.n else clips
    rows = []
    for path in clips:
        with open(path, 'rb') as f:
            wav = f.read()
        with wave.open(io.BytesIO(wav)) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16)
        if np.abs(pcm).max() < 200:
            continue                                           # silence: nothing to compare
        t0 = time.time()
        try:
            corelynx = assistant.transcribe_corelynx(pcm)
        except Exception as e:
            corelynx = f'(error: {e})'
        t1 = time.time()
        try:
            deep = deepgram(wav, key)
        except Exception as e:
            deep = f'(error: {e})'
        t2 = time.time()
        rows.append((os.path.basename(path), corelynx, deep, t1 - t0, t2 - t1))
        print(f'\n{os.path.basename(path)}')
        print(f'  Corelynx ({t1 - t0:.1f} s): {corelynx}')
        print(f'  Deepgram ({t2 - t1:.1f} s): {deep}')
    named = lambda text: bool(assistant.NAME_LOOSE.search(text))
    if rows:
        print(f'\n{len(rows)} clips. "Zephyr" heard: Corelynx {sum(named(r[1]) for r in rows)}, '
              f'Deepgram {sum(named(r[2]) for r in rows)}. Average time: Corelynx {np.mean([r[3] for r in rows]):.1f} s, '
              f'Deepgram {np.mean([r[4] for r in rows]):.1f} s (uploads; live streaming is faster for both).')


if __name__ == '__main__':
    main()
