"""Layer 1: "What might this cry mean right now?"

Pipeline for real audio:
  1. Sound event detection with YAMNet: is there an infant cry in the clip?
  2. log-Mel spectrogram -> YAMNet embeddings (transfer learning base)
     + MFCC / pitch / energy / rhythm features.
  3. Trained softmax head (train_cry_model.py) -> global cause probabilities.
  4. Per-baby personalization from parent feedback (personalization.py).

The scripted demo presets remain available for presentations.
"""
import json
import os

import numpy as np

from .acoustics import FEATURE_NAMES, acoustic_features, load_audio, spectrogram_thumbnail
from .dataset import LABELS
from .yamnet import SAMPLE_RATE, get_yamnet

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'models')
HEAD_NPZ = os.path.join(MODELS_DIR, 'cry_head.npz')
HEAD_JSON = os.path.join(MODELS_DIR, 'cry_head.json')

# Absolute loudness depends on the microphone and its distance from the crib,
# so the head ignores mfcc0 (overall level) and absolute dB values.
_EXCLUDED = {'mfcc0_mean', 'energy_mean_db', 'energy_peak_db'}
MODEL_FEATURE_INDEX = [i for i, name in enumerate(FEATURE_NAMES) if name not in _EXCLUDED]

CRY_PATCH_THRESHOLD = 0.15   # per 0.96 s patch
CRY_CLIP_THRESHOLD = 0.12    # clip-level score needed to call it a cry


def clip_embedding(frame_embeddings, frame_cry_scores):
    """Cry-weighted mean of YAMNet patch embeddings (crying patches count most)."""
    weights = np.asarray(frame_cry_scores, dtype=np.float32) + 0.05
    return (np.asarray(frame_embeddings) * weights[:, None]).sum(axis=0) / weights.sum()


def _softmax(logits):
    logits = logits - logits.max()
    exp = np.exp(logits)
    return exp / exp.sum()


