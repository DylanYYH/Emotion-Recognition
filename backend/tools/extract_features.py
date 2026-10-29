"""Run YAMNet + acoustic features over the corpus and cache the results."""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analyzers.acoustics import acoustic_features, load_audio  # noqa: E402
from analyzers.dataset import DATASET_DIR, ensure_corpus, list_clips  # noqa: E402
from analyzers.yamnet import get_yamnet  # noqa: E402

CACHE_PATH = os.path.join(DATASET_DIR, 'features_cache.npz')


def build_cache(force=False):
    if os.path.isfile(CACHE_PATH) and not force:
        return dict(np.load(CACHE_PATH, allow_pickle=True))
    corpus = ensure_corpus()
    clips = list_clips(corpus)
    yamnet = get_yamnet()
    frame_emb, frame_cry, frame_clip, clip_ac, clip_label, clip_folder, clip_path = [], [], [], [], [], [], []
    started = time.time()
    for index, (path, label, folder) in enumerate(clips):
        wav = load_audio(path)
        scores, embeddings, log_mel = yamnet.run(wav)
        vector, _ = acoustic_features(wav, log_mel)
        frame_emb.append(embeddings.astype(np.float32))
        frame_cry.append(yamnet.cry_scores(scores).astype(np.float32))
        frame_clip.append(np.full(len(embeddings), index))
        clip_ac.append(vector)
        clip_label.append(label)
        clip_folder.append(folder)
        clip_path.append(os.path.basename(path))
        if index % 50 == 0:
            print(f'  {index}/{len(clips)} clips ({time.time() - started:.0f}s)', flush=True)
    cache = {
        'frame_emb': np.concatenate(frame_emb), 'frame_cry': np.concatenate(frame_cry),
        'frame_clip': np.concatenate(frame_clip), 'clip_ac': np.stack(clip_ac),
        'clip_label': np.array(clip_label), 'clip_folder': np.array(clip_folder), 'clip_path': np.array(clip_path),
    }
    np.savez_compressed(CACHE_PATH, **cache)
    return cache


if __name__ == '__main__':
    data = build_cache(force='--force' in sys.argv)
    print({k: v.shape for k, v in data.items()})
