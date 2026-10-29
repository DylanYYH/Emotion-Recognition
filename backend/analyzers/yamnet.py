"""YAMNet (Google, Apache 2.0) inference in plain NumPy.

YAMNet is a MobileNet-v1 style CNN trained on AudioSet (521 sound classes).
FirstVoice uses it twice:
  1. As the lightweight sound event detector: its "Baby cry, infant cry"
     score decides whether a clip contains crying at all.
  2. As the frozen feature extractor for transfer learning: the 1024-d
     embedding of every 0.96 s patch feeds FirstVoice's own cry-cause head.

The forward pass is reimplemented here from the official Keras weights
(yamnet.h5) so the app does not need TensorFlow at runtime. The math mirrors
tensorflow/models/research/audioset/yamnet (features.py + yamnet.py) and is
checked against the TensorFlow reference in backend/tools/verify_yamnet.py.
"""
import csv
import os
import threading
import urllib.request

import numpy as np

PRETRAINED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'models', 'pretrained')
WEIGHTS_PATH = os.path.join(PRETRAINED_DIR, 'yamnet.h5')
CLASS_MAP_PATH = os.path.join(PRETRAINED_DIR, 'yamnet_class_map.csv')
WEIGHTS_URL = 'https://storage.googleapis.com/audioset/yamnet.h5'

SAMPLE_RATE = 16000
STFT_WINDOW = 400          # 25 ms
STFT_HOP = 160             # 10 ms
FFT_LENGTH = 512
MEL_BANDS = 64
MEL_MIN_HZ = 125.0
MEL_MAX_HZ = 7500.0
LOG_OFFSET = 0.001
PATCH_FRAMES = 96          # 0.96 s
PATCH_HOP_FRAMES = 48      # 0.48 s
BN_EPSILON = 1e-4

LAYER_DEFS = [
    ('conv', 2, 32), ('sep', 1, 64), ('sep', 2, 128), ('sep', 1, 128),
    ('sep', 2, 256), ('sep', 1, 256), ('sep', 2, 512), ('sep', 1, 512),
    ('sep', 1, 512), ('sep', 1, 512), ('sep', 1, 512), ('sep', 1, 512),
    ('sep', 2, 1024), ('sep', 1, 1024),
]

CRY_CLASS_NAMES = ('Baby cry, infant cry',)
SOB_CLASS_NAMES = ('Crying, sobbing',)
RELATED_CLASS_NAMES = ('Whimper', 'Screaming', 'Wail, moan')


def _hz_to_mel(hz):
    return 1127.0 * np.log(1.0 + hz / 700.0)


def _mel_matrix(num_spectrogram_bins):
    """Same construction as tf.signal.linear_to_mel_weight_matrix."""
    linear_freqs = np.linspace(0.0, SAMPLE_RATE / 2.0, num_spectrogram_bins)[1:]
    spec_mel = _hz_to_mel(linear_freqs)[:, None]
    edges = np.linspace(_hz_to_mel(MEL_MIN_HZ), _hz_to_mel(MEL_MAX_HZ), MEL_BANDS + 2)
    lower, center, upper = edges[:-2], edges[1:-1], edges[2:]
    lower_slopes = (spec_mel - lower) / (center - lower)
    upper_slopes = (upper - spec_mel) / (upper - center)
    weights = np.maximum(0.0, np.minimum(lower_slopes, upper_slopes))
    return np.pad(weights, [[1, 0], [0, 0]]).astype(np.float32)


_MEL = _mel_matrix(FFT_LENGTH // 2 + 1)
_HANN = (0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(STFT_WINDOW) / STFT_WINDOW)).astype(np.float32)


def pad_waveform(waveform):
    min_samples = int((0.96 + 0.025 - 0.010) * SAMPLE_RATE)
    num_samples = max(len(waveform), min_samples)
    hop = int(0.48 * SAMPLE_RATE)
    hops_after_first = int(np.ceil((num_samples - min_samples) / hop))
    target = min_samples + hops_after_first * hop
    return np.pad(waveform, (0, max(0, target - len(waveform))))


def log_mel_spectrogram(waveform):
    """[frames, 64] log-mel spectrogram exactly as YAMNet computes it."""
    waveform = np.asarray(waveform, dtype=np.float32)
    if len(waveform) < STFT_WINDOW:
        waveform = np.pad(waveform, (0, STFT_WINDOW - len(waveform)))
    n_frames = 1 + (len(waveform) - STFT_WINDOW) // STFT_HOP
    idx = np.arange(STFT_WINDOW)[None, :] + STFT_HOP * np.arange(n_frames)[:, None]
    frames = waveform[idx] * _HANN
    magnitude = np.abs(np.fft.rfft(frames, n=FFT_LENGTH, axis=1)).astype(np.float32)
    return np.log(magnitude @ _MEL + LOG_OFFSET)


def spectrogram_patches(log_mel):
    if len(log_mel) < PATCH_FRAMES:
        return np.zeros((0, PATCH_FRAMES, MEL_BANDS), dtype=np.float32)
    n = 1 + (len(log_mel) - PATCH_FRAMES) // PATCH_HOP_FRAMES
    idx = np.arange(PATCH_FRAMES)[None, :] + PATCH_HOP_FRAMES * np.arange(n)[:, None]
    return log_mel[idx]


