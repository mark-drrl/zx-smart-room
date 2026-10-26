"""Memory notes and the phone-mic protocol."""
import numpy as np

import memory as M
import phone_mic as P


def test_memory_remembers_and_forgets(tmp_path):
    m = M.Memory(str(tmp_path))
    assert m.remember('Mark likes sci-fi') == 'Saved to memory.'
    assert m.remember('mark likes  sci-fi') == 'Already remembered.'
    assert m.facts() == ['Mark likes sci-fi']
    assert m.forget('sci-fi') == 'Forgot 1 note(s).'
    assert m.facts() == []


def test_memory_logs_conversations(tmp_path):
    m = M.Memory(str(tmp_path))
    m.log_turn('what time is it', "It's ten.")
    notes = list((tmp_path / 'Zephyr' / 'Conversations').iterdir())
    assert len(notes) == 1 and "It's ten." in notes[0].read_text(encoding='utf-8')


def _varint(n):
    out = b''
    while True:
        b, n = n & 0x7F, n >> 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def test_phone_mic_reads_androidmic_packets():
    pcm = (np.sin(np.arange(960) / 10) * 8000).astype('<i2').tobytes()
    msg = b'\x0a' + _varint(len(pcm)) + pcm + b'\x10' + _varint(48000) + b'\x18' + _varint(1) + b'\x20' + _varint(2)
    data, rate, channels, fmt = P.decode_packet(msg)
    assert (rate, channels, fmt) == (48000, 1, P.PCM_16) and data == pcm
    x = P.to_float(data, fmt)
    assert x.dtype == np.float32 and len(x) == 960 and abs(x).max() <= 1.0


def test_lessons_are_kept_and_deduplicated(tmp_path):
    m = M.Memory(str(tmp_path))
    assert m.add_lesson('Zafer is my name') == 'Lesson saved.'
    assert m.add_lesson('zafer is  my name') == 'Already learned.'
    assert m.lessons()[0].startswith('Zafer is my name (correction,')
    for i in range(M.MAX_LESSONS + 5):
        m.add_lesson(f'lesson {i}', source='review')
    assert len(m.lessons()) == M.MAX_LESSONS


def test_review_measures_a_log():
    import review
    rows = [('2026-10-22 20:58:22', 'recorded 2.2 s, ended by sentence done'),
            ('2026-10-22 20:58:32', 'heard: "x" -> Claude (voice started after 1.9 s, done 10.3 s): ok'),
            ('2026-10-22 20:59:00', 'heard: "turn on the TV" -> Turning on the TV.'),
            ('2026-10-22 21:00:11', 'not your voice (match 0.20 < 0.33); ignored'),
            ('2026-10-22 21:00:12', 'interrupted')]
    m = review.measure(rows)
    assert m['conversations with Claude'] == 1 and m['instant TV commands'] == 1
    assert m['median time to first word (s)'] == 1.9 and m['voices rejected (not Mark)'] == 1
    assert m['interruptions'] == 1


def test_recent_conversations_are_remembered(tmp_path):
    m = M.Memory(str(tmp_path))
    for i in range(25):
        m.log_turn(f'question {i}', f'answer {i}')
    recent = m.recent()
    assert len(recent) == 20 and recent[-1][1:] == ('question 24', 'answer 24')


def test_repeated_openers_are_recognised():
    import brain as B
    assert B.OPENER.match('Ha, okay. Why did ').group(1) == 'Ha, okay. '
    assert B.OPENER.match('Hmm, honestly? ')
    assert not B.OPENER.match('Tokyo. ')


def test_review_flags_regressions_against_the_7_day_average():
    import review
    week = [{'median time to first word (s)': 2.0, 'slow transcript fallbacks (upload)': 10, 'errors': 1}] * 7
    worse = {'median time to first word (s)': 3.1, 'slow transcript fallbacks (upload)': 34, 'errors': 2}
    warnings = review.trends(week, worse)
    assert any('first word' in w for w in warnings) and any('upload' in w for w in warnings)
    assert not any('errors' in w for w in warnings)              # 1 -> 2 is below the floor: no alarm
    assert review.trends(week[:2], worse) == []                   # too little history to judge

