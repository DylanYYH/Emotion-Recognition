"""Train FirstVoice's Layer 1 cry-cause head on the Donate-a-Cry corpus.

Transfer learning setup:
  waveform -> log-Mel spectrogram -> YAMNet CNN (frozen, AudioSet-pretrained)
           -> 1024-d embeddings (cry-weighted mean over 0.96 s patches)
  + interpretable acoustic features (MFCCs, pitch, energy, rhythm)
  -> trained softmax head (dense layer) -> Hungry / Tired / Discomfort / Pain

Usage (from the repository root):
    python backend/train_cry_model.py

The corpus (~70 MB) downloads from GitHub on first run. Results are written to
backend/models/cry_head.npz and backend/models/cry_head.json.
"""
import datetime
import json
import os
import sys
import warnings

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyzers.acoustics import FEATURE_NAMES  # noqa: E402
from analyzers.audio import HEAD_JSON, HEAD_NPZ, MODEL_FEATURE_INDEX, clip_embedding  # noqa: E402
from analyzers.dataset import LABELS  # noqa: E402
from analyzers.personalization import personalize  # noqa: E402
from tools.extract_features import build_cache  # noqa: E402

C_REGULARIZATION = 0.003  # chosen by family-grouped 5-fold CV (see README "Layer 1 evaluation")
SEEDS = (0, 1, 2)


def _design_matrix(cache):
    n = len(cache['clip_label'])
    embeddings = np.stack([
        clip_embedding(cache['frame_emb'][cache['frame_clip'] == i], cache['frame_cry'][cache['frame_clip'] == i])
        for i in range(n)
    ])
    acoustic = cache['clip_ac'][:, MODEL_FEATURE_INDEX]
    return np.hstack([embeddings, acoustic]).astype(np.float64), embeddings


def _family_and_time(cache):
    """Donate-a-Cry file names start with an app-install UUID (one family) and a timestamp."""
    families, times = [], []
    for name in cache['clip_path']:
        name = str(name)
        families.append(name[:36].lower())
        try:
            times.append(int(name[37:].split('-')[0]))
        except ValueError:
            times.append(0)
    return np.array(families), np.array(times)


def _fit(X, y):
    from sklearn.linear_model import LogisticRegression
    mean, std = X.mean(axis=0), X.std(axis=0) + 1e-6
    model = LogisticRegression(C=C_REGULARIZATION, class_weight='balanced', max_iter=5000)
    model.fit((X - mean) / std, y)
    return model, mean, std


