import json
import os
import urllib.request
from collections import Counter
from datetime import datetime, timedelta


CAUSE_RECOMMENDATIONS = {
    'Hungry': 'Try a calm feeding check, then pause to burp and notice whether Luca settles.',
    'Tired': 'Try a short wind-down: dim the room, reduce stimulation, and offer a familiar nap cue.',
    'Discomfort': 'Check the diaper, clothing, temperature, and position before trying a soothing routine.',
    'Pain': 'Pause the demo routine and contact a healthcare professional if you are concerned about pain or unusual symptoms.',
}


class LongitudinalAnalyzer:
    """Layer 2: turns structured infant history into a report and next-step suggestion."""

    def _local_report(self, history, age_months):
        if not history:
            return {
                'summary': 'FirstVoice is just beginning to learn Luca’s pattern. Confirm a few classifications to start a personalized history.',
                'patterns': ['No recurring pattern yet'],
                'changes': ['A baseline will appear after the first few events'],
                'recommendations': ['Use Demo Cry to walk through a classification and parent confirmation'],
                'cautions': ['AI-generated insights are informational and are not a medical diagnosis.'],
            }
        causes = Counter(item['label'] for item in history)
        peak = Counter(item.get('time_of_day', 'daytime') for item in history)
        top_cause, top_count = causes.most_common(1)[0]
        peak_period, peak_count = peak.most_common(1)[0]
        confirmed = [item for item in history if item.get('feedback_confirmed') is True]
        corrected = [item for item in history if item.get('feedback_confirmed') is False]
        helpful = [item for item in history if item.get('recommendation_feedback') is True]
        return {
            'summary': f"Across {len(history)} recorded events at {age_months:g} months, {top_cause.lower()} is the most common pattern ({top_count} events), with the strongest signal around {peak_period}." + (f" Parent feedback has confirmed {len(confirmed)} classifications and corrected {len(corrected)}." if confirmed else ''),
            'patterns': [f'{top_cause} appears most often in this history', f'{peak_period} is the busiest period ({peak_count} events)', f'{len(helpful)} recommendation responses have been marked helpful'],
            'changes': ['The demo timeline compares accumulated history as Luca grows', 'Parent corrections are kept separate from classifier confidence'],
            'recommendations': [CAUSE_RECOMMENDATIONS.get(top_cause, 'Try a familiar calming routine and observe Luca’s response.')],
            'cautions': ['This prototype offers possible next steps, not a diagnosis.', 'For concerning or unusual symptoms, contact a healthcare professional.'],
        }

    def generate_weekly_summary(self, history, age_months):
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            return self._local_report(history, age_months) | {'provider': 'demo longitudinal analysis'}
        payload = {'model': os.getenv('OPENAI_MODEL', 'gpt-4o-mini'), 'input': [{'role': 'system', 'content': 'Return JSON with summary, patterns, changes, recommendations, cautions. Analyze infant history without diagnosing or making medical claims.'}, {'role': 'user', 'content': json.dumps({'age_months': age_months, 'history': history})}], 'text': {'format': {'type': 'json_object'}}}
        request = urllib.request.Request('https://api.openai.com/v1/responses', data=json.dumps(payload).encode('utf-8'), headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                body = json.loads(response.read().decode('utf-8'))
            text = ''.join(part.get('text', '') for item in body.get('output', []) for part in item.get('content', []) if part.get('type') == 'output_text')
            return json.loads(text) | {'provider': 'configured LLM'}
        except Exception:
            return self._local_report(history, age_months) | {'provider': 'demo fallback (LLM unavailable)'}

    def recommendation_for(self, label, history):
        return CAUSE_RECOMMENDATIONS.get(label, 'Try a familiar soothing routine and observe how Luca responds.')


def build_demo_history(age_months):
    """Simulated longitudinal events make the 0–18 month story visible without fake accuracy claims."""
    count = max(0, round(age_months * 23))
    labels = ['Hungry', 'Hungry', 'Tired', 'Hungry', 'Discomfort', 'Tired', 'Hungry']
    periods = ['morning', 'afternoon', '6–9 PM', '6–9 PM', 'night']
    history = []
    start = datetime.now() - timedelta(days=max(1, round(age_months * 30)))
    for index in range(count):
        label = labels[index % len(labels)]
        history.append({'label': label, 'time_of_day': periods[index % len(periods)], 'feedback_confirmed': index % 5 != 0, 'recommendation_feedback': index % 4 != 0, 'timestamp': (start + timedelta(hours=index * 7)).isoformat()})
    return history
