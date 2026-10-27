"""The console's log reader: lines become requests with the right outcome."""
import logview as L

LOG = '''2026-10-27 22:57:30  wake word (score 0.00, "Zephyr" spotted 1.00, checking transcript)
2026-10-27 22:57:31  recorded 1.0 s, ended by pause
2026-10-27 22:57:31  voice match 0.96
2026-10-27 22:57:35  transcript (upload, 3.99 s after you stopped): "Zephyr, volume ten."
2026-10-27 22:57:37  Claude used tv_volume {'mode': 'set', 'amount': 10} -> Volume's at 10.
2026-10-27 22:57:40  heard: "Zephyr, volume ten." -> Claude (voice started after 2.6 s, done 5.1 s): Volume's at 10.
2026-10-27 22:57:44  recorded 3.0 s, ended by nothing said
2026-10-27 22:57:44  transcript (live, 0.72 s after you stopped): ""
2026-10-27 22:57:51  wake word (score 0.00, "Zephyr" spotted 0.86, checking transcript)
2026-10-27 22:57:54  recorded 3.5 s, ended by nothing said
2026-10-27 22:57:54  transcript (live, 0.12 s after you stopped): "Let her go home."
2026-10-27 22:57:54  diag 225754.wav: 7.5 s, transcript "Let her go home."
2026-10-27 22:57:54  name not in transcript (spotter 0.86); ignored
2026-10-27 22:58:02  wake word (score 0.00, "Zephyr" spotted 0.69, checking transcript)
2026-10-27 22:58:04  recorded 2.3 s, ended by pause
2026-10-27 22:58:04  not your voice (match 0.30 < 0.33); ignored
2026-10-27 22:58:10  live stream connect failed: Connection timed out
2026-10-27 22:58:20  wake word (score 0.00, "Zephyr" spotted 1.00, checking transcript)
2026-10-27 22:58:22  recorded 1.4 s, ended by sentence done
2026-10-27 22:58:22  transcript (live, 0.12 s after you stopped): "Zephyr go home."
2026-10-27 22:58:22  heard: "Zephyr go home." -> Going home.
2026-10-27 22:58:30  wake word (score 0.00, "Zephyr" spotted 1.00, checking transcript)
2026-10-27 22:58:32  recorded 2.1 s, ended by nothing said
2026-10-27 22:58:32  transcript (live, 0.12 s after you stopped): ""
2026-10-27 22:58:40  wake word (score 0.00, "Zephyr" spotted 1.00, checking transcript)'''.splitlines()


def test_requests_and_their_outcomes():
    requests, events = L.parse(LOG)
    by_said = {L.said(r): r for r in requests}
    volume = by_said['Zephyr, volume ten.']
    assert volume['status'] == 'done' and volume['kind'] == 'claude' and volume['voice'] == 0.96
    assert volume['tools'][0]['name'] == 'tv_volume'
    assert L.response_s(volume) == 6.6                         # 3.99 s transcript + 2.6 s to her voice
    ignored = by_said['Let her go home.']
    assert ignored['status'] == 'ignored' and ignored['clip'] == '225754.wav' and ignored['ended_by'] == 'nothing said'
    assert any(r['status'] == 'rejected' for r in requests)
    home = by_said['Zephyr go home.']
    assert home['status'] == 'done' and home['kind'] == 'command' and L.response_s(home) == 0.1
    assert requests[-2]['status'] == 'missed'                   # name, then nothing
    assert requests[-1]['status'] == 'listening'                # still going
    assert not any(r['follow_up'] for r in requests)            # the silent follow-up isn't a request
    assert [e['kind'] for e in events] == ['problem']


def test_stats():
    requests, _ = L.parse(LOG)
    s = L.stats(requests)
    assert s['counts']['done'] == 2 and s['counts']['ignored'] == 1 and s['median_reply_s'] == 6.6
    assert s['slow_recordings'] == 2
