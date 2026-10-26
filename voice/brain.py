"""Zephyr's conversation brain: Claude Haiku 5.5, with the TV controls as tools.

Plain TV commands ("turn on the TV") never come here - assistant.py handles those instantly
on the laptop. This is for everything else: questions, chat, and natural requests like
"I'm heading to bed" that Claude turns into TV actions.
"""
import re
import threading
import time

import anthropic

MODEL = 'claude-haiku-5-5'          # the fastest current Claude; plenty for chat and TV control
FORGET_AFTER_S = 120                # a new conversation after 2 quiet minutes (less resent each reply)

SYSTEM = """You are {name}, a voice assistant in Mark's living room. Everything you write is \
spoken aloud by a text-to-speech voice, so:
- Never say your reasoning, rules or lessons out loud (no "this is a fragment", no "acting on the sensible meaning"): just act or answer.
- Answer in one or two short sentences, the way people actually talk. Never use lists, markdown, emojis or symbols.
- Never comment on how a request was worded or transcribed; just act on the sensible meaning.
- For TV, music and video actions, say nothing before or after the tool call: the result is spoken for you ("Volume's at 8.", "Playing Slow Mornings."). Saying it yourself makes you repeat it. Only add words if Mark also asked a question that the tool doesn't answer.
- What you hear comes from speech-to-text and can be wrong: "volume to eight" often arrives as "volume twenty eight". If a number or request seems odd for the situation, pick the sensible reading (normal TV volume is about 5-20). If what you hear makes no sense, or sounds like Mark talking to someone else, output nothing at all (no text, not even brackets or a note).
- Speech-to-text often turns "to" into a digit in front of a number: "volume 250" means fifty, "volume 330" means thirty, "volume 225" means twenty five. Silently drop the extra digit and set it: never explain the correction, never ask to confirm a volume.
- People talk in the room: ignore anything said before your name or not meant for you, and never mention it.
- Write numbers and times the way they are said ("ten thirty", "about twenty dirhams").
- If a question needs a long answer, give the gist and offer to say more.

You can control the Samsung Frame TV in the room with your tools. When Mark asks for something \
the TV can do, or clearly implies it ("I'm off to bed", "let's watch something"), use the tool, \
and let its result speak for you. Only claim something happened if the tool result says it did. You are the TV's admin: you have everything the remote has (power, volume, mute, every button, apps), so do multi-step requests fully, step by step, instead of telling Mark to use the remote. You \
cannot control lights or other devices yet; say so briefly if asked.

You have a long-term memory (Mark's notes, kept in Obsidian). When Mark tells you something worth keeping (a preference, a plan, a name, a date) or asks you to remember something, save it with the remember tool as one short, self-contained fact; don't announce saving unless asked. Use forget when he asks you to forget something. Use what you remember naturally. When he corrects you, just fix it and quietly save the lesson with learn_lesson.

You know a lot: recommend films and shows, answer questions and share facts from your own knowledge, and pick something specific rather than asking what he wants. You have no internet, so for live things like today's weather or news, say you can't check yet.

Mark lives in the UAE.

How you talk: like a friend sitting on the couch with Mark, not like a customer-service assistant.
- Use everyday spoken English: contractions, short phrases, sentence fragments are fine \
("Yeah, on it." "Ooh, good one." "Hmm, honestly? Interstellar.").
- Only sometimes react before you answer (about one reply in three): "Oh nice", "Ha", "Ah, okay", "Hmm", "Right", \
"Fair enough". Vary them; never open two replies in a row the same way.
- Match his energy: relaxed when he's relaxed, quick when he's in a hurry, playful when he jokes. \
Tease him lightly now and then, have opinions, and say what you'd pick.
- Write for the ear. Punctuation is your intonation: a comma for a breath, "..." for a thoughtful \
pause, "?" to lift your voice, "!" for real enthusiasm (sparingly). Keep most sentences short.
- Never say "I'd be happy to help", "Certainly", "Great question", "As an AI", "Is there anything \
else", or end with an offer of more help. Don't repeat his question back. Don't over-explain.
- For quick tasks, just a few words: "Done." "Yep, eight." "Sure, here's YouTube."
- A follow-up question is fine when you genuinely want to know, but not every time.
- Jokes: vary the kind (one-liners, puns, dry observations, a quick story, something about the room or the UAE), never two "Why did the..." in a row, and never one you've told recently."""

