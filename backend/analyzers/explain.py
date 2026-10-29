"""Turns a Layer 1 result into a short, plain-language "why"."""
import datetime

from .longitudinal import time_band

CAUSE_SOUND = {
    'Hungry': 'Hunger cries are often rhythmic and build up gradually.',
    'Tired': 'Tired cries are often whiny, lower in energy, with more pauses.',
    'Discomfort': 'Discomfort cries are often irregular and fussy.',
    'Pain': 'Research links pain cries to higher pitch and intensity than hunger or fussy cries (Parga et al., 2020).',
}


def _ago(delta):
    minutes = int(delta.total_seconds() // 60)
    return f'{minutes // 60}h {minutes % 60}m' if minutes >= 60 else f'{minutes} min'


def explain(result, name='your baby', baseline=None, stats=None, recent_events=None, now=None):
    now = now or datetime.datetime.now()
    cause = result['prediction']
    reasons = []
    detection = result.get('detection') or {}
    features = result.get('features') or {}

    if detection and not detection.get('cry_detected'):
        top = (detection.get('top_sounds') or [{}])[0].get('label', 'other sounds')
        reasons.append(f'FirstVoice did not hear a clear infant cry in this clip (loudest sound: {top}), so treat this result with extra caution.')

    if features.get('pitch_hz'):
        line = f"Pitch around {features['pitch_hz']} Hz, peaking near {features['pitch_peak_hz']} Hz"
        if baseline and baseline.get('pitch_hz'):
            ratio = features['pitch_hz'] / baseline['pitch_hz']
            if ratio > 1.15:
                line += f", higher than {name}'s usual {baseline['pitch_hz']} Hz"
            elif ratio < 0.87:
                line += f", lower than {name}'s usual {baseline['pitch_hz']} Hz"
            else:
                line += f", close to {name}'s usual {baseline['pitch_hz']} Hz"
        bursts, length, pauses = features.get('bursts_per_s', 0), features.get('burst_length_s', 0), features.get('pause_fraction', 0)
        if length >= 1.5:
            line += f'; long, continuous bursts (about {length:g} s each)'
        elif 0.5 <= bursts <= 2.0 and length < 1.0:
            line += f'; a rhythmic pattern of about {bursts:g} bursts per second'
        if pauses > 0.55:
            line += f'; quiet gaps {round(pauses * 100)}% of the time'
        reasons.append(line + '. ' + CAUSE_SOUND.get(cause, ''))
    elif cause in CAUSE_SOUND:
        reasons.append(CAUSE_SOUND[cause])

    personal = result.get('personalization') or {}
    if personal.get('examples'):
        general = personal.get('global_probabilities', {})
        general_top = max(general, key=general.get) if general else None
        own = personal['examples_by_cause'].get(cause, 0)
        line = f"{name}'s history ({personal['examples']} reviewed cries, {own} of them {cause.lower()})"
        if personal.get('changed_prediction') and general_top:
            line += f' changed the answer from {general_top} to {cause}.'
        else:
            line += f' supports this: {round(general.get(cause, 0) * 100)}% from the general model, {result["confidence"]}% after personalization.'
        if personal.get('nearest_cause'):
            line += f" The cry sounds most like {name}'s earlier {personal['nearest_cause'].lower()} cries."
        reasons.append(line)
    elif result.get('probabilities'):
        reasons.append(f"No feedback from you yet, so this comes from the general model alone. Confirming or correcting results teaches FirstVoice {name}'s cries.")

    band = time_band(now.hour)
    if stats:
        peak = stats.get('peak_time_band_by_cause', {}).get(cause)
        if peak == band:
            reasons.append(f'{cause} cries most often happen around {band} for {name}, and it is {band.split("-")[0].strip()}-ish now.')
    if recent_events:
        same = [e for e in recent_events if e['label'] == cause and e.get('timestamp')]
        if same:
            last = max(datetime.datetime.fromisoformat(e['timestamp']) for e in same)
            if now - last > datetime.timedelta(minutes=2):
                reasons.append(f'The last {cause.lower()} cry was {_ago(now - last)} ago.')
    return reasons[:5]
