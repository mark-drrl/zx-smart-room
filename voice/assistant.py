"""Voice control for The Frame: "Zephyr, turn on the TV."

The assistant's name is WAKE_NAME in corelynx.env. It is spotted locally (Vosk, one-word
vocabulary); nothing leaves this laptop until the name is heard. Then the command is recorded
until you stop talking, transcribed by Corelynx, and acted on.

Settings: corelynx.env (key, endpoint, name, TV address). Pairing for power off / Art Mode: pair_tv.py.
Log: assistant.log (only commands spoken after the name are logged).
"""
import json
import logging
import os
import queue
import random
import re
import socket
import ssl
import statistics
import sys
import threading
import time
import urllib.request
import io
import wave
from logging.handlers import RotatingFileHandler

from collections import deque

import numpy as np
import sounddevice as sd
import websocket
from openwakeword.model import Model
from vosk import KaldiRecognizer, Model as VoskModel, SetLogLevel

HERE = os.path.dirname(os.path.abspath(__file__))
RATE = 16000
FRAME = 1280                      # 80 ms, what openWakeWord expects
WAKE_THRESHOLD = 0.5              # "Hey Jarvis" model (only used if the name is Jarvis): sure straight away
SOFT_THRESHOLD = 0.15             # "Hey Jarvis" model, lower scores: confirm with the transcript first
PREROLL_FRAMES = 25               # keep ~2 s from before the trigger: "<name>, go home" in one breath fits whole
MAX_COMMAND_S = 20.0              # long directions fit (9 s cut sentences off on 22 Oct)
END_SILENCE_S = 0.6               # pause that ends a sentence
QUICK_END_S = 0.3                 # ...or this, once the live transcript shows a finished sentence
MIN_REQUEST_S = 0.3               # speech needed after the name before a pause can end the turn
LONG_TALK_S = 2.5                 # talking longer than this = giving directions: allow thinking pauses
LONG_PAUSE_S = 1.0                # ...so it takes this much silence to end, and no quick end


class Rhythm:
    """How you talk, learned as you go: the short gaps between your words (not the end of a
    sentence) and how many words a second you say. Fast talkers get quicker replies and a slightly
    quicker voice back; slower, thoughtful speech gets more room. Starts from the fixed values."""

    def __init__(self):
        self.gaps = deque(maxlen=40)      # seconds of silence inside a sentence
        self.rates = deque(maxlen=10)     # words per second of talking

    def gap(self):
        return statistics.median(self.gaps) if len(self.gaps) >= 4 else None

    def end_silence(self, long_talk):
        """Silence that ends your turn: about twice your usual mid-sentence gap."""
        g = self.gap()
        if g is None:
            return LONG_PAUSE_S if long_talk else END_SILENCE_S
        if long_talk:
            return min(1.2, max(0.6, g * 2.4))
        return min(0.8, max(0.45, g * 1.8))

    def note_rate(self, words, talk_s):
        if words >= 3 and talk_s >= 0.8:
            self.rates.append(words / talk_s)

    def voice_speed(self):
        """Her speaking speed: 1.0 for an average talker (~2.5 words/s), up to 1.12 if you're quick."""
        if not self.rates:
            return 1.0
        wps = statistics.median(self.rates)
        return round(min(1.12, max(0.95, 1.0 + (wps - 2.5) * 0.08)), 2)


RHYTHM = Rhythm()
LAST_TALK_S = [0.0]                   # seconds of actual speech in the last recording
NO_SPEECH_GIVEUP_S = 3.0
DIAGNOSTIC = '--diag' in sys.argv          # heartbeat lines in the log every 15 s
HISTORY_FRAMES = 50               # ~4 s of recent audio kept for "<name>, go home" said in one breath
SPOT_CONFIDENCE = 0.6
STRICT_VOICE_MATCH = 0.55           # voice ID bar when the name wasn't said (follow-ups): music scored 0.44
YOUR_VOICE_SURE = 0.65            # voice match this high: the spotter's "Zephyr" is trusted without the transcript
NAME_PAUSE_S = 3.5                 # after the name alone: 2.0 cut off sleepy, quiet commands (27 Oct)
SURE_CONFIDENCE = 0.95              # this sure of the name: play the sound at once (else after checking)
MIC_GAIN = 2                      # digital boost for the quiet laptop mic
SPEAKERPHONE_GAIN = 2.5           # quiet night-time speech from bed, a few metres from the AirHug (27 Oct)


def soft_gain(samples, gain):
    """Boost quiet speech without clipping loud speech: linear while quiet, rounding off smoothly
    towards full scale instead of cutting off."""
    x = samples.astype(np.float32) * (gain / 32768.0)
    return (np.tanh(x) * 32767.0).astype(np.int16)
PHONE = None                      # the phone mic, once main() has started it
SPEAKERPHONE = False              # set in main(): the mic is a speakerphone with echo cancellation
SOUND_UNTIL = 0.0                 # while the wake sound plays, the mic hears it: not your speech
FOLLOW_UP_S = 5.0                 # after a chat reply, keep listening this long for a follow-up

log = logging.getLogger('assistant')
log.setLevel(logging.INFO)
# ZEPHYR_LOG lets tests write somewhere else: test runs once polluted the real log (and the nightly
# review's measurements) with simulated failures.
_h = RotatingFileHandler(os.environ.get('ZEPHYR_LOG') or os.path.join(HERE, 'assistant.log'), maxBytes=512 * 1024,
                         backupCount=1, encoding='utf-8')
_h.setFormatter(logging.Formatter('%(asctime)s  %(message)s', '%Y-%m-%d %H:%M:%S'))
log.addHandler(_h)
if sys.stdout and sys.stdout.isatty():
    log.addHandler(logging.StreamHandler(sys.stdout))


def load_env():
    env = {}
    path = os.path.join(HERE, 'corelynx.env')
    if not os.path.exists(path):                 # tests / CI: no settings file
        path = os.path.join(HERE, 'corelynx.env.example')
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
    try:
        sys.path.insert(0, HERE)
        from keystore import fill            # API keys come from Windows Credential Manager
        fill(env)
    except Exception:
        pass
    return env


ENV = load_env()
TV_IP = ENV.get('TV_IP', '192.168.1.97')
TV_MAC = ENV.get('TV_WIFI_MAC', '')
TOKEN_FILE = os.path.join(HERE, 'tv-token.txt')

# The assistant's name, and spellings Corelynx has produced for it. The name only has to be
# spotted locally; these just let the transcript's copy of the name be stripped before parsing.
NAME = ENV.get('WAKE_NAME', 'Zephyr').strip() or 'Zephyr'
NAME_SPELLINGS = {
    'zephyr': ['zephyr', 'zephyrs', 'zefir', 'zephir', 'zafir', 'zephire', 'sephir', 'zeffer', 'zephyra',
               'zephy', 'zeffir', 'zafer', 'zefer', 'zapper', 'zephyrt', 'jeffrey', 'suffer', 'sefer', 'sapphire',
               'nefer', 'zappa', 'zepher'],     # how Corelynx has misheard it
    'jarvis': ['jarvis', "jarvis's", 'javis', 'jarvas', 'jervis', 'charvis', 'harvis', 'harvest', 'garvis', 'travis'],
}.get(NAME.lower(), [NAME.lower()])
NAME_LOOSE = re.compile(r"\b(hey|hi|okay|ok)?\s*(" + '|'.join(map(re.escape, NAME_SPELLINGS)) + r")\b", re.I)
# Mishearings that are everyday words ("Let her go home", "With effort, go home", "Is that fair? Good
# night", 27 Oct): they only count as the name at the very start, where the local spotter heard it.
NAME_AT_START = re.compile(r"^\W*(hey\s+|hi\s+|okay\s+|ok\s+)?(" + '|'.join({
    'zephyr': [r'is\s+that\s+(fair|for)', r'with\s+effort', r'let\s+her', r'just\s+for',
               r'(is\s+that|zip)(?=[\s,.!?]*$)'],      # the name alone, cut short: "Is that", "Zip."
}.get(NAME.lower(), [r'(?!x)x'])) + r")\b[\s,.!?]*", re.I)
USE_HEY_JARVIS = NAME.lower() == 'jarvis'     # the openWakeWord model only knows "Hey Jarvis"

# Apps the assistant can open: spoken names -> Smart Hub app id (same ids as the Home launcher).
APPS = {
    'netflix': ('Netflix', '3201907018807'),
    'youtube': ('YouTube', 'xvvl3S1TT1.TizenTubeStandalone'),   # TizenTube: YouTube without ads
    'prime': ('Prime Video', '3201910019365'),
    'amazon': ('Prime Video', '3201910019365'),
    'spotify': ('Spotify', '3201606009684'),
    'apple tv': ('Apple TV', '3201807016597'),
    'gallery': ('Gallery', '3201710015037'),
    'browser': ('Browser', '3202010022079'),
    'internet': ('Browser', '3202010022079'),
    'meet': ('Google Meet', '3202008021439'),
    'moonlight': ('Moonlight', 'MoonLightS.MoonlightWasm'),   # the laptop's screen, streamed by Sunshine
}
HOME_APP = 'FrameHome1.Home'


# ---------------------------------------------------------------- the TV

def tv_info(timeout=2.0):
    try:
        with urllib.request.urlopen(f'http://{TV_IP}:8001/api/v2/', timeout=timeout) as r:
            return json.load(r).get('device', {})
    except Exception:
        return None


BUSY_FILE = os.path.join(os.path.dirname(HERE), '.zephyr-busy')


def busy(seconds):
    """Tell the Home keeper (home-keeper.ps1) to leave the TV alone for a while: Zephyr is switching
    apps. Without it, the keeper sent the TV Home in the middle of Spotify -> YouTube (27 Oct)."""
    try:
        until = max(time.time() + seconds, float(open(BUSY_FILE).read() or 0)) if os.path.exists(BUSY_FILE) else time.time() + seconds
        with open(BUSY_FILE, 'w') as f:
            f.write(f'{until:.0f}')
    except (OSError, ValueError):
        pass


