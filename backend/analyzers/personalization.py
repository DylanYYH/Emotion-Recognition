"""Feedback loop 1: personalize cry classification for one baby.

Every time a parent confirms or corrects a result, that cry (its YAMNet
embedding plus the parent's label) becomes a labeled example for this baby.
Two signals are blended with the general model's probabilities:

  * Baby-specific prior: how often this baby's confirmed cries have had each
    cause. The general head is trained class-balanced, so multiplying by the
    baby's own cause frequencies is a standard label-shift correction.
  * Nearest prototypes: how similar the new cry sounds to this baby's earlier
    cries of each cause (cosine similarity of YAMNet embeddings).

Both signals start weak and grow with the number of examples, so a new baby
gets the general model and an older baby gets mostly their own history. The
settings below were tuned by replaying held-out Donate-a-Cry families in time
order (see train_cry_model.py). Personalization is never allowed to make a
possible pain cry less likely than a neutral prior would.
Nothing is retrained in a slow batch job: each piece of feedback takes effect
on the very next cry.
"""
import numpy as np

PRIOR_HALF_LIFE = 1.0      # examples needed for the prior to reach half strength
PRIOR_PSEUDOCOUNT = 0.5    # Dirichlet smoothing per cause
PROTOTYPE_MAX_WEIGHT = 0.6
PROTOTYPE_HALF_LIFE = 4
PROTOTYPE_TEMPERATURE = 25.0
# Safety: personalization may raise, but never lower, the chance of flagging pain.
NEVER_SUPPRESS = ('Pain',)


def _normalize(vector):
    vector = np.asarray(vector, dtype=np.float32)
    return vector / (np.linalg.norm(vector) + 1e-8)


def personalize(global_probs, embedding, examples, labels=None):
    """
    global_probs: {label: p} from the general model.
    embedding:   YAMNet clip embedding for the new cry.
    examples:    list of (embedding, parent_label) for this baby.
    Returns (probabilities, info).
    """
    labels = labels or list(global_probs)
    p_global = np.array([global_probs.get(label, 0.0) for label in labels], dtype=np.float64)
    examples = [(e, l) for e, l in examples if l in labels and e is not None]
    n = len(examples)
    info = {
        'examples': n,
        'examples_by_cause': {label: sum(1 for _, l in examples if l == label) for label in labels},
        'prior_weight': 0.0,
        'prototype_weight': 0.0,
        'global_probabilities': {label: round(float(p), 4) for label, p in zip(labels, p_global)},
        'nearest_cause': None,
        'changed_prediction': False,
    }
    if n == 0:
        return dict(zip(labels, p_global)), info

    counts = np.array([info['examples_by_cause'][label] for label in labels], dtype=np.float64)
    prior = (counts + PRIOR_PSEUDOCOUNT) / (n + PRIOR_PSEUDOCOUNT * len(labels))
    for index, label in enumerate(labels):
        if label in NEVER_SUPPRESS:
            prior[index] = max(prior[index], 1.0 / len(labels))
    prior_strength = n / (n + PRIOR_HALF_LIFE)
    adjusted = p_global * prior ** prior_strength
    adjusted /= adjusted.sum()
    info['prior_weight'] = round(prior_strength, 3)

    present = counts > 0
    final = adjusted
    if present.sum() >= 2 and n >= 3:
        query = _normalize(embedding)
        sims = np.full(len(labels), -np.inf)
        for index, label in enumerate(labels):
            members = [_normalize(e) for e, l in examples if l == label]
            if members:
                sims[index] = float(_normalize(np.mean(members, axis=0)) @ query)
        logits = np.where(present, sims * PROTOTYPE_TEMPERATURE, -np.inf)
        logits -= logits[present].max()
        proto = np.where(present, np.exp(logits), 0.0)
        proto /= proto.sum()
        # Causes this baby has never shown keep the general model's share.
        mass_present = adjusted[present].sum()
        proto_full = np.where(present, proto * mass_present, adjusted)
        for index, label in enumerate(labels):
            if label in NEVER_SUPPRESS:
                proto_full[index] = max(proto_full[index], adjusted[index])
        weight = PROTOTYPE_MAX_WEIGHT * n / (n + PROTOTYPE_HALF_LIFE)
        final = (1 - weight) * adjusted + weight * proto_full
        final /= final.sum()
        info['prototype_weight'] = round(weight, 3)
        info['nearest_cause'] = labels[int(np.argmax(np.where(present, sims, -np.inf)))]

    info['changed_prediction'] = bool(np.argmax(final) != np.argmax(p_global))
    return dict(zip(labels, final)), info
