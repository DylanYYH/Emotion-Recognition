"""Layer 2: "What can we learn from this baby's patterns over time?"

An LLM (Claude Sonnet 5 by default, or an OpenAI model) reads the baby's
accumulated care history: age, time of day, how often each cry type shows
up, parent confirmations and corrections, which suggestions helped, and the
manual care calendar. It writes a weekly summary and a per-cause playbook.

Feedback loop 2: the playbook is used for the action plan on every new cry,
parents rate those suggestions, the ratings go into the next report, and the
previous report is passed back in so the model can build on it.

Without an API key the same structure is produced by a local rule-based
analysis, so the app always works offline.
"""
import datetime
import json
import os
import urllib.error
import urllib.request
from collections import Counter, defaultdict

CAUSES = ['Hungry', 'Tired', 'Discomfort', 'Pain']

DEFAULT_PLAYBOOK = {
    'Hungry': ['Offer a feed (breast or bottle) and watch for rooting or hand-to-mouth cues.',
               'Pause halfway to burp.',
               'If they refuse the feed, check for tiredness or discomfort next.'],
    'Tired': ['Dim the lights and lower noise and stimulation.',
              'Swaddle or hold close and rock slowly; try white noise.',
              'Settle them for a nap at the first yawns or eye rubbing.'],
    'Discomfort': ['Check the diaper and change it if needed.',
                   'Check clothing, room temperature, and position; try burping or gentle tummy massage.',
                   'Hold upright against your chest for a few minutes.'],
    'Pain': ['Stay with your baby and look for fever, vomiting, a rash, or anything that seems unusual.',
             'Try gentle holding and skin-to-skin contact.',
             'If the crying is unusual, high-pitched and won\'t settle, or you are worried, contact a healthcare professional now.'],
}

ACTION_KEYWORDS = {
    'Feeding': ('feed', 'breast', 'bottle'),
    'Burping': ('burp',),
    'Rocking': ('rock',),
    'Changing diaper': ('diaper',),
    'Nap': ('nap', 'sleep'),
    'Skin-to-skin': ('skin-to-skin',),
    'White noise': ('white noise',),
    'Swaddling': ('swaddl',),
}

PAIN_SAFETY = ('If the crying is unusual, high-pitched and won\'t settle, or your baby has a fever, '
               'contact a healthcare professional.')

SYSTEM_PROMPT = """You are the longitudinal care analyst inside FirstVoice, an app that helps parents of infants (0-18 months) understand crying.
Layer 1 of FirstVoice classifies each cry as Hungry, Tired, Discomfort or Pain and parents confirm or correct it. You are Layer 2: you read this one baby's accumulated history and help the parent see patterns over days and weeks.

Rules:
- Base every statement on the data provided. Quote concrete numbers and times of day. Do not invent events.
- Use probabilistic, non-diagnostic language ("most often", "may", "seems to help"). Never diagnose a medical condition.
- Prefer parent-confirmed labels over raw classifier predictions. Note when the parent often corrects a cause.
- Use the recommendation feedback: put actions that helped this baby first, and drop or demote ones that did not.
- Build on the previous report if one is given: say what changed since then.
- Consider the baby's age in months when you interpret patterns (typical feeding and sleep needs shift between 0 and 18 months).
- If there are Pain-labelled cries, or anything unusual, include a caution to contact a healthcare professional when worried.
- Keep it short and warm. Parents are tired.

Respond with a single JSON object and nothing else, with these keys:
{
  "summary": "2-4 sentences: the week in plain words",
  "patterns": ["3-5 specific patterns with numbers/times"],
  "changes": ["1-3 changes compared with earlier weeks or the previous report"],
  "recommendations": ["3-5 concrete suggestions for next week"],
  "playbook": {"Hungry": ["up to 3 short steps, best first"], "Tired": [...], "Discomfort": [...], "Pain": [...]},
  "focus_for_next_week": "one sentence",
  "cautions": ["1-2 short safety notes"]
}"""


def time_band(hour):
    bands = [(0, 3, '12-3 AM'), (3, 6, '3-6 AM'), (6, 9, '6-9 AM'), (9, 12, '9 AM-12 PM'),
             (12, 15, '12-3 PM'), (15, 18, '3-6 PM'), (18, 21, '6-9 PM'), (21, 24, '9 PM-12 AM')]
    for start, end, label in bands:
        if start <= hour < end:
            return label
    return 'unknown'


def _parse_time(value):
    if isinstance(value, datetime.datetime):
        return value
    try:
        return datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None


