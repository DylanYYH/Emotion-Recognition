"""Check the NumPy YAMNet port against Google's TensorFlow reference.

Needs: pip install tensorflow tf_keras, plus yamnet.py / features.py /
params.py from github.com/tensorflow/models/tree/master/research/audioset/yamnet
in a folder passed as the first argument. Optional: a WAV file as the second.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analyzers.yamnet import WEIGHTS_PATH, YAMNet  # noqa: E402

reference_dir = sys.argv[1]
sys.path.insert(0, reference_dir)
import params as yamnet_params  # noqa: E402
import yamnet as yamnet_reference  # noqa: E402

if len(sys.argv) > 2:
    from analyzers.acoustics import load_audio  # noqa: E402
    wav = load_audio(sys.argv[2])
else:
    t = np.arange(16000 * 3) / 16000
    wav = (0.3 * np.sin(2 * np.pi * 450 * t * (1 + 0.2 * np.sin(3 * t))) + 0.02 * np.random.randn(len(t))).astype(np.float32)

model = yamnet_reference.yamnet_frames_model(yamnet_params.Params())
model.load_weights(WEIGHTS_PATH)
ref_scores, ref_emb, ref_mel = [x.numpy() for x in model(wav)]
ours_scores, ours_emb, ours_mel = YAMNet().run(wav)
print('patches', ref_scores.shape, ours_scores.shape)
print('max |log-mel diff|   ', float(np.abs(ref_mel - ours_mel).max()))
print('max |embedding diff| ', float(np.abs(ref_emb - ours_emb).max()), 'scale', float(np.abs(ref_emb).max()))
print('max |score diff|     ', float(np.abs(ref_scores - ours_scores).max()))
print('same top class       ', bool((ref_scores.argmax(1) == ours_scores.argmax(1)).all()))