def tv_close(app_id):
    req = urllib.request.Request(f'http://{TV_IP}:8001/api/v2/applications/{app_id}', method='DELETE')
    try:
        urllib.request.urlopen(req, timeout=5).read()
        return True
    except Exception as e:
        log.info(f'close {app_id} failed: {e}')
        return False


def tv_open(app_id):
    busy(20)
    if app_id != MOONLIGHT_APP and app_state(MOONLIGHT_APP)['running']:
        tv_close(MOONLIGHT_APP)      # leaving the laptop stream ends it, so Sunshine turns the 3rd screen off
    req = urllib.request.Request(f'http://{TV_IP}:8001/api/v2/applications/{app_id}', method='POST')
    try:
        urllib.request.urlopen(req, timeout=5).read()
        return True
    except Exception as e:
        log.info(f'open {app_id} failed: {e}')
        return False


def app_state(app_id):
    """What the TV says about an app: {'running': bool, 'visible': bool} (both False if it won't say)."""
    try:
        with urllib.request.urlopen(f'http://{TV_IP}:8001/api/v2/applications/{app_id}', timeout=2) as r:
            data = json.load(r)
        return {'running': bool(data.get('running')), 'visible': bool(data.get('visible'))}
    except Exception:
        return {'running': False, 'visible': False}


def youtube_on_screen():
    """TizenTube shows as "visible" while browsing, but while a video plays the TV only reports it as
    running (seen 27 Oct). So: visible, or running while Home and every other known app are hidden."""
    state = app_state(YOUTUBE_APP)
    if state['visible']:
        return True
    if not state['running']:
        return False
    others = [HOME_APP] + [app_id for _, app_id in APPS.values() if app_id != YOUTUBE_APP]
    return not any(app_state(a)['visible'] for a in dict.fromkeys(others))


def tv_showing(app_id, wait_s):
    """True once the TV reports the app on screen, polling for up to wait_s seconds."""
    end = time.time() + wait_s
    while True:                                   # always check at least once, sleep only between checks
        try:
            with urllib.request.urlopen(f'http://{TV_IP}:8001/api/v2/applications/{app_id}', timeout=2) as r:
                if json.load(r).get('visible'):
                    return True
        except Exception:
            pass
        if time.time() + 0.5 > end:
            return False
        time.sleep(0.5)


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


class Remote:
    """One remote-control connection, kept open between commands: opening it takes ~1.2 s
    (measured), sending a button on an open one is instant. Reopened if it dropped or sat idle
    for a minute."""

    def __init__(self):
        self.ws, self.used, self.lock = None, 0.0, threading.Lock()

    def _send(self, cmd, key):
        self.ws.send(json.dumps({'method': 'ms.remote.control', 'params': {
            'Cmd': cmd, 'DataOfCmd': key, 'Option': 'false', 'TypeOfRemote': 'SendRemoteKey'}}))

    def press(self, keys, hold_s=0.0, gap_s=0.35):
        with self.lock:
            for attempt in range(2):
                try:
                    if self.ws is None or not self.ws.connected or time.time() - self.used > 60:
                        if self.ws:
                            try:
                                self.ws.close()
                            except Exception:
                                pass
                        self.ws = _channel('samsung.remote.control')
                    for i, k in enumerate(keys):
                        if hold_s:
                            self._send('Press', k)
                            time.sleep(hold_s)
                            self._send('Release', k)
                        else:
                            self._send('Click', k)
                        if i < len(keys) - 1:
                            time.sleep(gap_s)
                    self.used = time.time()
                    return True
                except Exception as e:
                    self.ws = None                 # stale connection: open a fresh one and retry once
                    if attempt:
                        log.info(f'remote {keys} failed: {e}')
            return False


REMOTE = Remote()


def tv_key(key, hold_s=0.0):
    """Press a remote button; hold_s > 0 holds it down (a long press)."""
    return REMOTE.press([key], hold_s)


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
    """Wake-on-LAN normally brings the TV fully on by itself (Power On with Mobile). Only if it
    sits in standby for a while is the power button pressed, and only after re-checking: pressing
    it while the TV is already starting up switched it straight back off (seen 22 Oct)."""
    wake_on_lan()
    end, standby_since, pressed = time.time() + 45, None, False
    while time.time() < end:
        info = tv_info(1.5)
        state = info.get('PowerState') if info else None
        if info and state in (None, 'on'):
            return info
        if state == 'standby':
            standby_since = standby_since or time.time()
            if not pressed and time.time() - standby_since > 8 and tv_info(1.5).get('PowerState') == 'standby':
                tv_key('KEY_POWER')
                pressed = True
        else:
            wake_on_lan()                  # still unreachable: nudge again
        time.sleep(1)
    return None


def turn_on():
    busy(60)
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


ART_SLEEP_MIN = '5'                     # Art Mode screen goes dark after this many minutes without motion


def turn_off():
    """"Off" = Art Mode with the screen sleeping after 5 minutes without motion (the Frame's own
    motion sensor). Turning back on then takes seconds; a full power-off takes ~45 s to wake
    from (measured 22 Oct), because the TV's Wi-Fi goes into deep sleep."""
    if tv_is_off():
        return 'The TV is already off.'
    timer = art('get_motion_timer') or {}
    if timer.get('value') != ART_SLEEP_MIN:
        art('set_motion_timer', value=ART_SLEEP_MIN)
    if not art_mode_on():
        art('set_artmode_status', value='on')
        time.sleep(2)
    if art_mode_on():
        return 'Off. The screen sleeps after five minutes.'
    log.info('Art Mode did not come on; powering off fully instead')
    return turn_off_fully()


def turn_off_fully():
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
    t = NAME_LOOSE.sub(' ', NAME_AT_START.sub(' ', t))
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(r'\bt\s+v\b', 'tv', t)     # Corelynx sometimes writes "T V"
    if len(t.split()) <= 6:                # a scene said on its own, not "good morning, how are you today"
        for pattern, scene in SCENES:
            if pattern.search(t):
                return scene
    # Only short, single commands are handled here instantly; anything more ("open Spotify and
    # play a song", "volume to eight") goes to Claude, which has the full remote.
    if len(t.split()) > 6 or re.search(r'\b(and|then|after|play|song|music|volume|louder|quieter|mute|'
                                       r'search|find|watch|episode|movie|pause|resume|channel|input|source|'
                                       r'video|videos|need|want|something|anything|that|which)\b', t):
        return None
    tv = r'(tv|television|telly|screen|frame)'
    # Other devices (the Smart Room lamps etc.) are not the TV: leave those for later.
    if re.search(r'\b(lamps?|lights?|fan|heater|aircon|ac|speakers?|music)\b', t) and not re.search(rf'\b{tv}\b', t):
        return None
    # Art Mode first, so "turn on art mode" isn't read as "turn on the TV"
    if re.search(r'\bart(work)?\b|\bframe\s+mode\b', t):
        return go_art
    if re.search(rf'\b(turn(?:ed|s)?|switch(?:ed|es)?|power(?:ed)?)\s+(the\s+)?{tv}?\s*on\b', t) or re.search(r'\b(turn(?:ed|s)?|switch(?:ed|es)?|power(?:ed)?)\s+on\b', t) \
            or re.search(r'\bwake\b', t) or re.search(rf'\b{tv}\s+on\b', t):
        return turn_on
    if re.search(rf'\b(turn(?:ed|s)?|switch(?:ed|es)?|power(?:ed)?)\s+(the\s+)?{tv}?\s*off\b', t) or re.search(r'\b(turn(?:ed|s)?|switch(?:ed|es)?|power(?:ed)?)\s+off\b', t) \
            or re.search(rf'\b{tv}\s+off\b', t) or re.search(r'\b(shut\s+down|good\s*night)\b', t):
        if re.search(r'\b(completely|fully|all the way|totally|properly|shut\s+down|power\s+off)\b', t):
            return turn_off_fully
        return turn_off
    if re.search(r'\b(go\s+)?home\b|\bhome\s+screen\b|\bshow\s+(me\s+)?(my\s+)?apps\b', t):
        return go_home
    if any(w in t for w in APPS):
        go = lambda: open_app(t)
        go.opens_app = True
        return go
    return None


def quick_ack(action, text):
    """What she says the moment she understands a TV command, before it's done."""
    if action in SCENE_ACKS:
        return SCENE_ACKS[action]
    if action is turn_on:
        return 'Turning on the TV.'
    if action is turn_off:
        return 'Turning it off.'
    if action is turn_off_fully:
        return 'Powering it off completely.'
    if action is go_home:
        return 'Going home.'
    if action is go_art:
        return 'Art Mode.'
    for word, (title, _) in APPS.items():
        if word in text.lower():
            return f'Opening {title}.'
    return 'On it.'


def failed(reply):
    return bool(reply) and bool(re.search(r"couldn'?t|can'?t|isn'?t|didn'?t|no app|not paired|still on|nothing found", reply, re.I))


def in_background_if_off(job, waking_msg):
    """For Claude's tools: if the TV is off, start it waking and answer at once instead of making
    Claude (and you) wait up to 40 s for the result."""
    if not tv_is_off():
        return job()
    threading.Thread(target=lambda: log.info(f'background: {job()}'), daemon=True).start()
    return waking_msg


def without_name(text):
    """The words after the assistant's name, e.g. "Zephyr, what's on?" -> "what's on?"."""
    return NAME_LOOSE.sub(' ', NAME_AT_START.sub(' ', text)).strip(' ,.!?')


# ---------------------------------------------------------------- full remote control (for Claude)