def _same_pad(x, stride):
    """TensorFlow 'same' padding for a 3x3 kernel on NHWC input."""
    pads = []
    for size in x.shape[1:3]:
        out = -(-size // stride)
        total = max((out - 1) * stride + 3 - size, 0)
        pads.append((total // 2, total - total // 2))
    return np.pad(x, [(0, 0), pads[0], pads[1], (0, 0)]), [-(-s // stride) for s in x.shape[1:3]]


def _windows(x, stride, out_hw):
    padded, _ = _same_pad(x, stride)
    oh, ow = out_hw
    for dy in range(3):
        for dx in range(3):
            yield dy, dx, padded[:, dy:dy + stride * (oh - 1) + 1:stride, dx:dx + stride * (ow - 1) + 1:stride, :]


class YAMNet:
    def __init__(self, weights_path=WEIGHTS_PATH):
        self.weights_path = weights_path
        self._weights = None
        self._lock = threading.Lock()
        self.class_names = self._load_class_names()
        self.cry_index = [i for i, n in enumerate(self.class_names) if n in CRY_CLASS_NAMES]
        self.sob_index = [i for i, n in enumerate(self.class_names) if n in SOB_CLASS_NAMES]
        self.related_index = [i for i, n in enumerate(self.class_names) if n in RELATED_CLASS_NAMES]

    @staticmethod
    def _load_class_names():
        with open(CLASS_MAP_PATH) as handle:
            reader = csv.reader(handle)
            next(reader)
            return [row[2] for row in reader]

    @staticmethod
    def ensure_weights(path=WEIGHTS_PATH):
        if os.path.isfile(path):
            return path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        urllib.request.urlretrieve(WEIGHTS_URL, path)
        return path

    def _load(self):
        if self._weights is not None:
            return self._weights
        with self._lock:
            if self._weights is not None:
                return self._weights
            import h5py
            self.ensure_weights(self.weights_path)
            w = {}
            with h5py.File(self.weights_path, 'r') as f:
                def visit(name, obj):
                    if isinstance(obj, h5py.Dataset):
                        w[name.rsplit(':', 1)[0]] = np.array(obj, dtype=np.float32)
                f.visititems(visit)
            self._weights = w
            return w

    def _bn_relu(self, x, prefix):
        w = self._weights
        scale = 1.0 / np.sqrt(w[f'{prefix}/moving_variance'] + BN_EPSILON)
        return np.maximum(0.0, (x - w[f'{prefix}/moving_mean']) * scale + w[f'{prefix}/beta'])

    def _forward(self, patches):
        w = self._weights
        x = patches[..., None].astype(np.float32)
        for i, (kind, stride, filters) in enumerate(LAYER_DEFS, start=1):
            name = f'layer{i}'
            out_hw = [-(-s // stride) for s in x.shape[1:3]]
            if kind == 'conv':
                kernel = w[f'{name}/conv/{name}/conv/kernel']  # 3,3,in,out
                acc = np.zeros((x.shape[0], out_hw[0], out_hw[1], filters), dtype=np.float32)
                for dy, dx, win in _windows(x, stride, out_hw):
                    acc += win @ kernel[dy, dx]
                x = self._bn_relu(acc, f'{name}/conv/bn/{name}/conv/bn')
            else:
                dk = w[f'{name}/depthwise_conv/{name}/depthwise_conv/depthwise_kernel'][..., 0]  # 3,3,C
                acc = np.zeros((x.shape[0], out_hw[0], out_hw[1], x.shape[3]), dtype=np.float32)
                for dy, dx, win in _windows(x, stride, out_hw):
                    acc += win * dk[dy, dx]
                x = self._bn_relu(acc, f'{name}/depthwise_conv/bn/{name}/depthwise_conv/bn')
                pk = w[f'{name}/pointwise_conv/{name}/pointwise_conv/kernel'][0, 0]  # C,F
                x = self._bn_relu(x @ pk, f'{name}/pointwise_conv/bn/{name}/pointwise_conv/bn')
        embeddings = x.mean(axis=(1, 2))
        logits = embeddings @ w['logits/logits/kernel'] + w['logits/logits/bias']
        scores = 1.0 / (1.0 + np.exp(-np.clip(logits, -60, 60)))
        return scores, embeddings

    def run(self, waveform, batch_size=32):
        """Returns (scores [patches,521], embeddings [patches,1024], log_mel [frames,64])."""
        self._load()
        waveform = pad_waveform(np.asarray(waveform, dtype=np.float32))
        log_mel = log_mel_spectrogram(waveform)
        patches = spectrogram_patches(log_mel)
        scores, embeddings = [], []
        for start in range(0, len(patches), batch_size):
            s, e = self._forward(patches[start:start + batch_size])
            scores.append(s)
            embeddings.append(e)
        return np.concatenate(scores), np.concatenate(embeddings), log_mel

    def cry_scores(self, scores):
        """Per-patch probability that the patch contains an infant cry."""
        cry = scores[:, self.cry_index].max(axis=1)
        sob = scores[:, self.sob_index].max(axis=1)
        related = scores[:, self.related_index].max(axis=1)
        return np.maximum.reduce([cry, 0.85 * sob, 0.6 * related])

    def top_classes(self, scores, k=3):
        mean = scores.mean(axis=0)
        order = np.argsort(mean)[::-1][:k]
        return [{'label': self.class_names[i], 'score': float(mean[i])} for i in order]


_shared = None


def get_yamnet():
    global _shared
    if _shared is None:
        _shared = YAMNet()
    return _shared