class CryClassifier:
    DEMO_PRESETS = {
        'feeding': ('Hungry', 72, [('Tired', 18), ('Discomfort', 10)]),
        'nap': ('Tired', 76, [('Hungry', 14), ('Discomfort', 10)]),
        'comfort': ('Discomfort', 68, [('Tired', 20), ('Hungry', 12)]),
        'evening': ('Hungry', 72, [('Tired', 17), ('Discomfort', 11)]),
        'night': ('Tired', 74, [('Hungry', 16), ('Discomfort', 10)]),
    }

    def __init__(self):
        self.yamnet = get_yamnet()
        self._head = None
        self._meta = None
        self._head_mtime = None

    # ---- model loading -------------------------------------------------
    def _load_head(self):
        if not os.path.isfile(HEAD_NPZ):
            return None
        mtime = os.path.getmtime(HEAD_NPZ)
        if self._head is None or mtime != self._head_mtime:
            data = np.load(HEAD_NPZ)
            self._head = {key: data[key] for key in data.files}
            self._head_mtime = mtime
            try:
                with open(HEAD_JSON) as handle:
                    self._meta = json.load(handle)
            except (OSError, ValueError):
                self._meta = {}
        return self._head

    @property
    def trained(self):
        return self._load_head() is not None

    def model_info(self):
        head = self._load_head()
        if head is None:
            return {'trained': False, 'message': 'Run python backend/train_cry_model.py to train the Layer 1 head.'}
        meta = self._meta or {}
        evaluation = meta.get('evaluation', {})
        return {
            'trained': True,
            'architecture': meta.get('architecture'),
            'dataset': meta.get('dataset'),
            'class_counts': meta.get('class_counts'),
            'balanced_accuracy': evaluation.get('balanced_accuracy'),
            'macro_f1': evaluation.get('macro_f1'),
            'chance': evaluation.get('chance_balanced_accuracy'),
            'recall_per_class': evaluation.get('recall_per_class'),
            'protocol': evaluation.get('protocol'),
            'personalization_replay': evaluation.get('personalization_replay'),
            'trained_at': meta.get('trained_at'),
        }

    # ---- stage 1: sound event detection --------------------------------
    def detect(self, wav):
        scores, embeddings, log_mel = self.yamnet.run(wav)
        cry = self.yamnet.cry_scores(scores)
        return self._detection_summary(scores, cry), (scores, embeddings, log_mel, cry)

    def _detection_summary(self, scores, cry):
        top = float(np.sort(cry)[-2:].mean()) if len(cry) else 0.0
        crying_patches = int((cry >= CRY_PATCH_THRESHOLD).sum())
        return {
            'cry_detected': bool(top >= CRY_CLIP_THRESHOLD),
            'cry_score': round(top, 3),
            'crying_seconds': round(crying_patches * 0.48, 1),
            'top_sounds': self.yamnet.top_classes(scores, 3),
        }

    # ---- stages 2-3: features + head -----------------------------------
    def analyze_waveform(self, wav):
        detection, (scores, embeddings, log_mel, cry) = self.detect(wav)
        vector, summary = acoustic_features(wav, log_mel)
        embedding = clip_embedding(embeddings, cry)
        head = self._load_head()
        if head is not None:
            x = np.concatenate([embedding, vector[MODEL_FEATURE_INDEX]])
            z = (x - head['mean']) / head['std']
            probs = _softmax(z @ head['weights'] + head['bias'])
            labels = (self._meta or {}).get('labels', LABELS)
            global_probs = {label: float(p) for label, p in zip(labels, probs)}
            source = 'FirstVoice model (YAMNet transfer learning)'
        else:
            global_probs = self._heuristic_probs(summary)
            source = 'acoustic heuristic (model not trained yet)'
        return {
            'detection': detection,
            'global_probabilities': global_probs,
            'features': summary,
            'spectrogram': spectrogram_thumbnail(log_mel),
            'embedding': embedding.astype(np.float32),
            'acoustic_vector': vector.astype(np.float32),
            'source': source,
        }

    @staticmethod
    def _heuristic_probs(summary):
        """Rough literature-based fallback used only before training."""
        pitch, bursts = summary['pitch_hz'], summary['bursts_per_s']
        score = {'Hungry': 1.0 + (0.6 if 0.6 <= bursts <= 1.6 else 0), 'Tired': 1.0 + (0.5 if summary['pause_fraction'] > 0.5 else 0),
                 'Discomfort': 1.0, 'Pain': 0.6 + (0.9 if pitch > 550 and summary['burst_length_s'] > 1.0 else 0)}
        total = sum(score.values())
        return {k: v / total for k, v in score.items()}

    # ---- public API ------------------------------------------------------
    def predict(self, audio=None, demo_key='evening', personalizer=None):
        """audio: None (demo preset), a file path, or a 16 kHz float waveform."""
        if audio is None:
            label, confidence, alternatives = self.DEMO_PRESETS.get(demo_key, self.DEMO_PRESETS['evening'])
            return {
                'prediction': label,
                'confidence': confidence,
                'alternatives': [{'label': item[0], 'confidence': item[1]} for item in alternatives],
                'source': 'demo preset',
                'probabilities': None,
            }
        wav = load_audio(audio) if isinstance(audio, str) else np.asarray(audio, dtype=np.float32)
        analysis = self.analyze_waveform(wav)
        probs = analysis['global_probabilities']
        personal_info = None
        if personalizer is not None:
            probs, personal_info = personalizer(probs, analysis['embedding'])
        ranked = sorted(probs.items(), key=lambda item: item[1], reverse=True)
        analysis.update({
            'prediction': ranked[0][0],
            'confidence': round(ranked[0][1] * 100),
            'alternatives': [{'label': label, 'confidence': round(p * 100)} for label, p in ranked[1:]],
            'probabilities': {label: round(p, 4) for label, p in probs.items()},
            'personalization': personal_info,
        })
        return analysis


def analyze_audio(file_path, demo_key='evening'):
    """Backward-compatible adapter for the original API."""
    result = CryClassifier().predict(file_path if file_path else None, demo_key)
    return {'type': result['prediction'], 'confidence': result['confidence'], 'alternatives': result['alternatives']}


__all__ = ['CryClassifier', 'analyze_audio', 'clip_embedding', 'HEAD_NPZ', 'HEAD_JSON', 'MODEL_FEATURE_INDEX', 'SAMPLE_RATE']