UPNP = f'http://{TV_IP}:9197/upnp/control/RenderingControl1'


def _upnp(action, args=''):
    """The TV's standard UPnP volume service: exact levels, unlike the remote's +/- buttons."""
    body = ('<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1"><InstanceID>0</InstanceID>'
            f'<Channel>Master</Channel>{args}</u:{action}></s:Body></s:Envelope>')
    req = urllib.request.Request(UPNP, data=body.encode(), method='POST', headers={
        'Content-Type': 'text/xml; charset="utf-8"',
        'SOAPAction': f'"urn:schemas-upnp-org:service:RenderingControl:1#{action}"'})
    with urllib.request.urlopen(req, timeout=1.5) as r:
        return r.read().decode('utf-8', 'replace')


def get_volume():
    m = re.search(r'<CurrentVolume>(\d+)</CurrentVolume>', _upnp('GetVolume'))
    return int(m.group(1)) if m else None


MAX_VOLUME = 60                  # a misheard "250" can never blast the room


def set_volume(level=None, change=None):
    """Exact volume (0-100) or a relative change."""
    if tv_is_off():
        return 'The TV is off.'
    now = get_volume()
    level = max(0, min(MAX_VOLUME, int(level if level is not None else (now or 0) + int(change or 0))))
    _upnp('SetVolume', f'<DesiredVolume>{level}</DesiredVolume>')
    return f"Volume's at {level}."


def set_mute(on):
    if tv_is_off():
        return 'The TV is off.'
    _upnp('SetMute', f'<DesiredMute>{1 if on else 0}</DesiredMute>')
    return 'Muted.' if on else 'Unmuted.'


class Ducker:
    """Mutes the TV while you're talking to Zephyr, then unmutes - like Alexa or Google Home do.
    Music or dialogue in the room is what made the transcripts go wrong ("volume 250"). Uses the
    TV's UPnP mute (~0.05 s); if the TV is off or doesn't answer within half a second, it does
    nothing. It never unmutes a TV that was already muted."""

    def __init__(self):
        self.ducked = False

    def duck(self):
        if self.ducked:
            return
        try:
            was_muted = '<CurrentMute>1</CurrentMute>' in _upnp('GetMute')
            if not was_muted:
                _upnp('SetMute', '<DesiredMute>1</DesiredMute>')
                self.ducked = True
        except Exception:
            pass

    def restore(self):
        if not self.ducked:
            return
        self.ducked = False
        try:
            _upnp('SetMute', '<DesiredMute>0</DesiredMute>')
        except Exception as e:
            log.info(f'unmute after listening failed: {e}')


DUCK = Ducker()


REMOTE_KEYS = ['KEY_UP', 'KEY_DOWN', 'KEY_LEFT', 'KEY_RIGHT', 'KEY_ENTER', 'KEY_RETURN', 'KEY_HOME', 'KEY_MENU',
               'KEY_PLAY', 'KEY_PAUSE', 'KEY_STOP', 'KEY_FF', 'KEY_REWIND', 'KEY_VOLUP', 'KEY_VOLDOWN',
               'KEY_MUTE', 'KEY_CHUP', 'KEY_CHDOWN', 'KEY_SOURCE', 'KEY_INFO', 'KEY_GUIDE', 'KEY_CAPTION',
               'KEY_EXIT'] + [f'KEY_{n}' for n in range(10)]


def press_keys(keys):
    """Press remote buttons in order over one connection (navigation, play/pause, channels...)."""
    if tv_is_off():
        return 'The TV is off.'
    if not REMOTE.press(keys):
        return "Couldn't reach the TV's remote control."
    return f'Pressed {", ".join(keys)}.'


def youtube_find(query, pick_random=False):
    """Top YouTube result for a search, via yt-dlp (no API key needed; ~2.3 s measured).
    Skips Shorts so "play X" gets the real video. pick_random: a random long one (20+ min) from the
    top 10 instead, so a scene feels different each time."""
    import yt_dlp
    with yt_dlp.YoutubeDL({'quiet': True, 'extract_flat': True, 'skip_download': True, 'noprogress': True}) as y:
        found = y.extract_info(f'ytsearch{10 if pick_random else 5}:{query}', download=False).get('entries') or []
    videos = [e for e in found if e.get('id') and '/shorts/' not in (e.get('url') or '')]
    if pick_random and videos:
        return random.choice([e for e in videos if (e.get('duration') or 0) >= 1200] or videos)
    return videos[0] if videos else (found[0] if found else None)


YOUTUBE_APP = 'xvvl3S1TT1.TizenTubeStandalone'     # TizenTube (YouTube without ads)
_youtube = None


def youtube():
    """The kept-open connection to YouTube on the TV (youtube_tv.py), or None if not linked."""
    global _youtube
    import youtube_tv
    if _youtube is None and youtube_tv.linked():
        _youtube = youtube_tv.YouTubeRemote()
    return _youtube


def short_title(title):
    """What she says out loud: "4K Campfire by the River - Relaxing Fireplace & Nature Sounds |
    UHD 2160p" -> "Campfire by the River"."""
    t = re.split(r'\s+[-|–—:]\s+|\s*[|(\[]', title or '')[0]
    t = re.sub(r'\b(4K|8K|HDR|UHD|HD|2160p|1080p|60fps|official( music)? video|lyrics?)\b', '', t, flags=re.I)
    t = re.sub(r"[^\w\s&'.,!?/-]", '', t)                 # emojis and symbols aren't spoken
    t = ' '.join(t.split()).strip(' -|,.')
    return t if len(t) >= 3 else (title or '')[:60]


def youtube_play(query, pick_random=False):
    """Search YouTube and play the top result in TizenTube. The search runs while TizenTube opens
    and the connection is made; the video is sent as soon as YouTube answers, not after a fixed
    wait. Measured on an open connection: search ~1.6 s + send ~1.7 s."""
    from concurrent.futures import ThreadPoolExecutor
    busy(40)
    woke_tv = False
    if tv_is_off():
        woke = turn_on()
        if failed(woke):
            return woke
        woke_tv = True
    yt = youtube()
    with ThreadPoolExecutor(1) as pool:
        search = pool.submit(youtube_find, query, pick_random)
        opening = not youtube_on_screen()
        if opening:
            tv_open(YOUTUBE_APP)
            tv_showing(YOUTUBE_APP, 8)                 # on screen first: a video sent while it's still
        video = search.result()                        # coming back from the background gets lost
    if not video:
        return f'Nothing found on YouTube for "{query}".'
    if yt is None:
        return "YouTube is open, but I can't pick videos until it's linked with a TV code."
    try:
        # brought to the front or started cold: confirm it switched (a cold start after waking the TV
        # needed longer than 3 tries on 26 Oct)
        yt.play(video['id'], confirm=opening or woke_tv, attempts=7 if woke_tv else 4)
    except Exception as e:
        log.info(f'YouTube link failed: {e}')
        return "YouTube is open, but it didn't take the video. Try again in a moment."
    return f'Playing {short_title(video.get("title") or query)}.'


def youtube_control(action):
    yt = youtube()
    if yt is None:
        return "YouTube isn't linked yet."
    try:
        yt.control(action)
    except Exception as e:
        log.info(f'YouTube control failed: {e}')
        return "YouTube didn't respond."
    return {'pause': 'Paused.', 'resume': 'Playing.', 'next': 'Next video.', 'previous': 'Went back.',
            'skip_ad': 'Skipped.'}.get(action, 'Done.')


# ---------------------------------------------------------------- scenes: one phrase, several things

MOONLIGHT_APP = 'MoonLightS.MoonlightWasm'
SUNSHINE_INFO = 'http://localhost:47989/serverinfo'      # Sunshine, the laptop's streaming server
FOREST_SEARCHES = ['cozy campfire in the forest ambience', 'crackling campfire forest night sounds',
                   'campfire in the woods relaxing ambience', 'forest campfire crickets night ambience',
                   'cozy camp fire by a forest lake ambience']
MORNING_SEARCHES = ['boom bap jazzy hip hop mix', 'jazzy boom bap instrumental beats', 'jazz hop boom bap mix',
                    'chill boom bap jazz rap instrumentals']


SCREEN_OFF_AFTER_S = 5
SCREEN_TIMEOUT_NOTE = os.path.join(os.path.dirname(HERE), '.zephyr-screen-timeout')   # the timeout to put back


def _screen_timeout(seconds=None):
    """Read (no argument) or set Windows' plugged-in "turn off my screen after" time, in seconds (0 = never)."""
    import subprocess
    run = lambda *args: subprocess.run(['powercfg', *args], capture_output=True, text=True, timeout=10,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
    if seconds is None:
        m = re.search(r'Current AC Power Setting Index: 0x([0-9a-f]+)', run('/q', 'SCHEME_CURRENT', 'SUB_VIDEO', 'VIDEOIDLE'), re.I)
        return int(m.group(1), 16) if m else 0
    run('/setacvalueindex', 'SCHEME_CURRENT', 'SUB_VIDEO', 'VIDEOIDLE', str(int(seconds)))
    run('/setactive', 'SCHEME_CURRENT')


def restore_screen_timeout():
    """Put back the screen timeout good night shortened (also at start-up, in case Zephyr stopped in between)."""
    try:
        original = int(open(SCREEN_TIMEOUT_NOTE).read())
    except (OSError, ValueError):
        return
    _screen_timeout(original)
    os.remove(SCREEN_TIMEOUT_NOTE)


def stay_awake():
    """Screens off = Modern Standby on this laptop, which freezes ordinary apps: after "good night"
    Zephyr went deaf and the TV never got "off" (27 Oct). While plugged in, ask Windows to keep running
    with the screens dark, the way a music player does; on battery, let it sleep as usual. Windows ties
    this request to the thread that makes it, so this thread lives as long as Zephyr."""
    import ctypes

    class PowerStatus(ctypes.Structure):
        _fields_ = [('ac', ctypes.c_ubyte), ('battery', ctypes.c_ubyte), ('percent', ctypes.c_ubyte),
                    ('saver', ctypes.c_ubyte), ('life', ctypes.c_ulong), ('full_life', ctypes.c_ulong)]
    status = PowerStatus()
    while True:
        ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status))
        plugged_in = status.ac == 1
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if plugged_in else 0))   # ES_CONTINUOUS (+ ES_SYSTEM_REQUIRED)
        time.sleep(60)


