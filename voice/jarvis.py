"""Jarvis: "Jarvis, turn on the TV."

Wake word runs locally (openWakeWord "hey_jarvis"); nothing leaves this laptop until it hears "Jarvis" (the "hey_jarvis" model also fires on the name alone).
Then the command is recorded until you stop talking, transcribed by Corelynx, and acted on.

Settings: corelynx.env (key, endpoint, TV address). Pairing for power off / Art Mode: pair-tv.js.
Log: jarvis.log (only commands spoken after "Jarvis" are logged).
"""
import json
import logging
import os
import queue
import re
import socket
import ssl
import sys
import threading
import time
import urllib.request
import io
import wave
import winsound
from logging.handlers import RotatingFileHandler

from collections import deque

import numpy as np
import pyttsx3
import sounddevice as sd
import websocket
from openwakeword.model import Model
from vosk import KaldiRecognizer, Model as VoskModel, SetLogLevel

HERE = os.path.dirname(os.path.abspath(__file__))
RATE = 16000
FRAME = 1280                      # 80 ms, what openWakeWord expects
WAKE_THRESHOLD = 0.5              # confident ("Hey Jarvis"): chime straight away
SOFT_THRESHOLD = 0.15             # possible ("Jarvis" alone scores lower): confirm with the transcript first
PREROLL_FRAMES = 25               # keep ~2 s from before the trigger: "Jarvis, go home" in one breath fits whole
# How Corelynx has spelled "Jarvis" from this mic, including "Harvest" (seen in the log).
NAME_LOOSE = re.compile(r"\b(hey|hi|okay|ok)?\s*(jarvis|jarvis's|javis|jarvas|jervis|charvis|harvis|harvest|garvis|travis)\b", re.I)
MAX_COMMAND_S = 7.0
END_SILENCE_S = 0.8
NO_SPEECH_GIVEUP_S = 3.0
DIAGNOSTIC = '--diag' in sys.argv          # heartbeat lines in the log every 15 s
HISTORY_FRAMES = 50               # ~4 s of recent audio kept for "Jarvis, go home" said in one breath
SPOT_CONFIDENCE = 0.6
MIC_GAIN = 2                      # digital boost for the quiet laptop mic

log = logging.getLogger('jarvis')
log.setLevel(logging.INFO)
_h = RotatingFileHandler(os.path.join(HERE, 'jarvis.log'), maxBytes=512 * 1024, backupCount=1, encoding='utf-8')
_h.setFormatter(logging.Formatter('%(asctime)s  %(message)s', '%Y-%m-%d %H:%M:%S'))
log.addHandler(_h)
if sys.stdout and sys.stdout.isatty():
    log.addHandler(logging.StreamHandler(sys.stdout))