APP_NAMES = ['Netflix', 'YouTube', 'Prime Video', 'Spotify', 'Apple TV', 'Gallery', 'Browser', 'Google Meet', 'Moonlight']
REMOTE_KEYS = ['KEY_UP', 'KEY_DOWN', 'KEY_LEFT', 'KEY_RIGHT', 'KEY_ENTER', 'KEY_RETURN', 'KEY_HOME', 'KEY_MENU',
               'KEY_PLAY', 'KEY_PAUSE', 'KEY_STOP', 'KEY_FF', 'KEY_REWIND', 'KEY_VOLUP', 'KEY_VOLDOWN',
               'KEY_MUTE', 'KEY_CHUP', 'KEY_CHDOWN', 'KEY_SOURCE', 'KEY_INFO', 'KEY_GUIDE', 'KEY_CAPTION',
               'KEY_EXIT'] + [f'KEY_{n}' for n in range(10)]   # same list as assistant.py

TOOLS = [
    {
        'name': 'tv_power',
        'description': ('Turn the TV on (ends on the Home screen) or off. "off" = Art Mode with the screen sleeping after '
                        '5 minutes (wakes again in seconds): use it for normal "turn off". "off_fully" = complete power-off, '
                        'only when Mark says completely/fully off or that he is going away (waking from it takes ~45 s).'),
        'input_schema': {
            'type': 'object',
            'properties': {'state': {'type': 'string', 'enum': ['on', 'off', 'off_fully']}},
            'required': ['state'],
            'additionalProperties': False,
        },
        'strict': True,
        'eager_input_streaming': True,
    },
    {
        'name': 'tv_art_mode',
        'description': "Switch the Frame TV to Art Mode (it shows artwork instead of TV).",
        'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'strict': True,
        'eager_input_streaming': True,
    },
    {
        'name': 'tv_home',
        'description': "Show the TV's Home screen (the clock and the apps grid).",
        'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'strict': True,
        'eager_input_streaming': True,
    },
    {
        'name': 'tv_open_app',
        'description': 'Open an app on the TV. This turns the TV on first if it is off, so there is no need to call tv_power before it.',
        'input_schema': {
            'type': 'object',
            'properties': {'app': {'type': 'string', 'enum': APP_NAMES}},
            'required': ['app'],
            'additionalProperties': False,
        },
        'strict': True,
        'eager_input_streaming': True,
    },
    {
        'name': 'spotify_play',
        'description': ('Play music on the TV with Spotify (Premium): kind "track" for a song, "artist" for an artist, '
                        '"album", or "playlist" for moods and genres ("chill evening", "90s hip hop"). Opens Spotify '
                        'on the TV and turns the TV on if needed; takes up to 15 seconds when Spotify is not already open '
                        '(a short "Putting it on." is spoken automatically).'),
        'input_schema': {
            'type': 'object',
            'properties': {'query': {'type': 'string'},
                           'kind': {'type': 'string', 'enum': ['track', 'artist', 'album', 'playlist']}},
            'required': ['query', 'kind'],
            'additionalProperties': False,
        },
        'strict': True,
        'eager_input_streaming': True,
    },
    {
        'name': 'spotify_control',
        'description': "Control what's playing on Spotify: pause, resume, next, previous, shuffle on/off, or ask what's playing.",
        'input_schema': {
            'type': 'object',
            'properties': {'action': {'type': 'string', 'enum': ['pause', 'resume', 'next', 'previous', 'shuffle_on',
                                                                 'shuffle_off', 'now_playing']}},
            'required': ['action'],
            'additionalProperties': False,
        },
        'strict': True,
        'eager_input_streaming': True,
    },
    {
        'name': 'youtube_play',
        'description': ('Find a video on YouTube and play it on the TV (turns the TV on if needed; takes ~3 s). '
                        'Write the search the way you would type it into YouTube, adding words like "official", '
                        '"full", "trailer" or "live" when that is what Mark means.'),
        'input_schema': {
            'type': 'object',
            'properties': {'query': {'type': 'string'}},
            'required': ['query'],
            'additionalProperties': False,
        },
        'strict': True,
        'eager_input_streaming': True,
    },
    {
        'name': 'youtube_control',
        'description': 'Control the video playing in YouTube on the TV: pause, resume, next, previous.',
        'input_schema': {
            'type': 'object',
            'properties': {'action': {'type': 'string', 'enum': ['pause', 'resume', 'next', 'previous']}},
            'required': ['action'],
            'additionalProperties': False,
        },
        'strict': True,
        'eager_input_streaming': True,
    },
    {
        'name': 'tv_volume',
        'description': 'Set the TV volume. mode "set" goes to an exact level (0-100; normal listening is about 8-20); "up"/"down" change it by amount.',
        'input_schema': {
            'type': 'object',
            'properties': {'mode': {'type': 'string', 'enum': ['set', 'up', 'down']},
                           'amount': {'type': 'integer'}},
            'required': ['mode', 'amount'],
            'additionalProperties': False,
        },
        'strict': True,
        'eager_input_streaming': True,
    },
    {
        'name': 'tv_mute',
        'description': 'Mute or unmute the TV.',
        'input_schema': {
            'type': 'object',
            'properties': {'muted': {'type': 'boolean'}},
            'required': ['muted'],
            'additionalProperties': False,
        },
        'strict': True,
        'eager_input_streaming': True,
    },
    {
        'name': 'tv_remote',
        'description': ('Press TV remote buttons in order, exactly like the physical remote: navigate menus '
                        '(arrows, ENTER, RETURN), play/pause media, change channel or input, etc. You cannot see '
                        'the screen, so only use navigation when the layout is predictable; check with tv_status.'),
        'input_schema': {
            'type': 'object',
            'properties': {'keys': {'type': 'array', 'items': {'type': 'string', 'enum': REMOTE_KEYS}}},
            'required': ['keys'],
            'additionalProperties': False,
        },
        'strict': True,
        'eager_input_streaming': True,
    },
    {
        'name': 'tv_setting',
        'description': ('Change a TV setting through Samsung SmartThings (~2 s): picture_mode (Dynamic, FILMMAKER MODE, Movie, '
                        'Natural, Standard), sound_mode (Standard, Amplify), channel (a number, or up/down), or input '
                        '(TV, HDMI1, HDMI2...). Movie or FILMMAKER MODE suit films, Dynamic suits bright rooms and sport.'),
        'input_schema': {
            'type': 'object',
            'properties': {'setting': {'type': 'string', 'enum': ['picture_mode', 'sound_mode', 'channel', 'input']},
                           'value': {'type': 'string'}},
            'required': ['setting', 'value'],
            'additionalProperties': False,
        },
        'strict': True,
        'eager_input_streaming': True,
    },
    {
        'name': 'tv_status',
        'description': 'Check what the TV is doing: on/off, Art Mode, which app is on screen, volume.',
        'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'strict': True,
        'eager_input_streaming': True,
    },
    {
        'name': 'remember',
        'description': 'Save one short, self-contained fact about Mark or his home to long-term memory, e.g. "Mark is allergic to peanuts".',
        'input_schema': {
            'type': 'object',
            'properties': {'fact': {'type': 'string'}},
            'required': ['fact'],
            'additionalProperties': False,
        },
        'strict': True,
        'eager_input_streaming': True,
    },
    {
        'name': 'learn_lesson',
        'description': ('Save a lesson from a mistake so it never happens again. Use it whenever Mark corrects you '
                        '("no, I said fifteen", "that is not what I meant", "wrong app"): write what went wrong and '
                        'what to do instead, e.g. "When Mark says volume twenty five he means 25, not 20 then 5".'),
        'input_schema': {
            'type': 'object',
            'properties': {'lesson': {'type': 'string'}},
            'required': ['lesson'],
            'additionalProperties': False,
        },
        'strict': True,
        'eager_input_streaming': True,
    },
    {
        'name': 'forget',
        'description': 'Remove remembered facts that contain these words.',
        'input_schema': {
            'type': 'object',
            'properties': {'about': {'type': 'string'}},
            'required': ['about'],
            'additionalProperties': False,
        },
        'strict': True,
        'eager_input_streaming': True,
    },
]