def laptop_screens(on):
    """The laptop screen and the UltraGear.
    Off: Windows' own screen timeout, shortened to 5 s for a moment and then put back, so the screens go
    dark the normal way and stay dark until a mouse move or key. Switching them off directly
    (SC_MONITORPOWER) counts as the sleep button on this laptop: it went into Modern Standby, which froze
    Zephyr mid-command (the TV never got "off") and left her deaf to "good morning" (27 Oct).
    On: a one-pixel mouse nudge."""
    if on:
        restore_screen_timeout()
        import ctypes
        ctypes.windll.user32.mouse_event(0x0001, 1, 0, 0, 0)    # MOUSEEVENTF_MOVE, there and back
        ctypes.windll.user32.mouse_event(0x0001, -1, 0, 0, 0)
        return
    if not os.path.exists(SCREEN_TIMEOUT_NOTE):
        with open(SCREEN_TIMEOUT_NOTE, 'w') as f:
            f.write(str(_screen_timeout()))
    _screen_timeout(SCREEN_OFF_AFTER_S)
    threading.Timer(SCREEN_OFF_AFTER_S + 15, restore_screen_timeout).start()


def sunshine_streaming():
    """True while Moonlight streams the laptop, False if not, None if Sunshine isn't running."""
    try:
        with urllib.request.urlopen(SUNSHINE_INFO, timeout=2) as r:
            return '<currentgame>0<' not in r.read().decode('utf-8', 'replace')
    except Exception:
        return None


def forest_mode():
    reply = youtube_play(random.choice(FOREST_SEARCHES), pick_random=True)
    if not failed(reply):
        set_volume(15)
    return reply


def good_morning():
    laptop_screens(True)
    return youtube_play(random.choice(MORNING_SEARCHES), pick_random=True)


def good_night():
    """Everything dark: the TV fully off (Art Mode would light up again whenever its motion sensor saw
    someone in the night), then the laptop screens."""
    reply = turn_off_fully()
    laptop_screens(False)
    return reply


def mirror_laptop():
    """The laptop on the TV: Moonlight, streaming this laptop's Desktop from Sunshine. The TV ignores
    launch data sent from outside (deep links, tried 27 Oct), so this presses the buttons instead: on
    Moonlight's start screen Add host comes first and the laptop is to its right. ~23 s measured."""
    busy(45)
    streaming = sunshine_streaming()
    if streaming is None:
        return "Sunshine isn't running on the laptop, so there's nothing to stream."
    if tv_is_off():
        woke = turn_on()
        if failed(woke):
            return woke
    if streaming:
        tv_open(MOONLIGHT_APP)
        return 'Your laptop is on the TV.'
    if app_state(MOONLIGHT_APP)['running']:      # start it fresh, so it begins on its host list
        tv_close(MOONLIGHT_APP)
        time.sleep(2)
    tv_open(MOONLIGHT_APP)
    if not tv_showing(MOONLIGHT_APP, 15):
        return "I couldn't open Moonlight."
    time.sleep(5)                                # it finds the laptop on the network
    REMOTE.press(['KEY_RIGHT', 'KEY_ENTER'])     # the laptop...
    time.sleep(4)
    REMOTE.press(['KEY_ENTER'])                  # ...and its Desktop
    end = time.time() + 20
    while time.time() < end:
        if sunshine_streaming():
            return 'Your laptop is on the TV.'
        time.sleep(1)
    return "Moonlight is open, but the stream didn't start."


SCENES = [
    (re.compile(r'\b(forest|campfire|camp fire)\s+mode\b'), forest_mode),
    (re.compile(r'\bgood\s*morning\b'), good_morning),
    (re.compile(r'\bgood\s*night\b'), good_night),
    (re.compile(r'\b(mirror|cast|share|stream)\s+(my\s+|the\s+)?(laptop|computer|pc|desktop|screen)\b|'
                r'\b(put|show|open)\s+(my\s+|the\s+)?(laptop|computer|pc|desktop)\b'), mirror_laptop),
]
SCENE_BY_NAME = {'forest': forest_mode, 'morning': good_morning, 'night': good_night, 'mirror': mirror_laptop}
SCENE_ACKS = {forest_mode: 'Forest mode. Lighting the fire.', good_morning: 'Good morning!',
              good_night: 'Good night.', mirror_laptop: 'Putting your laptop on the TV.'}


SPOTIFY_APP = '3201606009684'
_spotify = None


def spotify():
    global _spotify
    if _spotify is None:
        from spotify import Spotify
        _spotify = Spotify()
    return _spotify


def spotify_play(query, kind):
    """Search Spotify and play the best match on the TV's Spotify app (Spotify Connect)."""
    busy(40)
    sp = spotify()
    if not sp.ready:
        return 'Spotify is not connected yet.'
    uri, what = sp.search(query, kind)
    if not uri:
        return f'Nothing found on Spotify for "{query}".'
    if tv_is_off():
        woke = turn_on()
        if failed(woke):
            return woke
    device = sp.tv_device('Frame')
    if not device:                               # the TV only shows up while its Spotify app is open
        tv_open(SPOTIFY_APP)
        device = sp.tv_device('Frame', wait_s=15)
    if not device:
        return "Spotify is open on the TV, but it didn't show up as a speaker. Try again in a moment."
    sp.play(uri, device['id'])
    return f'Playing {what} on Spotify.'


def spotify_control(action):
    sp = spotify()
    if not sp.ready:
        return 'Spotify is not connected yet.'
    if action == 'now_playing':
        return sp.now_playing()
    sp.control(action)
    return {'pause': 'Paused.', 'resume': 'Playing.', 'next': 'Skipped.', 'previous': 'Went back.',
            'shuffle_on': 'Shuffle on.', 'shuffle_off': 'Shuffle off.'}.get(action, 'Done.')


# ---- SmartThings: the TV's official cloud control, for settings the local APIs don't reach.
# Signed in once through Samsung's own SmartThings CLI (C:\dev\tools\smartthings), which keeps the
# sign-in refreshed. ~1.6 s per command (measured).
ST_CLI = r'C:\dev\tools\smartthings\smartthings.exe'
ST_TV = ENV.get('SMARTTHINGS_TV_ID', '168e8f98-0b72-9140-2335-b86051be5fd0')
PICTURE_MODES = ['Dynamic', 'FILMMAKER MODE', 'Movie', 'Natural', 'Standard']
SOUND_MODES = ['Standard', 'Amplify']