def action_from_text(text):
    text = (text or '').lower()
    for action, keywords in ACTION_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return action
    return None


def compute_stats(events, now=None):
    """events: dicts with timestamp, label, predicted, feedback_confirmed,
    recommendation_feedback, recommendation_detail, confidence."""
    now = now or datetime.datetime.now()
    week_ago, month_ago = now - datetime.timedelta(days=7), now - datetime.timedelta(days=28)
    this_week, earlier = [], []
    hours_by_cause = defaultdict(Counter)
    worked = defaultdict(lambda: defaultdict(lambda: {'helpful': 0, 'not_helpful': 0}))
    corrections = Counter()
    confirmed = corrected = 0
    for event in events:
        ts = _parse_time(event.get('timestamp'))
        if ts is None:
            continue
        label = event['label']
        if ts >= week_ago:
            this_week.append(event)
        elif ts >= month_ago:
            earlier.append(event)
        hours_by_cause[label][time_band(ts.hour)] += 1
        if event.get('feedback_confirmed') is True:
            confirmed += 1
        elif event.get('feedback_confirmed') is False:
            corrected += 1
            corrections[f"{event.get('predicted')} -> {label}"] += 1
        if event.get('recommendation_feedback') is not None:
            action = event.get('recommendation_detail') or action_from_text(event.get('recommendation')) or 'Suggested routine'
            key = 'helpful' if event['recommendation_feedback'] else 'not_helpful'
            worked[label][action][key] += 1
    weeks_earlier = 3.0
    return {
        'total_events': len(events),
        'this_week': {
            'events': len(this_week),
            'by_cause': dict(Counter(e['label'] for e in this_week)),
            'by_time_band': dict(Counter(time_band(_parse_time(e['timestamp']).hour) for e in this_week)),
        },
        'previous_3_weeks_weekly_average': {
            'events': round(len(earlier) / weeks_earlier, 1),
            'by_cause': {k: round(v / weeks_earlier, 1) for k, v in Counter(e['label'] for e in earlier).items()},
        },
        'all_time_by_cause': dict(Counter(e['label'] for e in events)),
        'peak_time_band_by_cause': {cause: bands.most_common(1)[0][0] for cause, bands in hours_by_cause.items() if bands},
        'time_bands_by_cause': {cause: dict(bands) for cause, bands in hours_by_cause.items()},
        'parent_feedback': {'confirmed': confirmed, 'corrected': corrected,
                            'agreement_rate': round(confirmed / (confirmed + corrected), 2) if confirmed + corrected else None,
                            'common_corrections': dict(corrections.most_common(4))},
        'what_worked_by_cause': {cause: {action: dict(v) for action, v in actions.items()} for cause, actions in worked.items()},
    }


def build_context(profile, events, care_entries=None, previous_report=None, now=None):
    now = now or datetime.datetime.now()
    recent = sorted(events, key=lambda e: e['timestamp'])[-40:]
    return {
        'today': now.strftime('%Y-%m-%d %A'),
        'baby': {'name': profile.get('name'), 'age_months': profile.get('age_months'),
                 'feeding_interval': profile.get('feeding_interval') or None},
        'stats': compute_stats(events, now),
        'recent_cries': [{
            'when': _parse_time(e['timestamp']).strftime('%a %H:%M'),
            'cause': e['label'], 'model_said': e.get('predicted'),
            'parent': {True: 'confirmed', False: 'corrected', None: 'no feedback'}[e.get('feedback_confirmed')],
            'tried': e.get('recommendation_detail'),
            'helped': e.get('recommendation_feedback'),
        } for e in recent if _parse_time(e.get('timestamp'))],
        'care_calendar_entries_last_7_days': (care_entries or [])[-30:],
        'previous_report': {k: previous_report.get(k) for k in ('generated_at', 'summary', 'recommendations', 'playbook', 'focus_for_next_week')} if previous_report else None,
    }


