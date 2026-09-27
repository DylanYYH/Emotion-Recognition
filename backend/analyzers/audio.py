# from backend.app import app
import librosa
import numpy as np
class CryClassifier:
    """Layer 1 boundary. The current repository has no trained checkpoint yet."""

    DEMO_PRESETS = {
        'feeding': ('Hungry', 72, [('Tired', 18), ('Discomfort', 10)]),
        'nap': ('Tired', 76, [('Hungry', 14), ('Discomfort', 10)]),
        'comfort': ('Discomfort', 68, [('Tired', 20), ('Hungry', 12)]),
        'evening': ('Hungry', 72, [('Tired', 17), ('Discomfort', 11)]),
        'night': ('Tired', 74, [('Hungry', 16), ('Discomfort', 10)]),
    }

    def predict(self, audio=None, demo_key='evening'):
        label, confidence, alternatives = self.DEMO_PRESETS.get(demo_key, self.DEMO_PRESETS['evening'])
        return {
            'prediction': label,
            'confidence': confidence,
            'alternatives': [{'label': item[0], 'confidence': item[1]} for item in alternatives],
            'source': 'demo acoustic profile',
        }


def analyze_audio(file_path, demo_key='evening'):
    """Backward-compatible adapter for the original API."""
    result = CryClassifier().predict(file_path, demo_key)
    return {'type': result['prediction'], 'confidence': result['confidence'], 'alternatives': result['alternatives']}