def smartthings(command):
    import subprocess
    out = subprocess.run([ST_CLI, 'devices:commands', ST_TV, command], capture_output=True, text=True,
                         timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    ok = out.returncode == 0 and 'success' in (out.stdout + out.stderr).lower()
    if not ok:
        log.info(f'SmartThings "{command}" failed: {(out.stdout + out.stderr).strip()[:200]}')
    return ok


def tv_setting(setting, value):
    if tv_is_off():
        return 'The TV is off.'
    v = value.strip()
    if setting == 'picture_mode':
        match = next((m for m in PICTURE_MODES if v.lower() in m.lower() or m.lower() in v.lower()), None)
        if not match:
            return f'Picture modes are: {", ".join(PICTURE_MODES)}.'
        return f"{match} picture's on." if smartthings(f'custom.picturemode:setPictureMode("{match}")') else "Couldn't change the picture mode."
    if setting == 'sound_mode':
        match = next((m for m in SOUND_MODES if v.lower() in m.lower()), None)
        if not match:
            return f'Sound modes are: {", ".join(SOUND_MODES)}.'
        return f"{match} sound's on." if smartthings(f'custom.soundmode:setSoundMode("{match}")') else "Couldn't change the sound mode."
    if setting == 'channel':
        if v.lower() in ('up', 'next'):
            cmd = 'tvChannel:channelUp'
        elif v.lower() in ('down', 'previous', 'back'):
            cmd = 'tvChannel:channelDown'
        elif v.isdigit():
            cmd = f'tvChannel:setTvChannel("{v}")'
        else:
            return 'Say a channel number, or up or down.'
        return 'Channel changed.' if smartthings(cmd) else "Couldn't change the channel."
    if setting == 'input':
        source = {'tv': 'dtv', 'antenna': 'dtv'}.get(v.lower(), v.upper().replace(' ', ''))
        return f'Switched to {v}.' if smartthings(f'samsungvd.mediaInputSource:setInputSource("{source}")') else f"Couldn't switch to {v}."
    return 'Unknown setting.'


def tv_status():
    """What the TV is doing right now."""
    if tv_is_off():
        return 'The TV is off.'
    parts = ['The TV is on.']
    try:
        parts.append('Art Mode is ' + ('on.' if art_mode_on() else 'off.'))
    except Exception:
        pass
    try:
        from concurrent.futures import ThreadPoolExecutor
        apps = list(dict(APPS.values()).items())          # unique (title, id) pairs, asked all at once
        with ThreadPoolExecutor(len(apps)) as pool:
            shown = list(pool.map(lambda a: tv_showing(a[1], 0), apps))
        on_screen = [title for (title, _), yes in zip(apps, shown, strict=True) if yes]
        parts.append(f'{on_screen[0]} is on screen.' if on_screen else 'No known app is on screen.')
    except Exception:
        pass
    try:
        parts.append(f'Volume is {get_volume()}.')
    except Exception:
        pass
    return ' '.join(parts)


# "Stop listening for now": go quiet until called back with "<name>, wake up" (or similar).
PAUSE = re.compile(r"\b(stop|quit|no more) (listening|talking)|\bgo to sleep\b|\b(pause|mute) yourself\b|"
                   r"\btake a break\b|\bdon'?t listen\b|\bleave me alone\b|\bgo (quiet|dark)\b", re.I)
LEARN_VOICE = re.compile(r"\b(learn|re-?learn|remember|enrol+|train on|recogni[sz]e) (my|me|to my) ?voice\b", re.I)
SOCIAL_OFF = re.compile(r"\b(stop|end|exit|leave|turn off|no more|cancel)\s+(the\s+)?social mode\b|\bsocial mode off\b", re.I)
SOCIAL_ON = re.compile(r"\bsocial mode\b", re.I)
FORGET_VOICE = re.compile(r"\bforget my voice\b", re.I)
RESUME = re.compile(r"\bwake up\b|\b(start|keep|begin) listening\b|\bi'?m back\b|\blisten again\b|\byou can listen\b", re.I)
# Ends a conversation without a reply: "stop", "never mind", "that's all, thanks".
DONE = re.compile(r"^(ok(ay)?|alright|no|thanks|thank you|cheers|great|perfect|cool|stop|enough|cancel|"
                  r"never ?mind|forget it|that'?s (all|it|enough)|i'?m (good|done|fine)|nothing|"
                  r"be quiet|quiet|shut up|shh+|bye|goodbye)\b[\s,.!]*"
                  r"(thanks|thank you|that'?s all|that'?s it|bye|for now|now|please|zephyr|mate|bro)?[\s,.!]*$", re.I)


def respond(text, brain, voice, memory=None):
    """Says the reply. TV commands run instantly on the laptop; anything else goes to Claude,
    whose reply is spoken sentence by sentence while it is still being written.
    Returns 'chat' (keep listening for a follow-up), 'pause', or None."""
    if not text.strip(' .'):
        return None
    question = without_name(text)
    if PAUSE.search(text):
        log.info(f'heard: "{text}" -> pausing')
        voice.say(f"Okay, I'll stop listening. Say {NAME}, wake up, when you need me.", interruptible=False)
        return 'pause'
    if DONE.match(question or ''):
        log.info(f'heard: "{text}" -> conversation ended')
        return None
    action = parse(text)
    if action:
        # Say it straight away and do it while she's talking; the TV can take 40 s to wake, and
        # there's no need to wait for that. She only speaks again if it didn't work.
        result = {}
        job = threading.Thread(target=lambda: result.update(reply=action()), daemon=True)
        job.start()
        voice.say(quick_ack(action, text))
        job.join(timeout=4)
        if 'reply' not in result:
            threading.Thread(target=lambda: (job.join(), log.info(f'heard: "{text}" -> {result.get("reply")}')),
                             daemon=True).start()
            return None
        log.info(f'heard: "{text}" -> {result["reply"]}')
        if failed(result['reply']):
            voice.say(result['reply'])
        return None
    if not question:
        return None
    if brain is None:
        log.info(f'heard: "{text}" -> no matching command (no Claude key)')
        voice.say("Sorry, I can turn the TV on or off, go home, or open an app.")
        return None
    started = time.time()
    reply, first_sound = voice.say_stream(brain.ask_stream(question, log))
    log.info(f'heard: "{text}" -> Claude (voice started after {first_sound or 0:.1f} s, '
             f'done {time.time() - started:.1f} s): {reply}')
    if memory:
        try:
            memory.log_turn(question, reply)
        except Exception as e:
            log.info(f'could not write the conversation note: {e}')
    # Keep listening without the name only when she asked something back; after a TV action
    # or a plain answer, wait for her name again so nearby conversation isn't taken as a question.
    if reply.rstrip().endswith('?') and not brain.used_tools:
        return 'chat'
    return None


def handle(text):
    action = parse(text)
    return action() if action else None


# ---------------------------------------------------------------- speech

SILENT_NOTE = re.compile(r"stay quiet|keep quiet|stay silent|leave (it|that|this)( one)? (alone|there)|"
                         r"(that'?s|this is|just) a fragment|nothing for me|not (meant )?for me|no clear request|"
                         r"i'?ll (leave|skip) (it|that)", re.I)


def speakable(text):
    """Never read out stage directions: Claude sometimes writes "[no response]" instead of nothing,
    or announces that it will stay quiet ("That's a fragment, so I'll stay quiet") - not said either."""
    text = re.sub(r'\[[^\]]*\]|\([^)]*no response[^)]*\)', '', text, flags=re.I).strip()
    return '' if SILENT_NOTE.search(text) else text


_ONES = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve',
         'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen']
_TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']


def number_words(n):
    """0-999 in words: 15 -> "fifteen", 42 -> "forty-two", 105 -> "one hundred and five"."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ('-' + _ONES[n % 10] if n % 10 else '')
    return _ONES[n // 100] + ' hundred' + (' and ' + number_words(n % 100) if n % 100 else '')


def spoken_numbers(sentence):
    """Whole numbers up to 999 as words before speaking: the voice said "Volume's at 15" as "volume at
    fen" (27 Oct). Times, decimals and longer numbers are left as they are."""
    return re.sub(r'(?<![\d:.,])\b(\d{1,3})\b(?![\d:]|[.,]\d)', lambda m: number_words(int(m.group(1))), sentence)


class ElevenLabs:
    """ElevenLabs Flash v2.5: human-sounding speech, ~0.4 s to first audio from here (measured).
    Each sentence is sent with the one before it, so the intonation flows across sentences.
    Any failure (no internet, out of credits) returns None and Kokoro speaks instead."""
    URL = 'https://api.elevenlabs.io/v1/text-to-speech/{}/stream?output_format=pcm_24000'

    def __init__(self, key, voice_id):
        self.key, self.voice_id = key, voice_id
        self.off_until = 0.0

    def make(self, sentence, previous=''):
        if time.time() < self.off_until:
            return None
        body = json.dumps({'text': sentence, 'model_id': 'eleven_flash_v2_5', 'previous_text': previous[-300:],
                           'voice_settings': {'stability': 0.4, 'similarity_boost': 0.75,
                                              'speed': RHYTHM.voice_speed()}}).encode()
        req = urllib.request.Request(self.URL.format(self.voice_id), data=body, headers={
            'xi-api-key': self.key, 'Content-Type': 'application/json', 'User-Agent': 'SmartRoom-Assistant/1.0'})
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                pcm = np.frombuffer(r.read(), np.int16)
            return pcm, 24000
        except urllib.error.HTTPError as e:
            detail = e.read()[:200].decode('utf-8', 'replace')
            if e.code in (401, 402, 429) or 'quota' in detail:
                self.off_until = time.time() + 600            # out of credits / limited: Kokoro for 10 min
            log.info(f'ElevenLabs {e.code}: {detail}; using Kokoro')
        except Exception as e:
            log.info(f'ElevenLabs unavailable ({e}); using Kokoro')
        return None