def evaluate(X, y, embeddings, families, times):
    """Grouped CV: every family's clips stay in one fold, so the model is always
    tested on babies it never heard. Within each held-out family we also replay
    the clips in time order with simulated parent feedback to measure loop 1."""
    from sklearn.metrics import balanced_accuracy_score, confusion_matrix, f1_score, recall_score
    from sklearn.model_selection import StratifiedGroupKFold
    runs, matrix = [], np.zeros((len(LABELS), len(LABELS)), dtype=int)
    replay = {'general': 0, 'personalized': 0, 'repeat_last': 0, 'clips': 0}
    for seed in SEEDS:
        predictions = np.zeros(len(y), dtype=int)
        splitter = StratifiedGroupKFold(5, shuffle=True, random_state=seed)
        for train, test in splitter.split(X, y, groups=families):
            model, mean, std = _fit(X[train], y[train])
            probs = model.predict_proba((X[test] - mean) / std)
            predictions[test] = probs.argmax(axis=1)
            by_family = {}
            for row, index in enumerate(test):
                by_family.setdefault(families[index], []).append((times[index], index, row))
            for clips in by_family.values():
                clips.sort()
                history = []
                for _, index, row in clips:
                    if history:
                        general = {label: probs[row][i] for i, label in enumerate(LABELS)}
                        personal, _ = personalize(general, embeddings[index], history, LABELS)
                        replay['clips'] += 1
                        replay['general'] += int(np.argmax(probs[row]) == y[index])
                        replay['personalized'] += int(LABELS.index(max(personal, key=personal.get)) == y[index])
                        replay['repeat_last'] += int(history[-1][1] == LABELS[y[index]])
                    history.append((embeddings[index], LABELS[y[index]]))
        runs.append({
            'balanced_accuracy': balanced_accuracy_score(y, predictions),
            'macro_f1': f1_score(y, predictions, average='macro'),
            'accuracy': float((predictions == y).mean()),
            'recall': recall_score(y, predictions, average=None, labels=range(len(LABELS))),
        })
        matrix += confusion_matrix(y, predictions, labels=range(len(LABELS)))
    clips = max(1, replay['clips'])
    return {
        'protocol': f'family-grouped stratified 5-fold cross-validation x {len(SEEDS)} seeds (test babies never seen in training)',
        'balanced_accuracy': round(float(np.mean([r['balanced_accuracy'] for r in runs])), 3),
        'accuracy': round(float(np.mean([r['accuracy'] for r in runs])), 3),
        'macro_f1': round(float(np.mean([r['macro_f1'] for r in runs])), 3),
        'chance_balanced_accuracy': round(1 / len(LABELS), 3),
        'recall_per_class': {label: round(float(np.mean([r['recall'][i] for r in runs])), 3) for i, label in enumerate(LABELS)},
        'confusion_matrix': {'labels': LABELS, 'rows_true_cols_pred': matrix.tolist()},
        'personalization_replay': {
            'description': 'Held-out families replayed in time order; each earlier clip is added as parent feedback before the next prediction.',
            'clips_with_feedback_history': replay['clips'] // len(SEEDS),
            'general_accuracy': round(replay['general'] / clips, 3),
            'personalized_accuracy': round(replay['personalized'] / clips, 3),
            'repeat_last_label_baseline': round(replay['repeat_last'] / clips, 3),
            'caveat': 'Donate-a-Cry families often uploaded several clips from one crying session, which flatters any history-based method.',
        },
    }


def main():
    warnings.filterwarnings('ignore')
    print('Step 1/3  Extracting YAMNet embeddings and acoustic features...')
    cache = build_cache()
    y = np.array([LABELS.index(label) for label in cache['clip_label']])
    X, embeddings = _design_matrix(cache)
    families, times = _family_and_time(cache)
    counts = {label: int((y == i).sum()) for i, label in enumerate(LABELS)}
    print(f'          {len(y)} clips from {len(set(families))} families: {counts}')

    print('Step 2/3  Cross-validating the head...')
    metrics = evaluate(X, y, embeddings, families, times)
    print(f"          balanced accuracy {metrics['balanced_accuracy']:.3f} (chance {metrics['chance_balanced_accuracy']}), "
          f"macro F1 {metrics['macro_f1']:.3f}")
    print(f"          recall per class {metrics['recall_per_class']}")
    replay = metrics['personalization_replay']
    print(f"          personalization replay on {replay['clips_with_feedback_history']} clips: "
          f"general {replay['general_accuracy']:.3f} -> personalized {replay['personalized_accuracy']:.3f}")

    print('Step 3/3  Fitting the final head on all clips...')
    model, mean, std = _fit(X, y)
    os.makedirs(os.path.dirname(HEAD_NPZ), exist_ok=True)
    np.savez(HEAD_NPZ, mean=mean.astype(np.float32), std=std.astype(np.float32),
             weights=model.coef_.T.astype(np.float32), bias=model.intercept_.astype(np.float32))
    meta = {
        'labels': LABELS,
        'architecture': 'YAMNet CNN (frozen, AudioSet) -> 1024-d embedding + 35 acoustic features -> softmax head',
        'acoustic_features': [FEATURE_NAMES[i] for i in MODEL_FEATURE_INDEX],
        'dataset': 'Donate-a-Cry corpus (gveres/donateacry-corpus); burping->Discomfort, belly_pain->Pain',
        'class_counts': counts,
        'families': int(len(set(families))),
        'regularization_C': C_REGULARIZATION,
        'trained_at': datetime.datetime.now().isoformat(timespec='seconds'),
        'evaluation': metrics,
    }
    with open(HEAD_JSON, 'w') as handle:
        json.dump(meta, handle, indent=2)
    print(f'Saved {os.path.relpath(HEAD_NPZ)} and {os.path.relpath(HEAD_JSON)}')


if __name__ == '__main__':
    main()
