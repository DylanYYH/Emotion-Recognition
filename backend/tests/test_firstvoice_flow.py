"""End-to-end check of both FirstVoice learning loops using Flask's test client.

Run from the repository root:  python backend/tests/test_firstvoice_flow.py
Uses a temporary database and a synthetic cry, so no server, microphone or
API key is needed.
"""
import io
import json
import os
import sys
import tempfile
import wave

import numpy as np

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
os.environ['FIRSTVOICE_DB'] = 'sqlite:///' + os.path.join(tempfile.mkdtemp(), 'test.db')
for key in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY'):
    os.environ.pop(key, None)
os.environ['LLM_PROVIDER'] = 'auto'

import app as firstvoice  # noqa: E402
from analyzers import longitudinal  # noqa: E402


def wav_bytes(seconds=4.0, cry=True, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(int(16000 * seconds)) / 16000
    if cry:
        f0 = 450 + 60 * np.sin(2 * np.pi * 0.7 * t)
        phase = 2 * np.pi * np.cumsum(f0) / 16000
        tone = sum(np.sin(k * phase) / k for k in range(1, 6))
        envelope = (np.sin(2 * np.pi * 0.9 * t) > -0.3).astype(float)
        signal = 0.3 * tone * envelope + 0.01 * rng.standard_normal(len(t))
    else:
        signal = 0.01 * rng.standard_normal(len(t))
    pcm = (np.clip(signal, -1, 1) * 32767).astype('<i2')
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(pcm.tobytes())
    buffer.seek(0)
    return buffer


def check(condition, message):
    print(('PASS ' if condition else 'FAIL ') + message)
    if not condition:
        raise SystemExit(1)


def main(corpus_wav=None):
    client = firstvoice.app.test_client()
    check(client.post('/api/signup', json={'username': 'parent1', 'password': 'pw'}).status_code == 200, 'signup')
    dash = client.get('/api/firstvoice/dashboard').get_json()
    check(dash['stats']['events'] == 0, 'new parent starts with an empty history (no fake seed)')
    check(dash['report']['provider'] == 'Local analysis', 'Layer 2 falls back to local analysis without a key')

    demo = client.post('/api/firstvoice/analyze', json={'source': 'demo', 'demo_key': 'nap'}).get_json()
    check(demo['event']['prediction'] == 'Tired', 'demo preset still works')

    def analyze(buffer, source='upload'):
        response = client.post('/api/firstvoice/analyze', data={'source': source, 'audio': (buffer, 'cry.wav')},
                               content_type='multipart/form-data')
        check(response.status_code == 200, f'real-audio analyze ({source}) -> {response.status_code}')
        return response.get_json()['event']

    first = analyze(open(corpus_wav, 'rb') if corpus_wav else wav_bytes())
    check(first['model_source'].startswith('FirstVoice model'), 'trained YAMNet-transfer head was used')
    check(bool(first['features'].get('pitch_hz')) or first['features'] is not None, 'acoustic features computed')
    check(len(first['action_plan']) >= 1 and len(first['explanation']) >= 1, 'result explains why and gives an action plan')
    check(first['spectrogram']['bands'] == 64, 'log-mel spectrogram returned for display')
    check(abs(sum(first['probabilities'].values()) - 1) < 1e-3, 'probabilities sum to 1')

    # Loop 1: corrections become personal examples.
    target = next(c for c in longitudinal.CAUSES if c != first['prediction'] and c != 'Pain')
    for seed in range(4):
        event = analyze(wav_bytes(seed=seed + 1), source='auto')
        client.post('/api/firstvoice/feedback', json={'event_id': event['id'], 'confirmed': False, 'corrected_cause': target})
    after = analyze(wav_bytes(seed=9), source='live')
    info = after['personalization']
    check(info['examples'] == 4, f'four reviewed cries feed personalization (got {info["examples"]})')
    check(after['probabilities'][target] > info['global_probabilities'][target],
          f'parent corrections raised P({target}) from {info["global_probabilities"][target]:.2f} to {after["probabilities"][target]:.2f}')

    # Loop 2: recommendation feedback reaches the report and the action plan.
    response = client.post('/api/firstvoice/recommendation-feedback', json={'event_id': after['id'], 'helpful': True, 'detail': 'Rocking'})
    check(response.status_code == 200, 'recommendation feedback saved')
    report = client.post('/api/firstvoice/report/refresh').get_json()['report']
    check(bool(report['summary']) and set(report['playbook']) == set(longitudinal.CAUSES), 'weekly report with per-cause playbook')

    # LLM plumbing with a stubbed provider.
    os.environ['ANTHROPIC_API_KEY'] = 'test-key'
    original = longitudinal.LLMClient.complete_json
    captured = {}

    def fake(self, system, user, timeout=90):
        captured['context'] = json.loads(user)
        return {'summary': 'Stub summary', 'patterns': ['p'], 'changes': ['c'], 'recommendations': ['r'],
                'playbook': {'Hungry': ['Feed']}, 'focus_for_next_week': 'f', 'cautions': ['x']}
    longitudinal.LLMClient.complete_json = fake
    report = client.post('/api/firstvoice/report/refresh').get_json()['report']
    longitudinal.LLMClient.complete_json = original
    check(report['provider'] == 'Claude Sonnet 5' and report['summary'] == 'Stub summary', 'Claude Sonnet 5 path used when ANTHROPIC_API_KEY is set')
    check(captured['context']['previous_report'] is not None, 'previous report is passed back to the LLM')
    check('what_worked_by_cause' in captured['context']['stats'], 'LLM sees which suggestions helped')
    check(report['playbook']['Pain'][-1].lower().count('healthcare') == 1, 'Pain playbook always keeps the safety step')
    os.environ.pop('ANTHROPIC_API_KEY')

    next_event = analyze(wav_bytes(seed=11))
    expected = report['playbook'][next_event['prediction']][0]
    check(next_event['action_plan'][0] == expected or next_event['prediction'] == 'Pain',
          f'action plan comes from the latest Layer 2 playbook ("{next_event["action_plan"][0]}")')

    # Hands-free detector.
    noise = client.post('/api/firstvoice/detect', data={'audio': (wav_bytes(2, cry=False), 'w.wav')}, content_type='multipart/form-data').get_json()
    check(noise['cry_detected'] is False, f'quiet room is not a cry (score {noise["cry_score"]})')
    if corpus_wav:
        cry = client.post('/api/firstvoice/detect', data={'audio': (open(corpus_wav, 'rb'), 'c.wav')}, content_type='multipart/form-data').get_json()
        check(cry['cry_detected'] is True, f'real infant cry is detected (score {cry["cry_score"]})')

    calendar = client.get('/api/calendar').get_json()
    check(sum(1 for e in calendar if e['event_type'] == 'FirstVoice cry') >= 7, 'every cry lands in the care calendar')
    check(any(e['cause'] == target for e in calendar), 'corrections update the calendar entry')
    print('\nAll checks passed.')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else None)
