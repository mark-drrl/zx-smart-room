"""Side by side: speech-to-text engines on the recordings Zephyr kept (voice/diag), so a switch is
decided on Mark's own voice, not on marketing.

    python voice/stt_compare.py                                    every kept clip, Corelynx vs Deepgram
    python voice/stt_compare.py -n 10                              the newest 10
    python voice/stt_compare.py --since 23:15 --engines dg-en,dg-multi,dg-tl,scribe
                                                                   Taglish test: four engines, clips since 23:15

Engines: corelynx, dg-en (Deepgram English, what Zephyr uses), dg-multi (Deepgram, switches languages
mid-sentence), dg-tl (Deepgram Tagalog), scribe (ElevenLabs, detects the language). Each clip is a few
seconds, so a run costs cents at most."""
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
CRLF = '\r\n'


def deepgram(wav_bytes, key, language='en'):
    """language: 'en', 'tl' (Tagalog) or 'multi' (switches languages mid-sentence)."""
    query = [('model', 'nova-3'), ('smart_format', 'true'), ('language', language)]
    if language == 'en':
        query += [('keyterm', k) for k in KEYTERMS]        # keyterm prompting is English-only
    req = urllib.request.Request(DEEPGRAM_URL + '?' + urllib.parse.urlencode(query), data=wav_bytes, method='POST',
                                 headers={'Authorization': 'Token ' + key, 'Content-Type': 'audio/wav'})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    return data['results']['channels'][0]['alternatives'][0]['transcript'].strip()


def scribe(wav_bytes, key):
    """ElevenLabs Scribe: detects the language itself (Filipino included)."""
    boundary = '----zephyr' + str(int(time.time() * 1000))
    head = (f'--{boundary}{CRLF}Content-Disposition: form-data; name="model_id"{CRLF}{CRLF}scribe_v1{CRLF}'
            f'--{boundary}{CRLF}Content-Disposition: form-data; name="file"; filename="clip.wav"{CRLF}'
            f'Content-Type: audio/wav{CRLF}{CRLF}')
    body = head.encode() + wav_bytes + f'{CRLF}--{boundary}--{CRLF}'.encode()
    req = urllib.request.Request('https://api.elevenlabs.io/v1/speech-to-text', data=body, method='POST',
                                 headers={'xi-api-key': key, 'Content-Type': f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    return f"{data.get('text', '').strip()}  [{data.get('language_code', '?')}]"


ENGINES = {
    'corelynx': lambda wav, pcm, env: assistant.transcribe_corelynx(pcm),
    'dg-en': lambda wav, pcm, env: deepgram(wav, env['DEEPGRAM_API_KEY'], 'en'),
    'dg-multi': lambda wav, pcm, env: deepgram(wav, env['DEEPGRAM_API_KEY'], 'multi'),
    'dg-tl': lambda wav, pcm, env: deepgram(wav, env['DEEPGRAM_API_KEY'], 'tl'),
    'scribe': lambda wav, pcm, env: scribe(wav, env['ELEVENLABS_API_KEY']),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-n', type=int, default=0, help='only the newest n clips')
    ap.add_argument('--since', help='only clips saved after this time today, e.g. 23:15')
    ap.add_argument('--engines', default='corelynx,dg-en', help=f'comma-separated: {", ".join(ENGINES)}')
    args = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    env = assistant.ENV
    engines = [e.strip() for e in args.engines.split(',')]
    if any(e.startswith('dg') for e in engines) and not env.get('DEEPGRAM_API_KEY'):
        sys.exit('No DEEPGRAM_API_KEY yet: python -P voice/keystore.py paste DEEPGRAM_API_KEY')
    clips = sorted(glob.glob(os.path.join(assistant.HERE, 'diag', '*.wav')), key=os.path.getmtime)
    if args.since:
        h, m = map(int, args.since.split(':'))
        cutoff = time.mktime(time.localtime()[:3] + (h, m, 0, 0, 0, -1))
        clips = [c for c in clips if os.path.getmtime(c) >= cutoff]
    clips = clips[-args.n:] if args.n else clips
    named = {e: 0 for e in engines}
    took = {e: [] for e in engines}
    count = 0
    for path in clips:
        with open(path, 'rb') as f:
            wav = f.read()
        with wave.open(io.BytesIO(wav)) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16)
        if np.abs(pcm).max() < 200:
            continue                                           # silence: nothing to compare
        count += 1
        print(f'\n{os.path.basename(path)}')
        for e in engines:
            t = time.time()
            try:
                text = ENGINES[e](wav, pcm, env)
            except Exception as err:
                text = f'(error: {err})'
            took[e].append(time.time() - t)
            named[e] += bool(assistant.NAME_LOOSE.search(text))
            print(f'  {e:<9} ({took[e][-1]:.1f} s): {text}')
    if count:
        print(f'\n{count} clips. ' + ' | '.join(f'{e}: "Zephyr" {named[e]}x, {np.mean(took[e]):.1f} s' for e in engines))


if __name__ == '__main__':
    main()