def load_env():
    env = {}
    with open(os.path.join(HERE, 'corelynx.env'), encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
    return env


ENV = load_env()
TV_IP = ENV.get('TV_IP', '192.168.1.97')
TV_MAC = ENV.get('TV_WIFI_MAC', '')
TOKEN_FILE = os.path.join(HERE, 'tv-token.txt')

# Apps Jarvis can open: spoken names -> Smart Hub app id (same ids as the Home launcher).
APPS = {
    'netflix': ('Netflix', '3201907018807'),
    'youtube': ('YouTube', '111299001912'),
    'prime': ('Prime Video', '3201910019365'),
    'amazon': ('Prime Video', '3201910019365'),
    'spotify': ('Spotify', '3201606009684'),
    'apple tv': ('Apple TV', '3201807016597'),
    'gallery': ('Gallery', '3201710015037'),
    'browser': ('Browser', '3202010022079'),
    'internet': ('Browser', '3202010022079'),
    'meet': ('Google Meet', '3202008021439'),
}
HOME_APP = 'FrameHome1.Home'


# ---------------------------------------------------------------- the TV

def tv_info(timeout=2.0):
    try:
        with urllib.request.urlopen(f'http://{TV_IP}:8001/api/v2/', timeout=timeout) as r:
            return json.load(r).get('device', {})
    except Exception:
        return None


def tv_open(app_id):
    req = urllib.request.Request(f'http://{TV_IP}:8001/api/v2/applications/{app_id}', method='POST')
    try:
        urllib.request.urlopen(req, timeout=5).read()
        return True
    except Exception as e:
        log.info(f'open {app_id} failed: {e}')
        return False


def tv_showing(app_id, wait_s):
    """True once the TV reports the app on screen, polling for up to wait_s seconds."""
    end = time.time() + wait_s
    while time.time() < end:
        try:
            with urllib.request.urlopen(f'http://{TV_IP}:8001/api/v2/applications/{app_id}', timeout=2) as r:
                if json.load(r).get('visible'):
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def wake_on_lan():
    mac = bytes.fromhex(TV_MAC.replace(':', '').replace('-', ''))
    packet = b'\xff' * 6 + mac * 16
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    subnet = '.'.join(TV_IP.split('.')[:3]) + '.255'
    for _ in range(3):
        for target in (subnet, '255.255.255.255'):
            for port in (9, 7):
                s.sendto(packet, (target, port))
        time.sleep(0.1)
    s.close()


def _channel(name):
    """Open a paired channel on the TV (remote control or art). Needs tv-token.txt from pairing."""
    if not os.path.exists(TOKEN_FILE):
        raise RuntimeError('not paired')
    token = open(TOKEN_FILE, encoding='utf-8').read().strip()
    url = f'wss://{TV_IP}:8002/api/v2/channels/{name}?name=SmFydmlz&token={token}'   # base64 "Jarvis"
    ws = websocket.create_connection(url, timeout=6, sslopt={'cert_reqs': ssl.CERT_NONE})
    if json.loads(ws.recv()).get('event') != 'ms.channel.connect':
        ws.close()
        raise RuntimeError('TV refused the connection')
    return ws


def tv_key(key, hold_s=0.0):
    """Press a remote button; hold_s > 0 holds it down (a long press)."""
    try:
        ws = _channel('samsung.remote.control')
        def send(cmd):
            ws.send(json.dumps({'method': 'ms.remote.control', 'params': {
                'Cmd': cmd, 'DataOfCmd': key, 'Option': 'false', 'TypeOfRemote': 'SendRemoteKey'}}))
        if hold_s:
            send('Press')
            time.sleep(hold_s)
            send('Release')
        else:
            send('Click')
        time.sleep(0.3)
        ws.close()
        return True
    except Exception as e:
        log.info(f'remote key {key} failed: {e}')
        return False


def art(request, **fields):
    """Ask the Frame's art channel something; returns the TV's answer (a dict) or None."""
    try:
        ws = _channel('com.samsung.art-app')
        rid = str(time.time())
        ws.send(json.dumps({'method': 'ms.channel.emit', 'params': {'event': 'art_app_request', 'to': 'host',
                            'data': json.dumps(dict(request=request, id=rid, **fields))}}))
        end = time.time() + 5
        while time.time() < end:
            m = json.loads(ws.recv())
            if m.get('event') == 'd2d_service_message':
                data = json.loads(m.get('data') or '{}')
                if data.get('id') == rid or data.get('event') in ('artmode_status', 'art_mode_changed'):
                    ws.close()
                    return data
        ws.close()
    except Exception as e:
        log.info(f'art {request} failed: {e}')
    return None


def art_mode_on():
    """True / False when the TV answers, None if it can't be asked (off, or not paired)."""
    a = art('get_artmode_status')
    return None if a is None else a.get('value') == 'on'


def tv_is_off():
    """Off = not answering, or answering as 'standby' (with Power On with Mobile enabled the
    TV keeps its network alive while off, so it still answers)."""
    info = tv_info(1.5)
    return info is None or info.get('PowerState') not in (None, 'on')


def wait_for_tv(timeout_s):
    """Wait until the TV is properly on (answering and not in standby)."""
    end = time.time() + timeout_s
    while time.time() < end:
        info = tv_info(1.5)
        if info and info.get('PowerState') in (None, 'on'):
            return info
        time.sleep(1)
    return None


def wake_tv():
    wake_on_lan()
    if tv_info(1.5) is not None:           # in network standby the remote channel may also listen
        tv_key('KEY_POWER')
    return wait_for_tv(30)


def turn_on():
    """From fully off or from Art Mode, end on the Home screen."""
    if tv_is_off():
        if not wake_tv():
            return "I couldn't wake the TV. Check that it's plugged in and on the Wi-Fi."
        time.sleep(2)
    if art_mode_on():
        art('set_artmode_status', value='off')
        time.sleep(2)
    tv_open(HOME_APP)
    return 'Turning on the TV.' if tv_showing(HOME_APP, 5.0) else "The TV is on, but I couldn't open Home."


def turn_off():
    """Completely off (not Art Mode): on the Frame that's a long press of power."""
    if tv_is_off():
        return 'The TV is already off.'
    if not tv_key('KEY_POWER', hold_s=3.0):
        return "I can't reach the TV's remote control. Try pairing again."
    end = time.time() + 12
    while time.time() < end:
        if tv_is_off():
            return 'Turning off the TV.'
        time.sleep(1)
    log.info(f'after power hold: {tv_info(1.5)}, art mode {art_mode_on()}')
    return 'I pressed power, but the TV still seems to be on.'


def go_art():
    """Art Mode, from TV mode or from fully off."""
    if tv_is_off():
        if not wake_tv():
            return "I couldn't wake the TV."
        time.sleep(2)
    if art_mode_on():
        return 'Art Mode is already on.'
    art('set_artmode_status', value='on')
    time.sleep(2)
    return 'Art Mode.' if art_mode_on() else "I couldn't switch to Art Mode."


def open_app(spoken):
    for word, (title, app_id) in APPS.items():
        if word in spoken:
            if tv_is_off():
                turn_on()
            return f'Opening {title}.' if tv_open(app_id) else f"I couldn't open {title}."
    return None


def go_home():
    if tv_is_off():
        return turn_on()
    return 'Going home.' if tv_open(HOME_APP) else "I couldn't open Home."


# ---------------------------------------------------------------- understanding a command

def parse(text):
    """Work out the command without doing it: returns a function to call, or None."""
    t = text.lower()
    t = re.sub(r"[^\w\s']", ' ', t)
    t = NAME_LOOSE.sub(' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(r'\bt\s+v\b', 'tv', t)     # Corelynx sometimes writes "T V"
    tv = r'(tv|television|telly|screen|frame)'
    # Other devices (the Smart Room lamps etc.) are not the TV: leave those for later.
    if re.search(r'\b(lamps?|lights?|fan|heater|aircon|ac|speakers?|music)\b', t) and not re.search(rf'\b{tv}\b', t):
        return None
    # Art Mode first, so "turn on art mode" isn't read as "turn on the TV"
    if re.search(r'\bart(work)?\b|\bframe\s+mode\b', t):
        return go_art
    if re.search(rf'\b(turn(?:ed|s)?|switch(?:ed|es)?|power(?:ed)?)\s+(the\s+)?{tv}?\s*on\b', t) or re.search(rf'\b(turn(?:ed|s)?|switch(?:ed|es)?|power(?:ed)?)\s+on\b', t) \
            or re.search(rf'\bwake\b', t) or re.search(rf'\b{tv}\s+on\b', t):
        return turn_on
    if re.search(rf'\b(turn(?:ed|s)?|switch(?:ed|es)?|power(?:ed)?)\s+(the\s+)?{tv}?\s*off\b', t) or re.search(rf'\b(turn(?:ed|s)?|switch(?:ed|es)?|power(?:ed)?)\s+off\b', t) \
            or re.search(rf'\b{tv}\s+off\b', t) or re.search(r'\b(shut\s+down|good\s*night)\b', t):
        return turn_off
    if re.search(r'\b(go\s+)?home\b|\bhome\s+screen\b|\bshow\s+(me\s+)?(my\s+)?apps\b', t):
        return go_home
    if any(w in t for w in APPS):
        return lambda: open_app(t)
    return None


def handle(text):
    action = parse(text)
    return action() if action else None


# ---------------------------------------------------------------- speech

class Voice:
    """Windows' own speech engine (SAPI, the one Narrator uses). pyttsx3 went silent after the
    first sentence on this laptop, a known pyttsx3 issue on Windows."""

    def __init__(self):
        import comtypes.client
        self.sapi = comtypes.client.CreateObject('SAPI.SpVoice')
        for v in self.sapi.GetVoices():
            if 'David' in v.GetDescription():
                self.sapi.Voice = v
        self.sapi.Rate = 1
        self.sapi.Volume = 100

    def say(self, text):
        try:
            self.sapi.Speak(text, 0)        # 0 = speak now and wait until done
        except Exception as e:
            log.info(f'speech failed: {e}')


def transcribe(pcm):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    # A named User-Agent: Corelynx's firewall answers 403 to Python's default "Python-urllib/x.y".
    req = urllib.request.Request(ENV['CORELYNX_ENDPOINT'], data=buf.getvalue(), method='POST', headers={
        'Authorization': 'Bearer ' + ENV['CORELYNX_API_KEY'], 'Content-Type': 'audio/wav',
        'User-Agent': 'SmartRoom-Jarvis/1.0', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r).get('text', '').strip()


def _make_chime():
    """A soft two-note chime (B5 then E6) as an in-memory WAV, played at normal system volume."""
    sr, notes, out = 22050, [(988, 0.11), (1319, 0.18)], []
    for freq, dur in notes:
        t = np.arange(int(sr * dur)) / sr
        env = np.minimum(1, t / 0.008) * np.exp(-t * 9)          # quick attack, gentle fade
        out.append(0.45 * env * np.sin(2 * np.pi * freq * t))
    pcm = (np.concatenate(out) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


CHIME_FILE = os.path.join(HERE, 'chime.wav')
with open(CHIME_FILE, 'wb') as _f:          # Windows can only play async from a file, not from memory
    _f.write(_make_chime())


def chime():
    try:
        winsound.PlaySound(CHIME_FILE, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception as e:
        log.info(f'chime failed: {e}')


# ---------------------------------------------------------------- main loop

def main():
    model = Model(wakeword_models=['hey_jarvis'], inference_framework='onnx')
    spotter = NameSpotter()
    voice = Voice()
    frames = queue.Queue()
    # The laptop mic is quiet after its own noise suppression: a modest digital boost, clipped
    # so loud speech can't distort.
    def on_audio(data, n, t, s):
        frames.put(np.clip(data[:, 0].astype(np.int32) * MIC_GAIN, -32768, 32767).astype(np.int16))
    stream = sd.InputStream(samplerate=RATE, channels=1, dtype='int16', blocksize=FRAME, callback=on_audio)
    stream.start()
    log.info(f'Jarvis listening on "{sd.query_devices(sd.default.device[0])["name"]}"')

    log.info(f'replies play on "{sd.query_devices(sd.default.device[1])["name"]}"')
    stream.stop()
    voice.say('Jarvis online.')
    while not frames.empty():
        frames.get_nowait()
    stream.start()
    history = deque(maxlen=HISTORY_FRAMES)   # the last ~4 s of audio
    noise = 300.0                     # running estimate of room noise (RMS)
    beat_at, beat_level, beat_score, beat_frames = time.time() + 15, 0.0, 0.0, 0
    while True:
        f = frames.get()
        rms = float(np.sqrt(np.mean(f.astype(np.float32) ** 2)))
        noise = 0.995 * noise + 0.005 * min(rms, noise * 3)
        history.append(f)
        score = max(model.predict(f).values())
        said_jarvis = spotter.heard(f)    # "Jarvis" on its own, spotted locally by Vosk
        beat_level, beat_score, beat_frames = max(beat_level, rms), max(beat_score, score), beat_frames + 1
        if time.time() >= beat_at and DIAGNOSTIC:
            log.info(f'heartbeat: {beat_frames} frames, loudest {beat_level:.0f}, best wake score {beat_score:.3f}')
            beat_at, beat_level, beat_score, beat_frames = time.time() + 15, 0.0, 0.0, 0
        if score < SOFT_THRESHOLD and not said_jarvis:
            continue
        confident = score >= WAKE_THRESHOLD
        by_name = said_jarvis and not confident
        log.info(f'wake word (score {score:.2f}' + (', "Jarvis" spotted' if said_jarvis else '') +
                 f', {"confident" if confident else "checking transcript"})')

        # ---- woke. Confident ("Hey Jarvis"): chime, then record until the speaker stops.
        #      Spotted by name: Vosk reports it at the first pause, often the comma in "Jarvis, go
        #      home", so keep recording in case the command follows (give up quickly if not).
        #      Possible: record silently, then check the transcript.
        if confident or by_name:            # beep as soon as the name is recognised: your cue it's listening
            chime()
        if by_name:
            command, _ = record_command(frames, list(history), noise, giveup=1.2)
            heard_speech = True
        else:
            command, heard_speech = record_command(frames, list(history)[-PREROLL_FRAMES:], noise)
        stream.stop()                 # don't listen to ourselves while we work and talk
        try:
            audio = np.concatenate(command)
            text = transcribe(audio) if (heard_speech or not confident) else ''
            if DIAGNOSTIC:
                save_diag(audio, text)
            action = parse(text)
            said_name = bool(NAME_LOOSE.search(text))
            if not confident and not action and not said_name:
                # a possible wake-up that turned out to be ordinary speech: stay silent
                log.info('not a command; ignored' + (f' (diag: "{text}")' if DIAGNOSTIC else ''))
                continue
            if not confident and not by_name:
                chime()
            if not action and said_name:
                # only the name was said: the chime is the cue, now listen for the command
                stream.start()
                command, heard_speech = record_command(frames, [], noise)
                stream.stop()
                text = transcribe(np.concatenate(command)) if heard_speech else ''
                action = parse(text)
            reply = action() if action else None
            log.info(f'heard: "{text}" -> {reply or "no matching command"}')
            if reply:
                voice.say(reply)
            elif text:
                voice.say("Sorry, I can turn the TV on or off, go home, or open an app.")
        except Exception as e:
            log.info(f'error: {e}')
            voice.say("Sorry, something went wrong.")
        finally:
            model.reset()
            spotter.reset()
            history.clear()
            while not frames.empty():
                frames.get_nowait()
            stream.start()


class NameSpotter:
    """Spots the single word "Jarvis" locally with Vosk, using a one-word vocabulary.
    The "hey_jarvis" model scored the name alone anywhere from 0.03 to 0.43 in this voice;
    Vosk catches it reliably. It reports the word once the speaker pauses."""

    def __init__(self):
        SetLogLevel(-1)
        self.model = VoskModel(os.path.join(HERE, 'models', 'vosk-model-small-en-us-0.15'))
        self.reset()

    def reset(self):
        self.rec = KaldiRecognizer(self.model, RATE, json.dumps(['jarvis', '[unk]']))
        self.rec.SetWords(True)

    def heard(self, frame):
        if not self.rec.AcceptWaveform(frame.tobytes()):
            return False
        words = json.loads(self.rec.Result()).get('result', [])
        return any(w['word'] == 'jarvis' and w.get('conf', 0) >= SPOT_CONFIDENCE for w in words)


def save_diag(audio, text):
    """Diagnostic mode only: keep the recording and its transcript so a miss can be inspected."""
    d = os.path.join(HERE, 'diag')
    os.makedirs(d, exist_ok=True)
    name = time.strftime('%H%M%S')
    with wave.open(os.path.join(d, name + '.wav'), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(audio.tobytes())
    log.info(f'diag {name}.wav: {len(audio) / RATE:.1f} s, transcript "{text}"')


def record_command(frames, preroll, noise, giveup=NO_SPEECH_GIVEUP_S):
    """Collect audio until the speaker has been quiet for END_SILENCE_S (or limits are hit).
    Time is counted in audio frames (80 ms each), not by the clock."""
    sec = FRAME / RATE
    command = list(preroll)
    heard_speech, quiet = False, 0
    speech_level = max(noise * 3.0, 500.0)
    for n in range(1, int(MAX_COMMAND_S / sec) + 1):
        f = frames.get()
        command.append(f)
        level = float(np.sqrt(np.mean(f.astype(np.float32) ** 2)))
        if level > speech_level:
            heard_speech, quiet = True, 0
        elif heard_speech:
            quiet += 1
            if quiet * sec > END_SILENCE_S:
                break
        elif n * sec > giveup:
            break
    return command, heard_speech


if __name__ == '__main__':
    while True:                     # never stay down: log the problem, wait, start listening again
        try:
            main()
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.info(f'crashed: {type(e).__name__}: {e}; restarting in 5 s')
            time.sleep(5)
