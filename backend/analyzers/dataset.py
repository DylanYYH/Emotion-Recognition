"""Donate-a-Cry corpus helpers shared by training and evaluation."""
import glob
import io
import os
import urllib.request
import zipfile

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DATASET_DIR = os.path.join(ROOT, 'data', 'datasets', 'donateacry')
CORPUS_URL = 'https://codeload.github.com/gveres/donateacry-corpus/zip/refs/heads/master'
CORPUS_SUBDIR = 'donateacry_corpus_cleaned_and_updated_data'

# FirstVoice uses four parent-facing causes.
LABEL_MAP = {
    'hungry': 'Hungry',
    'tired': 'Tired',
    'discomfort': 'Discomfort',
    'burping': 'Discomfort',     # trapped wind is a kind of physical discomfort
    'belly_pain': 'Pain',
}
LABELS = ['Hungry', 'Tired', 'Discomfort', 'Pain']


def ensure_corpus(dataset_dir=DATASET_DIR):
    target = os.path.join(dataset_dir, CORPUS_SUBDIR)
    if os.path.isdir(target):
        return target
    os.makedirs(dataset_dir, exist_ok=True)
    print(f'Downloading Donate-a-Cry corpus from {CORPUS_URL} ...')
    with urllib.request.urlopen(CORPUS_URL, timeout=120) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    prefix = None
    for name in archive.namelist():
        if f'/{CORPUS_SUBDIR}/' in name and name.endswith('.wav'):
            prefix = name.split(f'/{CORPUS_SUBDIR}/')[0]
            relative = name[len(prefix) + 1:]
            out = os.path.join(dataset_dir, relative)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, 'wb') as handle:
                handle.write(archive.read(name))
    return target


def list_clips(corpus_dir):
    clips = []
    for folder, label in LABEL_MAP.items():
        for path in sorted(glob.glob(os.path.join(corpus_dir, folder, '*.wav'))):
            clips.append((path, label, folder))
    return clips
