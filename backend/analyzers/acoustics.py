"""Audio loading and hand-crafted acoustic features (MFCCs, pitch, energy, rhythm).

These are the interpretable half of Layer 1. YAMNet embeddings capture the
overall sound; these numbers capture what the cry literature measures
directly (Parga et al., 2020: pain cries are higher in pitch and intensity
than hunger or fussy cries).
"""
import numpy as np

from .yamnet import SAMPLE_RATE, log_mel_spectrogram

N_MFCC = 13
PITCH_FMIN = 150.0   # infant cry f0 typically sits around 300-600 Hz
PITCH_FMAX = 1000.0

FEATURE_NAMES = (
    [f'mfcc{i}_mean' for i in range(N_MFCC)] + [f'mfcc{i}_std' for i in range(N_MFCC)] +
    ['pitch_mean', 'pitch_std', 'pitch_max', 'voiced_fraction',
     'energy_mean_db', 'energy_std_db', 'energy_peak_db',
     'burst_rate', 'burst_mean_s', 'pause_fraction',
     'centroid_mean', 'zcr_mean']
)


class AudioDecodeError(Exception):
    pass


def load_audio(path, max_seconds=30.0):
    """Mono float32 at 16 kHz. WAV/FLAC/OGG/MP3 decode natively; WebM/M4A need ffmpeg."""
    import librosa
    try:
        import soundfile as sf
        data, sr = sf.read(path, dtype='float32', always_2d=True)
        wav = data.mean(axis=1)
    except Exception:
        try:
            wav, sr = librosa.load(path, sr=None, mono=True)
        except Exception as exc:
            raise AudioDecodeError(
                'Could not decode this audio file. Use WAV or MP3, or install ffmpeg '
                '(macOS: brew install ffmpeg) for WebM/M4A recordings.') from exc
    if sr != SAMPLE_RATE:
        wav = librosa.resample(wav, orig_sr=sr, target_sr=SAMPLE_RATE)
    wav = np.asarray(wav, dtype=np.float32)[: int(max_seconds * SAMPLE_RATE)]
    if not len(wav):
        raise AudioDecodeError('The recording is empty.')
    return wav


def _segments(active):
    """Lengths (in frames) of True runs."""
    runs, count = [], 0
    for value in active:
        if value:
            count += 1
        elif count:
            runs.append(count)
            count = 0
    if count:
        runs.append(count)
    return runs


def acoustic_features(wav, log_mel=None):
    """Returns (feature_vector, human_readable_summary)."""
    import librosa
    wav = np.asarray(wav, dtype=np.float32)
    duration = len(wav) / SAMPLE_RATE
    if log_mel is None:
        log_mel = log_mel_spectrogram(wav)

    # MFCCs from the same log-mel spectrogram YAMNet sees.
    mel_db = 10.0 * log_mel / np.log(10.0)
    mfcc = librosa.feature.mfcc(S=mel_db.T, n_mfcc=N_MFCC)

    hop = 160
    rms = librosa.feature.rms(y=wav, frame_length=400, hop_length=hop)[0]
    rms_db = 20.0 * np.log10(rms + 1e-6)
    peak_db = float(np.percentile(rms_db, 95))
    active = rms_db > peak_db - 20.0           # frames within 20 dB of the loud part
    active_db = rms_db[active] if active.any() else rms_db

    f0 = librosa.yin(wav, fmin=PITCH_FMIN, fmax=PITCH_FMAX, sr=SAMPLE_RATE,
                     frame_length=1024, hop_length=hop)
    n = min(len(f0), len(active))
    voiced = f0[:n][active[:n]]
    voiced = voiced[(voiced > PITCH_FMIN * 1.05) & (voiced < PITCH_FMAX * 0.95)]
    pitch_mean = float(np.median(voiced)) if len(voiced) else 0.0
    pitch_std = float(np.std(voiced)) if len(voiced) else 0.0
    pitch_max = float(np.percentile(voiced, 95)) if len(voiced) else 0.0
    voiced_fraction = float(len(voiced) / max(1, active.sum()))

    bursts = [r for r in _segments(active) if r >= 10]   # >= 100 ms of sound
    burst_rate = len(bursts) / max(duration, 1e-3)
    burst_mean = float(np.mean(bursts) * hop / SAMPLE_RATE) if bursts else 0.0
    pause_fraction = float(1.0 - active.mean())

    centroid = librosa.feature.spectral_centroid(y=wav, sr=SAMPLE_RATE, n_fft=512, hop_length=hop)[0]
    zcr = librosa.feature.zero_crossing_rate(wav, frame_length=400, hop_length=hop)[0]

    vector = np.concatenate([
        mfcc.mean(axis=1), mfcc.std(axis=1),
        [pitch_mean, pitch_std, pitch_max, voiced_fraction,
         float(active_db.mean()), float(active_db.std()), peak_db,
         burst_rate, burst_mean, pause_fraction,
         float(centroid[active[:len(centroid)]].mean()) if active.any() else float(centroid.mean()),
         float(zcr.mean())],
    ]).astype(np.float32)

    summary = {
        'duration_s': round(duration, 2),
        'pitch_hz': round(pitch_mean),
        'pitch_peak_hz': round(pitch_max),
        'pitch_variation_hz': round(pitch_std),
        'energy_db': round(float(active_db.mean()), 1),
        'energy_peak_db': round(peak_db, 1),
        'bursts_per_s': round(burst_rate, 2),
        'burst_length_s': round(burst_mean, 2),
        'pause_fraction': round(pause_fraction, 2),
        'voiced_fraction': round(voiced_fraction, 2),
    }
    return np.nan_to_num(vector), summary


def spectrogram_thumbnail(log_mel, max_columns=160):
    """Down-sampled log-mel image (0-255 ints, low frequencies first) for the UI."""
    if not len(log_mel):
        return {'bands': 0, 'frames': 0, 'values': []}
    step = max(1, int(np.ceil(len(log_mel) / max_columns)))
    trimmed = log_mel[: (len(log_mel) // step) * step] if len(log_mel) >= step else log_mel
    pooled = trimmed.reshape(-1, step, log_mel.shape[1]).mean(axis=1)
    lo, hi = np.percentile(pooled, 5), np.percentile(pooled, 99)
    scaled = np.clip((pooled - lo) / max(hi - lo, 1e-6), 0, 1)
    image = (scaled.T * 255).astype(np.uint8)    # [bands, frames]
    return {'bands': int(image.shape[0]), 'frames': int(image.shape[1]), 'values': image.flatten().tolist()}