class Voice:
    """Her voice: ElevenLabs when a key is set (most human-sounding), Kokoro on this laptop
    otherwise or as the backup (natural, offline, free; ~0.4-1.4 s per sentence here).
    Each sentence is spoken while the next one is being made. Windows' own voice (SAPI) is the
    last resort if Kokoro can't load."""

    def __init__(self):
        self.kokoro, self.sapi = None, None
        self.cache, self.cut, self.barged, self.interruptible = {}, threading.Event(), False, False
        self.voice = ENV.get('TTS_VOICE', 'af_heart')
        self.eleven = None
        if ENV.get('ELEVENLABS_API_KEY'):
            self.eleven = ElevenLabs(ENV['ELEVENLABS_API_KEY'], ENV.get('ELEVENLABS_VOICE') or 'cgSgspJ2msm6clMCkdW9')
        self.previous = ''                   # what she just said, for ElevenLabs' intonation
        self.lang = 'en-gb' if self.voice.startswith('b') else 'en-us'
        try:
            from kokoro_onnx import Kokoro
            m = os.path.join(HERE, 'models', 'kokoro')
            self.kokoro = Kokoro(os.path.join(m, 'kokoro-v1.0.int8.onnx'), os.path.join(m, 'voices-v1.0.bin'))
            self.kokoro.create("Ready when you are, just say the word.", voice=self.voice, speed=1.05, lang=self.lang)   # warm-up: the first call is slow
        except Exception as e:
            log.info(f'Kokoro unavailable ({e}); using the Windows voice')
            import comtypes.client
            self.sapi = comtypes.client.CreateObject('SAPI.SpVoice')
            self.sapi.Rate = 1
            self.sapi.Volume = 100

        # The wake sound (Epidemic Sound "Soft Short Pad" chord), loaded once so it plays instantly.
        with wave.open(os.path.join(HERE, 'wake.wav')) as w:
            self.wake = (np.frombuffer(w.readframes(w.getnframes()), np.int16), w.getframerate())

    def _make(self, sentence):
        sentence = spoken_numbers(sentence)
        # short replies repeat a lot ("Volume's at eight."): keep them, so they play instantly
        key = (sentence, RHYTHM.voice_speed())
        hit = self.cache.get(key)
        if hit:
            return hit
        made = self.eleven.make(sentence, self.previous) if self.eleven else None
        self.previous = (self.previous + ' ' + sentence)[-300:]
        if made is None:
            made = self.kokoro.create(sentence, voice=self.voice, speed=round(1.05 * RHYTHM.voice_speed(), 2),
                                      lang=self.lang)
        # keep short confirmations, never reactions or jokes: replaying "Ha, sure." sounds canned
        if len(sentence) < 60 and not re.match(r"(ha|haha|ooh|oh|hmm|ah|aw|wow|heh|well|so)\b", sentence, re.I):
            if len(self.cache) > 300:
                self.cache.clear()
            self.cache[key] = made
        return made

    def interrupt(self):
        """You said the name while she was talking: stop mid-sentence, like a person would."""
        if self.interruptible:
            self.cut.set()

    def take_interrupt(self):
        hit = self.barged
        self.barged = False
        return hit

    def wake_sound(self):
        """Play without waiting: you can keep talking straight through it."""
        global SOUND_UNTIL
        try:
            sd.play(*self.wake)
            SOUND_UNTIL = time.time() + len(self.wake[0]) / self.wake[1] + 0.15
        except Exception as e:
            log.info(f'wake sound failed: {e}')

    def say(self, text, interruptible=True):
        return self.say_stream([text], interruptible)

    def say_stream(self, pieces, interruptible=True):
        """Speak text that is still arriving (Claude streams its reply): each sentence is voiced
        as soon as it is complete, while later ones are still being written and made.
        Saying the name while she talks cuts her off (see interrupt).
        Returns (everything said, seconds until the first sound)."""
        started, first_sound, said = time.time(), None, []
        self.previous = ""                   # a new reply: intonation starts fresh
        self.cut.clear()
        self.interruptible = interruptible   # off for lines that contain her own name
        try:
            if self.sapi:
                text = ''.join(pieces).strip()
                self.sapi.Speak(text, 0)    # 0 = speak now and wait until done
                return text, time.time() - started
            made, spoken = queue.Queue(), []
            def maker():
                buf = ''
                try:
                    for piece in pieces:
                        said.append(piece)
                        buf += piece
                        # a sentence is complete once text follows its full stop ("3.5" stays whole);
                        # the opening one is cut at its first comma too, so the voice starts sooner
                        while (m := re.search(r'[.!?]+\s' if spoken else r'[.!?]+\s|\w{2,},\s', buf)):
                            spoken.append(1)
                            sentence, buf = speakable(buf[:m.end()]), buf[m.end():]
                            if len(sentence) > 1:
                                made.put(self._make(sentence))
                    if speakable(buf):
                        made.put(self._make(speakable(buf)))
                except Exception as e:
                    log.info(f'reply failed mid-way: {e}')
                finally:
                    made.put(None)
            threading.Thread(target=maker, daemon=True).start()
            while True:
                item = made.get()
                if item is None:
                    break
                if self.cut.is_set():
                    break
                if first_sound is None:
                    first_sound = time.time() - started
                sd.play(*item)
                while sd.get_stream().active and not self.cut.is_set():
                    time.sleep(0.02)
                if self.cut.is_set():
                    sd.stop()
                    break
        except Exception as e:
            log.info(f'speech failed: {e}')
        if self.cut.is_set():
            self.cut.clear()
            self.barged = True
            log.info('interrupted')
        self.interruptible = False
        return ''.join(said).strip(), first_sound


class LiveTranscriber:
    """Corelynx live streaming: your words are transcribed while you're still talking.
    Measured here: the live text was complete 0.03 s after speech ended, while a one-shot
    upload took ~1.5 s. Opening a connection takes ~1.1 s, so one is always kept open and
    ready, and a fresh spare is opened in the background after each use."""
    URL = 'wss://sttapi.corelynx.io/v1/stream'
    SPARE_MAX_AGE_S = 90             # Corelynx drops idle connections (SSL EOF seen): keep the spare fresh

    def __init__(self, key):
        self.key = key
        self.lock = threading.Lock()
        self.spare = None                      # (websocket, opened_at)
        self.need = threading.Event()
        threading.Thread(target=self._keep_spare_fresh, daemon=True).start()

    def _open(self):
        ws = websocket.create_connection(self.URL, timeout=8, header=[
            'Authorization: Bearer ' + self.key, 'User-Agent: SmartRoom-Assistant/1.0'])
        if json.loads(ws.recv()).get('type') != 'ready':
            ws.close()
            raise RuntimeError('stream did not become ready')
        ws.settimeout(None)
        return ws

    def _keep_spare_fresh(self):
        while True:
            with self.lock:
                stale = self.spare is None or time.time() - self.spare[1] > self.SPARE_MAX_AGE_S
                old = self.spare if stale else None
            if stale:
                try:
                    ws = self._open()
                    with self.lock:
                        self.spare = (ws, time.time())
                    if old:
                        try:
                            old[0].close()
                        except Exception:
                            pass
                except Exception as e:
                    log.info(f'live stream connect failed: {e}')
            self.need.wait(10)               # refill as soon as the spare is taken
            self.need.clear()

    @staticmethod
    def _alive(ws):
        """A spare can look connected after the server dropped it: ping it before trusting it."""
        if not ws.connected:
            return False
        try:
            ws.ping()
            return True
        except Exception:
            return False

    def start(self):
        with self.lock:
            spare, self.spare = self.spare, None
        self.need.set()
        try:
            ws = spare[0] if spare and self._alive(spare[0]) else self._open()
        except Exception as e:
            log.info(f'live stream unavailable: {e}')
            return None
        return LiveSession(ws)


class LiveSession:
    """One utterance on a live stream. Only the speech recogniser's own text is used, never
    Corelynx's LLM stage: in testing it rewrote "Zephyr" to "Jarvis"."""

    def __init__(self, ws):
        self.ws, self.ok = ws, True
        self.order, self.live, self.deltas = [], {}, {}
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while True:
            try:
                m = json.loads(self.ws.recv())
            except Exception:
                return
            t, uid = m.get('type'), m.get('utterance_id')
            if uid is not None and uid not in self.order:
                self.order.append(uid)
            if t == 'revision':
                self.live[uid] = f"{m.get('stable', '')} {m.get('tentative', '')}".strip()
            elif t == 'final':
                self.deltas[uid] = self.deltas.get(uid, '') + m.get('text', '')
            elif t == 'correction' and m.get('stage') == 'asr':
                self.live[uid] = m.get('corrected', '')
            elif t == 'error':
                self.ok = False
                log.info(f'live stream error: {m.get("code")} {m.get("message")}')

    def send(self, frame):
        if not self.ok:
            return
        try:
            for i in range(0, len(frame), 640):                     # 40 ms chunks, as Corelynx asks
                self.ws.send_binary(frame[i:i + 640].tobytes())
        except Exception:
            self.ok = False

    def dangling(self):
        """The words so far stop mid-thought: "...volume to", "...play some"."""
        t = ' '.join(self.live.get(u) or self.deltas.get(u, '') for u in self.order).strip().lower()
        return bool(re.search(r"\b(to|at|the|a|an|and|on|for|of|uh|um|like|is|my|some|with|in|play|set|put|turn|"
                              r"that|which|who|so|but|because|or|if|when|about|from|into|than|just|maybe)[,]?$", t))

    def sounds_finished(self):
        """The live transcript ends a sentence ("...volume to eight.") with words after the name.
        Only asked once you've gone quiet, so a short pause is enough. "Zephyr," or "turn on the"
        are not finished."""
        t = ' '.join(self.live.get(u) or self.deltas.get(u, '') for u in self.order).strip()
        return bool(re.search(r'[.?!]$', t)) and len(without_name(t).split()) >= 2

    def text(self, settle_s=0.12):
        time.sleep(settle_s)                                        # let the last revision land
        end = time.time() + 0.6
        while not any(self.live.get(u) or self.deltas.get(u) for u in self.order) and time.time() < end and self.ok:
            time.sleep(0.05)
        parts = [self.live.get(u) or self.deltas.get(u, '') for u in self.order]
        return ' '.join(p.strip() for p in parts if p and p.strip()).strip()

    def close(self):
        try:
            self.ws.send(json.dumps({'type': 'close'}))
        except Exception:
            pass
        threading.Timer(3.0, lambda: self.ws.close()).start()


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
        'User-Agent': 'SmartRoom-Assistant/1.0', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r).get('text', '').strip()


# ---------------------------------------------------------------- main loop