class LLMClient:
    """Minimal HTTP client for Anthropic or OpenAI. No SDK dependency."""

    def __init__(self):
        self.provider, self.model = self._select()

    @staticmethod
    def _select():
        preference = os.getenv('LLM_PROVIDER', 'auto').lower()
        has_anthropic, has_openai = bool(os.getenv('ANTHROPIC_API_KEY')), bool(os.getenv('OPENAI_API_KEY'))
        if preference == 'anthropic' and has_anthropic or preference == 'auto' and has_anthropic:
            return 'anthropic', os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-5')
        if preference in ('openai', 'auto') and has_openai:
            return 'openai', os.getenv('OPENAI_MODEL', 'gpt-5.4-mini')
        return None, None

    @property
    def configured(self):
        return self.provider is not None

    @property
    def display_name(self):
        if self.provider == 'anthropic':
            return 'Claude Sonnet 5' if self.model == 'claude-sonnet-5' else self.model
        if self.provider == 'openai':
            return self.model
        return 'Local analysis'

    def complete_json(self, system, user, timeout=90):
        if self.provider == 'anthropic':
            body = {'model': self.model, 'max_tokens': 2000, 'system': system,
                    'messages': [{'role': 'user', 'content': user}]}
            request = urllib.request.Request(
                'https://api.anthropic.com/v1/messages', data=json.dumps(body).encode(), method='POST',
                headers={'x-api-key': os.getenv('ANTHROPIC_API_KEY'), 'anthropic-version': '2023-06-01',
                         'content-type': 'application/json'})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read())
            text = ''.join(block.get('text', '') for block in data.get('content', []) if block.get('type') == 'text')
        elif self.provider == 'openai':
            body = {'model': self.model, 'instructions': system, 'input': user,
                    'max_output_tokens': 4000, 'text': {'format': {'type': 'json_object'}}}
            request = urllib.request.Request(
                'https://api.openai.com/v1/responses', data=json.dumps(body).encode(), method='POST',
                headers={'Authorization': f"Bearer {os.getenv('OPENAI_API_KEY')}", 'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read())
            text = ''.join(part.get('text', '') for item in data.get('output', [])
                           for part in item.get('content', []) or [] if part.get('type') == 'output_text')
        else:
            raise RuntimeError('No LLM provider configured')
        start, end = text.find('{'), text.rfind('}')
        if start < 0 or end < 0:
            raise ValueError('The model did not return JSON')
        return json.loads(text[start:end + 1])


def _clean_report(raw):
    def strings(value, limit):
        if isinstance(value, str):
            value = [value]
        return [str(v).strip() for v in (value or []) if str(v).strip()][:limit]
    playbook = {}
    for cause in CAUSES:
        steps = strings((raw.get('playbook') or {}).get(cause), 3)
        playbook[cause] = steps or DEFAULT_PLAYBOOK[cause]
    if PAIN_SAFETY not in ' '.join(playbook['Pain']) and 'healthcare' not in ' '.join(playbook['Pain']).lower():
        playbook['Pain'] = playbook['Pain'][:2] + [PAIN_SAFETY]
    return {
        'summary': str(raw.get('summary') or '').strip(),
        'patterns': strings(raw.get('patterns'), 5),
        'changes': strings(raw.get('changes'), 3),
        'recommendations': strings(raw.get('recommendations'), 5),
        'playbook': playbook,
        'focus_for_next_week': str(raw.get('focus_for_next_week') or '').strip(),
        'cautions': strings(raw.get('cautions'), 2) or ['FirstVoice offers possible reasons, not a diagnosis.'],
    }


class LongitudinalAnalyzer:
    def __init__(self):
        self.llm = LLMClient()

    def refresh_provider(self):
        self.llm = LLMClient()
        return self.llm

    # ---- weekly report ---------------------------------------------------
    def generate_report(self, context):
        llm = self.refresh_provider()
        now = datetime.datetime.now().isoformat(timespec='seconds')
        if llm.configured and context['stats']['total_events']:
            try:
                raw = llm.complete_json(SYSTEM_PROMPT, json.dumps(context, indent=1, default=str))
                report = _clean_report(raw)
                if report['summary']:
                    return report | {'provider': llm.display_name, 'provider_kind': llm.provider,
                                     'model': llm.model, 'generated_at': now}
                error = 'empty summary'
            except urllib.error.HTTPError as exc:
                error = f'HTTP {exc.code}: {exc.read()[:300].decode(errors="ignore")}'
            except Exception as exc:  # network, JSON, timeout
                error = str(exc)[:300]
            return self.local_report(context) | {'provider': f'Local analysis ({llm.display_name} unavailable)',
                                                 'provider_kind': 'local', 'provider_error': error, 'generated_at': now}
        return self.local_report(context) | {'provider': 'Local analysis', 'provider_kind': 'local', 'generated_at': now}

    # Kept for older callers.
    def generate_weekly_summary(self, history, age_months):
        events = [{'timestamp': h['timestamp'], 'label': h['label'], 'feedback_confirmed': h.get('feedback_confirmed'),
                   'recommendation_feedback': h.get('recommendation_feedback')} for h in history if h.get('timestamp')]
        return self.local_report(build_context({'name': 'your baby', 'age_months': age_months}, events))

    def local_report(self, context):
        stats, name = context['stats'], context['baby'].get('name') or 'your baby'
        age = context['baby'].get('age_months')
        if not stats['total_events']:
            return _clean_report({
                'summary': f'FirstVoice is just starting to learn {name}\'s cries. Analyze a few cries and confirm or correct the results to build a personal history.',
                'patterns': ['No recurring pattern yet'],
                'changes': ['A baseline appears after the first week of cries'],
                'recommendations': ['Turn on hands-free listening near the crib', 'Confirm or correct each result so FirstVoice can learn'],
                'playbook': {}, 'focus_for_next_week': 'Collect a first week of confirmed cries.',
            })
        week, previous = stats['this_week'], stats['previous_3_weeks_weekly_average']
        by_cause = week['by_cause'] or stats['all_time_by_cause']
        top_cause, top_count = Counter(by_cause).most_common(1)[0]
        peak_band = Counter(week['by_time_band']).most_common(1)[0][0] if week['by_time_band'] else None
        patterns = [f'{top_cause} was the most common cause this week ({top_count} of {week["events"] or stats["total_events"]} cries).']
        for cause, band in stats['peak_time_band_by_cause'].items():
            count = stats['time_bands_by_cause'][cause][band]
            if count >= 2:
                patterns.append(f'{cause} cries cluster around {band} ({count} so far).')
        feedback = stats['parent_feedback']
        if feedback['agreement_rate'] is not None:
            patterns.append(f'You agreed with FirstVoice on {round(feedback["agreement_rate"] * 100)}% of the cries you reviewed.')
        changes = []
        if previous['events']:
            delta = week['events'] - previous['events']
            direction = 'more' if delta > 0 else 'fewer'
            changes.append(f'{abs(round(delta))} {direction} cries than the average of the previous three weeks ({previous["events"]}/week).' if delta else 'About the same number of cries as the previous three weeks.')
        for correction, count in feedback['common_corrections'].items():
            changes.append(f'You corrected "{correction}" {count} time(s); FirstVoice now weights {name}\'s own history more.')
            break
        playbook = {cause: self.rank_steps(cause, DEFAULT_PLAYBOOK[cause], stats) for cause in CAUSES}
        recommendations = [playbook[top_cause][0]]
        if peak_band:
            recommendations.append(f'Plan ahead for {peak_band}, the busiest window this week.')
        best = self.best_action(top_cause, stats)
        if best:
            recommendations.append(f'{best} has helped most often when {name} cries from {top_cause.lower()}; try it first.')
        cautions = ['FirstVoice offers possible reasons, not a diagnosis.']
        if stats['all_time_by_cause'].get('Pain'):
            cautions.append(PAIN_SAFETY)
        return _clean_report({
            'summary': f'In the last 7 days FirstVoice recorded {week["events"]} cries for {name}' + (f' ({age:g} months)' if age is not None else '') +
                       f'. {top_cause} was the most common cause' + (f', and crying peaked around {peak_band}.' if peak_band else '.'),
            'patterns': patterns, 'changes': changes or ['Not enough earlier history to compare yet.'],
            'recommendations': recommendations, 'playbook': playbook,
            'focus_for_next_week': f'Keep confirming results so the {top_cause.lower()} pattern can be checked against next week.',
            'cautions': cautions,
        })

    # ---- action plans (used by Layer 1 results) -------------------------
    @staticmethod
    def best_action(cause, stats):
        actions = stats.get('what_worked_by_cause', {}).get(cause, {})
        scored = [(v['helpful'] - v['not_helpful'], v['helpful'], a) for a, v in actions.items() if v['helpful']]
        return max(scored)[2] if scored else None

    @staticmethod
    def rank_steps(cause, steps, stats):
        actions = stats.get('what_worked_by_cause', {}).get(cause, {})
        def score(step):
            action = action_from_text(step)
            if not action or action not in actions:
                return 0
            return actions[action]['helpful'] - actions[action]['not_helpful']
        return sorted(steps, key=score, reverse=True)

    def action_plan(self, cause, stats=None, playbook=None):
        steps = list((playbook or {}).get(cause) or DEFAULT_PLAYBOOK.get(cause) or ['Try a familiar soothing routine and watch how your baby responds.'])
        if stats:
            steps = self.rank_steps(cause, steps, stats)
        if cause == 'Pain' and not any('healthcare' in s.lower() for s in steps):
            steps = steps[:2] + [PAIN_SAFETY]
        return steps[:3]

    def recommendation_for(self, label, history=None):
        return self.action_plan(label)[0]
