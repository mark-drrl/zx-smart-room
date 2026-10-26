"""Zephyr's memory, kept as plain Markdown in an Obsidian vault so Mark can read and edit it.

  <vault>/Zephyr/Memory.md                 - facts Zephyr keeps ("- Mark prefers sci-fi films")
  <vault>/Zephyr/Conversations/<date>.md   - what was said each day
  <vault>/Zephyr/Lessons.md                - what she has learned from mistakes (corrections, daily review)
  <vault>/Zephyr/Reports/<date>.md         - the nightly self-review (review.py)

Memory.md is read fresh before every reply, so edits made in Obsidian take effect straight away.
"""
import os
import re
import threading
import time

LESSONS_HEADER = ('# What Zephyr has learned\n\nFrom your corrections and her nightly self-review. One lesson '
                  'per line; edit or delete freely. She reads this before every reply.\n\n')
MAX_LESSONS = 60

HEADER = '# What Zephyr remembers\n\nOne fact per line. Edit freely; Zephyr reads this before every reply.\n\n'


class Memory:
    def __init__(self, vault):
        self.dir = os.path.join(vault, 'Zephyr')
        self.facts_file = os.path.join(self.dir, 'Memory.md')
        self.talk_dir = os.path.join(self.dir, 'Conversations')
        self.lessons_file = os.path.join(self.dir, 'Lessons.md')
        self.lock = threading.Lock()
        os.makedirs(self.talk_dir, exist_ok=True)
        if not os.path.exists(self.facts_file):
            with open(self.facts_file, 'w', encoding='utf-8') as f:
                f.write(HEADER)

    def facts(self):
        try:
            with open(self.facts_file, encoding='utf-8') as f:
                return [l[2:].strip() for l in f if l.startswith('- ') and l[2:].strip()]
        except OSError:
            return []

    def remember(self, fact):
        fact = ' '.join(fact.split())
        if not fact:
            return 'Nothing to remember.'
        with self.lock:
            if fact.lower() in (f.lower() for f in self.facts()):
                return 'Already remembered.'
            with open(self.facts_file, 'a', encoding='utf-8') as f:
                f.write(f'- {fact}\n')
        return 'Saved to memory.'

    def forget(self, about):
        """Removes every remembered line containing the given words."""
        about = about.lower().strip()
        if not about:
            return 'Nothing to forget.'
        with self.lock:
            with open(self.facts_file, encoding='utf-8') as f:
                lines = f.readlines()
            keep = [l for l in lines if not (l.startswith('- ') and about in l.lower())]
            removed = len(lines) - len(keep)
            if removed:
                with open(self.facts_file, 'w', encoding='utf-8') as f:
                    f.writelines(keep)
        return f'Forgot {removed} note(s).' if removed else 'Nothing matched in memory.'

    def lessons(self):
        try:
            with open(self.lessons_file, encoding='utf-8') as f:
                return [l[2:].strip() for l in f if l.startswith('- ') and l[2:].strip()]
        except OSError:
            return []

    def add_lesson(self, lesson, source='correction'):
        lesson = ' '.join(lesson.split())
        if not lesson:
            return 'Nothing to learn.'
        with self.lock:
            existing = self.lessons()
            if lesson.lower() in (l.lower().split(' (')[0] for l in existing):
                return 'Already learned.'
            lines = existing[-(MAX_LESSONS - 1):] + [f'{lesson} ({source}, {time.strftime("%d %b")})']
            with open(self.lessons_file, 'w', encoding='utf-8') as f:
                f.write(LESSONS_HEADER + ''.join(f'- {l}\n' for l in lines))
        return 'Lesson saved.'

    def recent(self, n=20, days=2):
        """The last n exchanges from the conversation notes (today and yesterday), oldest first."""
        pairs = []
        for back in range(days - 1, -1, -1):
            day = time.strftime('%Y-%m-%d', time.localtime(time.time() - back * 86400))
            try:
                with open(os.path.join(self.talk_dir, day + '.md'), encoding='utf-8') as f:
                    text = f.read()
            except OSError:
                continue
            for m in re.finditer(r'\*\*(\d\d:\d\d) Mark:\*\* (.*)\n\*\*Zephyr:\*\* (.*)', text):
                pairs.append((('yesterday ' if back else '') + m.group(1), m.group(2).strip(), m.group(3).strip()))
        return pairs[-n:]

    def log_turn(self, heard, said):
        day = time.strftime('%Y-%m-%d')
        path = os.path.join(self.talk_dir, day + '.md')
        new = not os.path.exists(path)
        with self.lock, open(path, 'a', encoding='utf-8') as f:
            if new:
                f.write(f'# Conversations {day}\n\n')
            f.write(f'**{time.strftime("%H:%M")} Mark:** {heard}\n**Zephyr:** {said}\n\n')