def main():
    restore_screen_timeout()
    threading.Thread(target=stay_awake, daemon=True, name='stay-awake').start()
    model = Model(wakeword_models=['hey_jarvis'], inference_framework='onnx') if USE_HEY_JARVIS else None
    spotter = NameSpotter()
    voice = Voice()
    live = LiveTranscriber(ENV['CORELYNX_API_KEY'])
    memory = None
    try:
        sys.path.insert(0, HERE)            # memory.py and brain.py sit next to this file
        from memory import Memory
        memory = Memory(ENV.get('OBSIDIAN_VAULT') or os.path.join(os.path.expanduser('~'), 'Documents', 'Zephyr Vault'))
        log.info(f'memory: {memory.facts_file}')
    except Exception as e:
        log.info(f'memory off: {e}')
    voice_id = None
    try:
        from voice_id import VoiceID, ENROL_PHRASES
        voice_id = VoiceID(RATE)
        log.info('voice ID: ' + (f'on (match bar {voice_id.threshold:.2f})' if voice_id.enrolled
                                 else 'not trained yet: say "Zephyr, learn my voice"'))
    except Exception as e:
        log.info(f'voice ID off: {e}')
    brain = None
    if ENV.get('ANTHROPIC_API_KEY'):
        from brain import Brain, MODEL as BRAIN_MODEL
        brain = Brain(ENV['ANTHROPIC_API_KEY'], NAME, {
            'on': lambda: in_background_if_off(turn_on, 'The TV is starting up (about 30 seconds); Home opens by itself.'),
            'off': turn_off, 'off_fully': turn_off_fully,
            'art': lambda: in_background_if_off(go_art, 'The TV is starting up; Art Mode will come on by itself.'),
            'home': lambda: in_background_if_off(go_home, 'The TV is starting up (about 30 seconds); Home opens by itself.'),
            'open': lambda app: in_background_if_off(
                lambda: open_app(app.lower()) or f"There's no app called {app} on the TV.",
                f'The TV is starting up (about 30 seconds); {app} will open by itself.'),
            'volume': set_volume, 'mute': set_mute, 'keys': press_keys, 'status': tv_status,
            'spotify': lambda q, kind: in_background_if_off(
                lambda: spotify_play(q, kind), f'The TV is starting up (about 30 seconds); then Spotify will play "{q}".'),
            'spotify_control': spotify_control,
            'youtube_control': youtube_control,
            'setting': tv_setting,
            'scene': lambda name: SCENE_BY_NAME[name]() if name == 'night' else in_background_if_off(
                SCENE_BY_NAME[name], f'The TV is starting up (about 30 seconds); {name} mode follows by itself.'),
            'youtube': lambda q: in_background_if_off(
                lambda: youtube_play(q), f'The TV is starting up (about 30 seconds); "{q}" will play on YouTube once it is on.'),
        }, memory)
        log.info(f'conversation on: Claude {BRAIN_MODEL}')

        def warm_up_youtube():           # first video request shouldn't pay for loading and connecting
            try:
                import yt_dlp  # noqa: F401
                if youtube():
                    youtube().warm_up()
            except Exception as e:
                log.info(f'YouTube warm-up skipped: {e}')
        threading.Thread(target=warm_up_youtube, daemon=True).start()
    else:
        log.info('conversation off: add ANTHROPIC_API_KEY to corelynx.env to talk with Claude')
    frames = queue.Queue()
    # Where the mic's audio goes: 'main' = listening for you; 'barge' = she's working or talking,
    # so only her name is listened for (to cut her off), never commands or her own voice.
    mic = {'to': 'barge'}
    barge_frames = queue.Queue()
    def route(f):
        (frames if mic['to'] == 'main' else barge_frames).put(f)
    # Mic and speaker: MIC_DEVICE / SPEAKER_DEVICE in corelynx.env (part of the name, e.g. AIRHUG),
    # else Windows' defaults. A speakerphone (anything but the laptop's own mic array) has its own
    # level control and echo cancellation: no digital boost, it beats the phone mic, and it can keep
    # listening while her chime plays.
    global SPEAKERPHONE
    sd.default.device = (pick_device(ENV.get('MIC_DEVICE'), 'input'), pick_device(ENV.get('SPEAKER_DEVICE'), 'output'))
    SPEAKERPHONE = 'microphone array' not in sd.query_devices(sd.default.device[0])['name'].lower()
    gain = SPEAKERPHONE_GAIN if SPEAKERPHONE else MIC_GAIN    # the laptop mic is quiet after its own noise suppression
    phone = None
    def on_audio(data, n, t, s):
        if phone and phone.connected and not SPEAKERPHONE:
            return                        # the phone is the mic right now
        if SPEAKERPHONE:
            route(soft_gain(data[:, 0], gain))
        else:
            route(np.clip(data[:, 0].astype(np.int32) * gain, -32768, 32767).astype(np.int16))
    stream = sd.InputStream(samplerate=RATE, channels=1, dtype='int16', blocksize=FRAME, callback=on_audio)
    # Android phone as the mic (AndroidMic app, Wi-Fi): used whenever it's connected. Its audio
    # follows the laptop mic's on/off, so Zephyr never hears itself through the phone either.
    try:
        from phone_mic import PhoneMic
        global PHONE
        phone = PHONE = PhoneMic(int(ENV.get('PHONE_MIC_PORT', '55555')), RATE, FRAME,
                         route, log,
                         gain=float(ENV.get('PHONE_MIC_GAIN', '1.0')))
    except Exception as e:
        log.info(f'phone mic off: {e}')
    stream.start()
    log.info(f'{NAME} listening on "{sd.query_devices(sd.default.device[0])["name"]}"')
    log.info(f'replies play on "{sd.query_devices(sd.default.device[1])["name"]}"' +
             (' (speakerphone: echo cancelled, phone mic not needed)' if SPEAKERPHONE else ''))
    # Interrupting: while she talks, a second spotter listens for her name only.
    barge_spotter = NameSpotter(spotter.model)
    def watch_for_barge_in():
        fresh = True
        while True:
            f = barge_frames.get()
            if mic['to'] != 'barge' or not voice.interruptible:
                if not fresh:
                    barge_spotter.reset()     # start clean next time she talks
                    fresh = True
                continue
            fresh = False
            if barge_spotter.heard(f) and barge_spotter.conf >= SURE_CONFIDENCE:
                voice.interrupt()
                barge_spotter.reset()
                fresh = True
    threading.Thread(target=watch_for_barge_in, daemon=True).start()
    voice.say(f'{NAME} online.', interruptible=False)
    while not frames.empty():
        frames.get_nowait()
    mic['to'] = 'main'
    history = deque(maxlen=HISTORY_FRAMES)   # the last ~4 s of audio
    paused = False                    # "stop listening": ignore everything until "<name>, wake up"
    social = {'on': False}            # "social mode": voice ID off, anyone can talk to her
    noise = 300.0                     # running estimate of room noise (RMS)
    beat_at, beat_level, beat_score, beat_frames = time.time() + 15, 0.0, 0.0, 0
    while True:
        f = frames.get()
        rms = float(np.sqrt(np.mean(f.astype(np.float32) ** 2)))
        noise = 0.995 * noise + 0.005 * min(rms, noise * 3)
        history.append(f)
        score = max(model.predict(f).values()) if model else 0.0
        said_name = spotter.heard(f)      # the name on its own, spotted locally by Vosk
        beat_level, beat_score, beat_frames = max(beat_level, rms), max(beat_score, score), beat_frames + 1
        if time.time() >= beat_at and DIAGNOSTIC:
            log.info(f'heartbeat: {beat_frames} frames, loudest {beat_level:.0f}, best wake score {beat_score:.3f}')
            beat_at, beat_level, beat_score, beat_frames = time.time() + 15, 0.0, 0.0, 0
        if score < SOFT_THRESHOLD and not said_name:
            continue
        confident = score >= WAKE_THRESHOLD
        by_name = said_name and not confident
        log.info(f'wake word (score {score:.2f}' + (f', "{NAME}" spotted {spotter.conf:.2f}' if said_name else '') +
                 f', {"confident" if confident else "checking transcript"})')

        # ---- woke. Spotted by name: Vosk reports it at the first pause, often the comma in
        #      "Zephyr, go home", so keep recording in case the command follows. Possible
        #      ("Hey Jarvis" model, low score): record, then check the transcript.
        #      Everything is streamed to Corelynx while it's recorded, so the words are ready
        #      the moment you stop talking.
        def listen(preroll, giveup, strict=False, duck=True):
            """Record until a pause; return (text, heard_speech). Live text, else a one-shot upload.
            The TV is muted while you talk (duck) and unmuted as soon as you've finished."""
            if duck:
                DUCK.duck()               # ~0.05 s; audio keeps queuing meanwhile, nothing is lost
            session = live.start()
            if session:
                for f in preroll:
                    session.send(f)
            command, speech = record_command(frames, preroll, noise, giveup=giveup,
                                             on_frame=session.send if session else None,
                                             finished=session.sounds_finished if session else None,
                                             dangling=session.dangling if session else None)
            mic['to'] = 'barge'           # don't listen to ourselves while we work and talk
            threading.Thread(target=DUCK.restore, daemon=True).start()
            # what was just said (plus ~1.5 s before, where the name usually is), for voice ID
            last['audio'] = np.concatenate(command[max(0, len(preroll) - 20):])
            last['rejected'], last['score'] = False, None
            if voice_id and voice_id.enrolled and speech and not social['on']:
                ok, sc = voice_id.is_you(last['audio'])
                bar = max(voice_id.threshold, STRICT_VOICE_MATCH) if strict else voice_id.threshold
                if sc is not None and sc < bar:
                    last['rejected'] = True
                    session and session.close()
                    log.info(f'not your voice (match {sc:.2f} < {bar:.2f}{", no name said" if strict else ""}); ignored')
                    return '', False
                if sc is not None:
                    last['score'] = sc
                    log.info(f'voice match {sc:.2f}')
                    voice_id.learn(last['audio'], sc)     # clear matches sharpen your voiceprint
            ended = time.time()
            text = ''
            if session:
                text = session.text()
                session.close()
            if not text and (speech or not session):
                text = transcribe(np.concatenate(command))
                how = 'upload'
            else:
                how = 'live'
            log.info(f'transcript ({how}, {time.time() - ended:.2f} s after you stopped): "{text}"')
            RHYTHM.note_rate(len(text.split()), LAST_TALK_S[0])
            if DIAGNOSTIC or ((not speech or len(text.split()) <= 2) and len(os.listdir(DIAG_DIR) if os.path.isdir(DIAG_DIR) else []) < MISSED_CLIPS_KEPT):
                save_diag(np.concatenate(command), text)      # misses kept to tune quiet night-time listening
            return text, speech

        last = {'audio': None, 'rejected': False, 'score': None}

        def learn_voice():
            voice.say("Sure. I'll read six short phrases. After each one, wait for the sound, "
                      "then say it back in your normal voice.", interruptible=False)
            clips = []
            for phrase in ENROL_PHRASES:
                for _attempt in range(2):
                    voice.say(phrase, interruptible=False)
                    voice.wake_sound()
                    time.sleep(0.35)
                    mic['to'] = 'main'
                    while not frames.empty():
                        frames.get_nowait()
                    command, speech = record_command(frames, [], noise, giveup=5.0)
                    mic['to'] = 'barge'
                    audio = np.concatenate(command)
                    if speech and len(audio) > RATE * 1.0:
                        clips.append(audio)
                        break
                    voice.say("I didn't catch that. Once more.", interruptible=False)
            if len(clips) < 4:
                voice.say("I couldn't hear enough to learn your voice. Let's try again later.")
                return
            own, bar = voice_id.enrol(clips)
            log.info(f'voice learned from {len(clips)} clips: self-match {own:.2f}, bar {bar:.2f}')
            voice.say("Got it. From now on I'll only answer to your voice.")

        def listen_again(giveup, strict=False):
            while not frames.empty():
                frames.get_nowait()
            mic['to'] = 'main'
            return listen([], giveup, strict)

        # The local spotter also fires on TV dialogue and people talking ("Barbara", "so like"),
        # so unless it's very sure, the transcript has to contain the name before she reacts.
        sure = confident or (by_name and spotter.conf >= SURE_CONFIDENCE)
        sounded = False
        if sure and not paused:
            voice.wake_sound()
            sounded = True
        if brain and not paused:
            brain.warm()                  # connect to Claude while you're still talking
        try:
            if by_name:
                text, heard_speech = listen(list(history), NAME_PAUSE_S, duck=sure and not paused)   # people pause after the name
            else:
                text, heard_speech = listen(list(history)[-PREROLL_FRAMES:], NO_SPEECH_GIVEUP_S, duck=sure and not paused)
            if last['rejected']:
                continue                  # someone else: stay completely silent
            if voice_id and LEARN_VOICE.search(text) and (bool(NAME_LOOSE.search(text)) or sure):
                learn_voice()
                continue
            if SOCIAL_OFF.search(text) and (sure or NAME_LOOSE.search(text)):
                social['on'] = False
                log.info('social mode off: only your voice again')
                voice.say("Okay, social mode's off. Back to just you.")
                continue
            if SOCIAL_ON.search(text) and (sure or NAME_LOOSE.search(text)):
                social['on'] = True
                log.info('social mode on: answering anyone')
                voice.say("Social mode on. I'll listen to everyone.")
                continue
            if voice_id and FORGET_VOICE.search(text) and voice_id.enrolled:
                voice_id.forget()
                log.info('voice ID forgotten')
                voice.say("Done. I'll answer to anyone again.")
                continue
            if paused:
                if RESUME.search(text) and NAME_LOOSE.search(text):
                    paused = False
                    log.info('resumed')
                    voice.say("I'm back.")
                continue
            action = parse(text)
            name_in_text = bool(NAME_LOOSE.search(text) or NAME_AT_START.search(text))
            # From across the room the name comes out as a different word every time ("That's true",
            # "Left for", "Therefore", 27 Oct) though the spotter heard it and the voice was clearly
            # Mark's: then trust them. TV and other people still need the name in the transcript.
            yours = by_name and (last['score'] or 0) >= YOUR_VOICE_SURE
            if not sure and not name_in_text and not yours:
                # the name wasn't really said (TV, music, people talking): stay silent
                log.info(f'name not in transcript (spotter {spotter.conf:.2f}); ignored')
                continue
            if not action and not without_name(text):
                # only the name was said: the sound is the cue, now listen for the command
                if not sounded:
                    voice.wake_sound()
                text, heard_speech = listen_again(NO_SPEECH_GIVEUP_S)
            outcome = respond(text, brain, voice, memory)
            # Cut off mid-reply ("Zephyr, actually..."): listen straight away. After she asks
            # something back, listen briefly without the name. Otherwise wait for the name, so a
            # show's dialogue or nearby conversation isn't taken as a question.
            while True:
                if voice.take_interrupt():
                    voice.wake_sound()
                    text, heard_speech = listen_again(NO_SPEECH_GIVEUP_S, strict=True)
                elif outcome == 'chat':
                    text, heard_speech = listen_again(FOLLOW_UP_S, strict=True)
                else:
                    break
                if not text.strip(' .'):
                    break
                outcome = respond(text, brain, voice, memory)
            if outcome == 'pause':
                paused = True
        except Exception as e:
            log.info(f'error: {e}')
            voice.say("Sorry, something went wrong.")
        finally:
            if model:
                model.reset()
            spotter.reset()
            history.clear()
            voice.take_interrupt()
            DUCK.restore()                # never leave the TV muted, whatever happened
            while not frames.empty():
                frames.get_nowait()
            mic['to'] = 'main'