# TV actions whose result is already a good spoken confirmation (see ask_stream's fast path)
FAST_TOOLS = {'tv_power', 'tv_home', 'tv_open_app', 'tv_art_mode', 'tv_volume', 'tv_mute', 'tv_setting',
              'spotify_play', 'spotify_control', 'youtube_play', 'youtube_control'}
SLOW_ACK = {'spotify_play': 'Putting it on.', 'youtube_play': 'Pulling it up.', 'tv_setting': 'Sure.'}
FAILED = re.compile(r"couldn'?t|can'?t|wouldn'?t|isn'?t|not connected|nothing found|no app|is off|failed|unknown", re.I)


OPENER = re.compile(r"^\s*((?:ha+|haha|ooh+|oh|hmm+|ah+|aw+|wow|heh|right|okay|ok|sure|well|nice|alright|yeah|"
                    r"fair enough|got it|good one|oh nice|ah okay)(?:[ ,]+(?:okay|ok|sure|nice|so|then|yeah|right))?[,.!]+\s+)",
                    re.I)


def _norm(text):
    return ' '.join(re.sub(r"[^a-z0-9' ]", ' ', text.lower()).split())


class Brain:
    def __init__(self, api_key, name, tv, memory=None):
        """tv: dict of callables {'on', 'off', 'art', 'home', 'open'(app_name)} returning a short status line.
        memory: a memory.Memory (the Obsidian notes), or None."""
        self.client = anthropic.Anthropic(api_key=api_key, timeout=20.0, max_retries=1)
        self.base_system = SYSTEM.format(name=name)
        self.tv = tv
        self.memory = memory
        self.history = []
        self.last = 0.0

    def _run_tool(self, tool_name, args):
        """Inputs stream in (eager_input_streaming), so the server no longer validates them:
        check them here before touching the TV."""
        if not isinstance(args, dict):
            raise ValueError('tool input was not an object')
        if tool_name == 'tv_power':
            if args.get('state') not in ('on', 'off', 'off_fully'):
                raise ValueError('state must be "on", "off" or "off_fully"')
            return self.tv[args['state']]()
        if tool_name == 'tv_art_mode':
            return self.tv['art']()
        if tool_name == 'tv_home':
            return self.tv['home']()
        if tool_name == 'tv_open_app':
            if args.get('app') not in APP_NAMES:
                raise ValueError(f'app must be one of {APP_NAMES}')
            return self.tv['open'](args['app'])
        if tool_name == 'tv_volume':
            if args.get('mode') not in ('set', 'up', 'down') or not isinstance(args.get('amount'), int):
                raise ValueError('needs mode set/up/down and an integer amount')
            n = max(0, min(100, args['amount']))
            if args['mode'] == 'set':
                return self.tv['volume'](level=n)
            return self.tv['volume'](change=n if args['mode'] == 'up' else -n)
        if tool_name == 'tv_mute':
            if not isinstance(args.get('muted'), bool):
                raise ValueError('muted must be true or false')
            return self.tv['mute'](args['muted'])
        if tool_name == 'tv_remote':
            keys = args.get('keys')
            if not isinstance(keys, list) or not keys or len(keys) > 30 or any(k not in REMOTE_KEYS for k in keys):
                raise ValueError('keys must be 1-30 known remote buttons')
            return self.tv['keys'](keys)
        if tool_name == 'spotify_play':
            if not isinstance(args.get('query'), str) or not args['query'].strip() or                     args.get('kind') not in ('track', 'artist', 'album', 'playlist'):
                raise ValueError('needs query text and kind track/artist/album/playlist')
            return self.tv['spotify'](args['query'].strip(), args['kind'])
        if tool_name == 'spotify_control':
            if args.get('action') not in ('pause', 'resume', 'next', 'previous', 'shuffle_on', 'shuffle_off', 'now_playing'):
                raise ValueError('unknown Spotify action')
            return self.tv['spotify_control'](args['action'])
        if tool_name == 'youtube_control':
            if args.get('action') not in ('pause', 'resume', 'next', 'previous'):
                raise ValueError('unknown YouTube action')
            return self.tv['youtube_control'](args['action'])
        if tool_name == 'youtube_play':
            q = args.get('query')
            if not isinstance(q, str) or not q.strip():
                raise ValueError('query must be some search text')
            return self.tv['youtube'](q.strip())
        if tool_name == 'tv_setting':
            if args.get('setting') not in ('picture_mode', 'sound_mode', 'channel', 'input') or                     not isinstance(args.get('value'), str) or not args['value'].strip():
                raise ValueError('needs a setting and a value')
            return self.tv['setting'](args['setting'], args['value'])
        if tool_name == 'tv_status':
            return self.tv['status']()
        if tool_name == 'learn_lesson':
            text = args.get('lesson')
            if not isinstance(text, str) or not text.strip():
                raise ValueError('needs the lesson text')
            return self.memory.add_lesson(text) if self.memory else 'Memory is not set up.'
        if tool_name in ('remember', 'forget'):
            text = args.get('fact' if tool_name == 'remember' else 'about')
            if not isinstance(text, str) or not text.strip():
                raise ValueError('needs some text')
            if not self.memory:
                return 'Memory is not set up.'
            return self.memory.remember(text) if tool_name == 'remember' else self.memory.forget(text)
        raise ValueError(f'unknown tool {tool_name}')

    def warm(self):
        """Open the connection to Claude in the background (called as soon as the name is heard),
        so the real request doesn't pay ~1 s for the secure handshake. Free: it only reads model info."""
        def go():
            try:
                self.client.models.retrieve(MODEL)
            except Exception:
                pass
        threading.Thread(target=go, daemon=True).start()

    def _system(self):
        """Re-read the notes every turn, so edits made in Obsidian count straight away."""
        facts = self.memory.facts() if self.memory else []
        # The tools and the fixed instructions are the same every time: cached, they cost a tenth
        # on repeat requests. The remembered facts come after the cache point, so editing them
        # in Obsidian never breaks the cache.
        blocks = [{'type': 'text', 'text': self.base_system, 'cache_control': {'type': 'ephemeral'}}]
        now = time.strftime('%A %d %B %Y, %I:%M %p').replace(' 0', ' ')
        extra = f'Right now it is {now} (UAE time, from the laptop clock).'
        if facts:
            extra += '\n\nWhat you remember:\n' + '\n'.join('- ' + f for f in facts)
        recent = self.memory.recent() if self.memory else []
        if recent:
            extra += ('\n\nYour recent conversations with Mark (oldest first). You remember these. Never reuse '
                      'their openings, jokes, stories or phrasing:\n' +
                      '\n'.join(f'[{t}] Mark: {q[:160]} | You: {a[:200]}' for t, q, a in recent))
        lessons = self.memory.lessons() if self.memory else []
        if lessons:
            extra += '\n\nLessons from past mistakes (follow them):\n' + '\n'.join('- ' + ln for ln in lessons)
        blocks.append({'type': 'text', 'text': extra})
        return blocks

    def ask_stream(self, text, log=None):
        """One spoken turn, streamed: yields pieces of the reply as Claude writes them, so the
        voice can start on the first sentence while the rest is still coming."""
        if time.time() - self.last > FORGET_AFTER_S:
            self.history = []
        self.last = time.time()
        turn_start = len(self.history)
        self.history.append({'role': 'user', 'content': text})
        spoke = False
        spoken_text = ''
        # Claude keeps opening with the same reaction ("Ha, okay." five jokes in a row): openings used
        # in her last few replies are cut before they're spoken.
        opener = ''
        recent = self.memory.recent(8) if self.memory else []
        used_openers = {_norm(m.group(1)) for _, _, a in recent if (m := OPENER.match(a + ' '))}
        self.used_tools = False

        for _ in range(4):                       # at most a few tool round trips per turn
            try:
                with self.client.messages.stream(
                    model=MODEL,
                    max_tokens=400,                    # spoken replies: a few sentences at most
                    system=self._system(),
                    tools=TOOLS,
                    output_config={'effort': 'low'},
                    thinking={'type': 'disabled'},     # answer straight away: ~0.5 s sooner (measured)
                    messages=self.history,
                ) as stream:
                    for event in stream:
                        if event.type == 'text':
                            if opener is not None:          # still deciding about the opening words
                                opener += event.text
                                # decide only once the whole opening is in: it streams in pieces
                                # ("Ha," then " okay."), and deciding on "Ha," let "Ha, okay." through
                                if len(opener) < 24 and not re.search(r'[.!?]\s|[.!?]$', opener):
                                    continue
                                m = OPENER.match(opener + ' ')
                                if m and _norm(m.group(1)) in used_openers:
                                    opener = opener[m.end():]   # said that way recently: skip it
                                piece, opener = opener, None
                            else:
                                piece = event.text
                            if piece:
                                spoke = True
                                spoken_text += piece
                                yield piece
                    if opener:                               # a reply shorter than the check
                        spoke, spoken_text = True, spoken_text + opener
                        yield opener
                        opener = None
                    response = stream.get_final_message()
            except ValueError:
                continue                             # a tool input that couldn't be parsed: ask again
            except anthropic.AuthenticationError:
                del self.history[turn_start:]
                yield "My Claude key isn't working. Check the Anthropic key in the settings file."
                return
            except anthropic.RateLimitError:
                del self.history[turn_start:]
                yield "I'm being rate limited. Try again in a minute."
                return
            except anthropic.APIConnectionError:
                del self.history[turn_start:]
                yield "I can't reach Claude right now. Check the internet connection."
                return
            except anthropic.APIStatusError as e:
                del self.history[turn_start:]
                if log:
                    log.info(f'Claude error {e.status_code}: {e.message}')
                yield 'Something went wrong talking to Claude.'
                return

            # Keep the full content (including any thinking blocks) so the history stays valid.
            self.history.append({'role': 'assistant', 'content': response.content})
            tool_uses = [b for b in response.content if b.type == 'tool_use']
            if response.stop_reason == 'refusal':
                if not spoke:
                    yield "Sorry, I can't help with that one."
                return
            if response.stop_reason != 'tool_use' or not tool_uses:
                if not spoke and self.used_tools:
                    yield 'Done.'                    # acted but said nothing: confirm briefly
                return

            fast = all(b.name in FAST_TOOLS for b in tool_uses)
            ack = next((SLOW_ACK[b.name] for b in tool_uses if b.name in SLOW_ACK), None)
            if fast and ack and not spoke:
                yield ack + ' '                      # say something right away while the slow part runs
                spoke = True
            results = []
            for block in tool_uses:
                try:
                    out = self._run_tool(block.name, block.input) or 'Done.'
                    results.append({'type': 'tool_result', 'tool_use_id': block.id, 'content': out})
                except Exception as e:
                    results.append({'type': 'tool_result', 'tool_use_id': block.id,
                                    'content': f'Failed: {e}', 'is_error': True})
                if log:
                    log.info(f'Claude used {block.name} {block.input} -> {results[-1]["content"]}')
            self.history.append({'role': 'user', 'content': results})
            self.used_tools = True
            if fast and not any(r.get('is_error') or FAILED.search(r['content']) for r in results):
                # Simple TV actions: the result already says what happened ("Volume's at 8."), so
                # speak it instead of asking Claude to word a confirmation (saves a round trip, ~1-2 s).
                said = ' '.join(r['content'] for r in results)
                self.history.append({'role': 'assistant', 'content': said})
                already = _norm(spoken_text)
                if spoke and (_norm(said) in already or any(w in already for w in ('playing', 'volume'))):
                    return                           # she already said it: don't repeat herself
                yield (' ' if spoke else '') + said
                return
            if spoke:
                yield ' '                            # keep sentences from two turns apart

    def ask(self, text, log=None):
        return ''.join(self.ask_stream(text, log)).strip()