class NameSpotter:
    """Spots the assistant's name (WAKE_NAME) locally with Vosk, using a one-word vocabulary.
    For "Jarvis", the "hey_jarvis" model scored the name alone only 0.03-0.43 in this voice;
    Vosk catches it reliably. It reports the word once the speaker pauses."""

    def __init__(self, model=None):
        SetLogLevel(-1)
        self.model = model or VoskModel(os.path.join(HERE, 'models', 'vosk-model-small-en-us-0.15'))
        self.reset()

    def reset(self):
        self.conf = 0.0
        self.rec = KaldiRecognizer(self.model, RATE, json.dumps([NAME.lower(), '[unk]']))
        self.rec.SetWords(True)

    def heard(self, frame):
        if not self.rec.AcceptWaveform(frame.tobytes()):
            return False
        words = json.loads(self.rec.Result()).get('result', [])
        self.conf = max((w.get('conf', 0) for w in words if w['word'] == NAME.lower()), default=0.0)
        return self.conf >= SPOT_CONFIDENCE


DIAG_DIR = os.path.join(HERE, 'diag')       # stays on this laptop (gitignored)
MISSED_CLIPS_KEPT = 50


def save_diag(audio, text):
    """Keep a recording and its transcript so a miss can be inspected: everything in diagnostic mode,
    otherwise only misses (nothing heard, or just a word or two), up to MISSED_CLIPS_KEPT files."""
    d = DIAG_DIR
    os.makedirs(d, exist_ok=True)
    name = time.strftime('%H%M%S')
    with wave.open(os.path.join(d, name + '.wav'), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(audio.tobytes())
    log.info(f'diag {name}.wav: {len(audio) / RATE:.1f} s, transcript "{text}"')


_vad = None


def speech_detector():
    """Silero VAD (via sherpa-onnx): tells speech from silence and noise by how it sounds, not how
    loud it is, so a quiet phone mic or a noisy room doesn't confuse it. ~0.4 ms per frame here.
    It reports silence after 0.25 s, which is counted into the pause lengths below."""
    global _vad
    if _vad is None:
        import sherpa_onnx
        c = sherpa_onnx.VadModelConfig()
        c.silero_vad.model = os.path.join(HERE, 'models', 'silero_vad.onnx')
        c.silero_vad.threshold = 0.35            # 0.5 missed quiet night-time speech from bed (27 Oct: 58% "nothing said")
        c.silero_vad.min_silence_duration = 0.25
        c.silero_vad.min_speech_duration = 0.1
        c.sample_rate = RATE
        _vad = sherpa_onnx.VoiceActivityDetector(c, buffer_size_in_seconds=30)
    return _vad


def pick_device(name_part, kind):
    """The audio device whose name contains name_part (e.g. "AIRHUG"), or Windows' default if it
    isn't plugged in. kind: 'input' or 'output'."""
    default = sd.default.device[0 if kind == 'input' else 1]
    if not name_part:
        return default
    channels = 'max_input_channels' if kind == 'input' else 'max_output_channels'
    for i, d in enumerate(sd.query_devices()):
        if d[channels] > 0 and name_part.lower() in d['name'].lower() and d['hostapi'] == 0:
            return i
    log.info(f'{kind} device "{name_part}" not found; using the default')
    return default


def record_command(frames, preroll, noise, giveup=NO_SPEECH_GIVEUP_S, on_frame=None, finished=None, dangling=None):
    """Collect audio until you've finished: a pause of END_SILENCE_S, or a shorter QUICK_END_S
    pause once the live transcript shows a complete sentence, or the time limit.
    Time is counted in audio frames (80 ms each), not by the clock."""
    sec = FRAME / RATE
    command = list(preroll)
    vad = speech_detector()
    vad.reset()            # not primed with the audio before: that holds "Zephyr" itself, and the natural
                           # pause after the name then looked like the end of the request (26 Oct)
    heard_speech, quiet, skipped, reason, talked = False, 0, 0, 'time limit', 0
    for n in range(1, int((MAX_COMMAND_S + 2) / sec) + 1):
        f = frames.get()
        command.append(f)
        if on_frame:
            on_frame(f)                                  # live-stream it while still recording
        if time.time() < SOUND_UNTIL and not SPEAKERPHONE and not (PHONE and PHONE.connected):
            skipped += 1                                 # the laptop mic hears the wake sound: don't judge it
            continue
        vad.accept_waveform(f.astype(np.float32) / 32768)
        if vad.is_speech_detected():
            if heard_speech and quiet:
                RHYTHM.gaps.append(quiet * sec + 0.25)   # a pause inside your sentence: learn its length
            quiet = 0
            talked += 1
            heard_speech = talked * sec >= MIN_REQUEST_S   # a real request, not a cough or the name's tail
        elif heard_speech:
            quiet += 1                                   # each counts on top of the detector's own 0.25 s
            long_talk = talked * sec > LONG_TALK_S       # directions, not a quick command
            end_after = RHYTHM.end_silence(long_talk)     # learned from how you talk
            if quiet * sec >= end_after - 0.25:
                if dangling and dangling() and quiet * sec < 1.5:
                    continue                             # "...a video that" - you're still thinking
                reason = 'pause'
                break
            if not long_talk and quiet * sec >= QUICK_END_S - 0.25 and finished and finished():
                reason = 'sentence done'
                break
        elif (n - skipped) * sec > giveup:
            reason = 'nothing said'
            break
    LAST_TALK_S[0] = talked * sec
    log.info(f'recorded {n * sec:.1f} s, ended by {reason}' + (
        f' (your gaps ~{RHYTHM.gap():.2f} s)' if RHYTHM.gap() else ''))
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
