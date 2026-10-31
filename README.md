# FirstVoice

FirstVoice is a working demo of a two-layer AI system that learns with a baby over time.

Layer 1 asks: **What might this cry mean right now?**

Layer 2 asks: **What can we learn about this baby over time?**

This is a prototype for demonstration and exploration. It is not a medical diagnostic product.

## Project structure

```text
Emotion-Recognition/
├── backend/
│   ├── app.py                    Flask app, routes, authentication, API
│   ├── models.py                 SQLAlchemy models
│   ├── analyzers/
│   │   ├── audio.py              Layer 1 classifier boundary
│   │   ├── history.py             Existing care suggestions and context helpers
│   │   ├── longitudinal.py        Layer 2 report and demo-history logic
│   │   └── vision.py              Existing vision analyzer
│   ├── requirements.txt
│   └── test_*.py                 Existing backend tests
├── frontend/
│   ├── index.html                Main FirstVoice dashboard
│   ├── script.js                 Dashboard interactions and API calls
│   ├── style.css                 Responsive laptop/mobile UI
│   ├── login.html, signup.html   Authentication screens
│   ├── calendar*.html/js         Existing care calendar
│   ├── auto_mode.*               Existing auto-monitor view
│   └── manifest.json             Installable web-app metadata
└── Procfile                     Gunicorn deployment command
```

Flask serves the separate `frontend/` directory. The frontend is intentionally framework-free so the demo can be edited quickly and run without a separate Node build step.

## Run locally

From the repository root (macOS/Linux):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env          # optional: add an API key for Layer 2
python backend/app.py
```

Windows PowerShell: `.\.venv\Scripts\Activate.ps1` and `copy .env.example .env`.

Open [http://localhost:5002](http://localhost:5002). The built-in account `demo` / `firstvoice` has a seeded month of history; any new account starts empty.

## Deploy on Render

This repository includes a `render.yaml` blueprint. In Render, choose **New > Blueprint**, connect the repository, and deploy it. The service uses `gunicorn --chdir backend app:app` and exposes `/health` for health checks.

The included Blueprint is intentionally configured as a free demo: it uses the default local SQLite database and temporary local file storage. New accounts, recordings, and profile photos can disappear when the free service restarts. For a real deployment, create a Render Postgres database and set `FIRSTVOICE_DB` to its internal connection string. If recordings and profile photos must survive restarts, attach a persistent disk mounted at `/var/data` (or change `FIRSTVOICE_STORAGE_DIR` to the disk mount path). Render's default filesystem is ephemeral.

TensorFlow is **not** needed. YAMNet runs as a NumPy port of Google's official weights (`backend/models/pretrained/yamnet.h5`, Apache 2.0), checked against the TensorFlow reference to within 1e-5 (`backend/tools/verify_yamnet.py`). The trained cry head ships in `backend/models/`, so the app works right after install.

The browser records 16 kHz WAV directly, so live and hands-free audio need no ffmpeg. Uploaded WebM/M4A files do (`brew install ffmpeg`); WAV and MP3 work without it.

## How it works

```text
microphone ──► energy gate (browser) ──► YAMNet sound event detection ("Baby cry, infant cry")
                                               │ cry confirmed
                                               ▼
                 log-Mel spectrogram ──► YAMNet CNN embeddings (frozen, AudioSet)
                 + MFCCs, pitch, energy, rhythm
                                               ▼
                 trained softmax head ──► general probabilities
                                               ▼
   loop 1  ──►   per-baby personalization from parent confirmations/corrections
                                               ▼
                 result + "why" + action plan (from the Layer 2 playbook) ──► spoken aloud, saved to care calendar
                                               ▼
   loop 2  ──►   LLM weekly report (Claude Sonnet 5 or GPT) reads age, time of day, cause frequency,
                 feedback, what helped, calendar entries and its previous report ──► new playbook
```

### Layer 1: cry classification (`backend/analyzers/`)

| File | Role |
| --- | --- |
| `yamnet.py` | YAMNet in NumPy: log-Mel spectrogram, CNN forward pass, cry-detection score |
| `acoustics.py` | Audio loading, MFCC / pitch (YIN) / energy / burst-rhythm features, spectrogram thumbnail |
| `audio.py` | `CryClassifier`: detection, transfer-learned head, demo presets |
| `personalization.py` | Loop 1: blends this baby's reviewed cries into each prediction |
| `explain.py` | Plain-language "why" for each result |

Retrain the head with `python backend/train_cry_model.py`. It downloads the Donate-a-Cry corpus (~70 MB, from GitHub) into `data/datasets/`, extracts features, cross-validates, and writes `backend/models/cry_head.npz` + `cry_head.json`.

### Layer 1 evaluation (read this before presenting numbers)

Evaluation is **grouped by family**: a baby's clips are never in both the training and the test fold. That is the honest setting for a new baby, and it changes the picture a lot.

| Measure | Result |
| --- | --- |
| Clips / families | 457 / 221 (Hungry 382, Tired 24, Discomfort 35, Pain 16) |
| General model, balanced accuracy on unseen babies | **26%** (chance 25%) |
| Same model with per-baby feedback, replayed in time order | **42% → 69%** accuracy |
| Simply repeating the family's previous label | 83% (see caveat) |

What this means:

- On this public dataset, a general cry-cause model does not beat chance on babies it has never heard. Mixing the same families into train and test made it look like 38%, which was leakage. This matches Lockhart-Bouron et al.: one general model struggles to separate causes like fussiness and hunger.
- Personalization is where the gains come from, which supports the "learn each baby individually" design.
- Caveat: many Donate-a-Cry families uploaded several clips from one crying session with the same label, which flatters every history-based method, including the naive "repeat last label" baseline. A real per-baby study is needed before claiming a personalization accuracy number.
- YAMNet's cry **detection** works well (real infant cries score around 0.7-0.95, room noise near 0). That part is reliable; the cause classification is the open research problem.
- Pain recall is near zero (only 16 Pain clips), so personalization is never allowed to lower the chance of a Pain result, and every Pain result tells parents to contact a healthcare professional if worried.

### Layer 2: longitudinal analysis (`backend/analyzers/longitudinal.py`)

The analyzer builds a structured context (age, cries per cause this week vs. the previous three weeks, time-of-day bands per cause, parent agreement rate and common corrections, which actions helped for each cause, manual calendar entries, the previous report) and asks the LLM for JSON: `summary`, `patterns`, `changes`, `recommendations`, `playbook` (3 steps per cause), `focus_for_next_week`, `cautions`.

Reports are cached in the `WeeklyReport` table and regenerated when one is 7 days old, when 10 new cries have arrived, or when the parent taps **Refresh**. The playbook from the latest report becomes the action plan for every new cry, reordered by what this baby's parent marked helpful.

Provider selection (`.env`):

| Setting | Effect |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude Sonnet 5 (`ANTHROPIC_MODEL`, default `claude-sonnet-5`) |
| `OPENAI_API_KEY` | OpenAI Responses API (`OPENAI_MODEL`, default `gpt-5.4-mini`) |
| neither | Local rule-based report with the same structure |

If a call fails, the app falls back to the local report and shows a note on the dashboard.

### Hands-free mode (`/auto-monitor`)

Leave the page open on a computer near the crib. A loudness gate runs in the browser; loud moments are sent to `/api/firstvoice/detect` (YAMNet, nothing stored). After two positive checks it records until the crying stops (4-10 s, including 2.5 s of pre-roll), analyzes it, reads the result and first action aloud with the browser's speech synthesis, and pauses for the chosen cooldown. Results can be confirmed or corrected right there.

## Tests

```bash
python backend/tests/test_firstvoice_flow.py                       # synthetic audio
python backend/tests/test_firstvoice_flow.py path/to/real_cry.wav  # also checks cry detection
```

This runs the full path with Flask's test client and a temporary database: real-audio classification, explanation and action plan, four corrections feeding personalization, recommendation feedback, local and (stubbed) Claude reports, previous report fed back to the LLM, playbook reaching the action plan, detector on noise vs. a real cry, and calendar sync.

## API routes

| Route | Method | Purpose |
| --- | --- | --- |
| `/api/firstvoice/analyze` | POST | Analyze a cry: JSON (`demo_key` or saved `audio_reference`) or multipart `audio` file |
| `/api/firstvoice/detect` | POST | Hands-free stage 1: cry / no cry for a short window |
| `/api/firstvoice/recordings` | POST | Save a recording to the parent's private folder |
| `/api/firstvoice/feedback` | POST | Confirm or correct a classification (loop 1) |
| `/api/firstvoice/recommendation-feedback` | POST | Mark a suggestion helpful / not helpful (loop 2) |
| `/api/firstvoice/report/refresh` | POST | Regenerate the Layer 2 report now |
| `/api/firstvoice/dashboard` | GET | Profile, stats, latest event, report, model card |
| `/api/firstvoice/model` | GET | Layer 1 metrics and Layer 2 provider |
| `/api/firstvoice/profile` | PUT | Update infant profile |
| `/api/calendar` | GET/POST/PUT/DELETE | Care calendar |

## Safety and product language

Keep FirstVoice language probabilistic and non-diagnostic:

- Use "most likely", "possible reason", and "suggested next step".
- Do not describe a cry classification as a diagnosis.
- Do not claim medical accuracy or scientific validation without real evaluation data.
- For concerning or unusual symptoms, recommend contacting a healthcare professional.
- Preserve the visible disclaimer in the dashboard.

## Future vibe coding guide

Before making a change:

1. Read the relevant backend route, model, analyzer, and frontend call site together.
2. Preserve the existing classifier boundary in `CryClassifier.predict()`.
3. Keep raw cry classification in Layer 1; keep historical pattern analysis in Layer 2.
4. Store parent corrections and recommendation feedback as separate fields.
5. Make demo behavior deterministic and usable without an API key or microphone.
6. Test the full path: classify -> feedback -> history -> report -> recommendation feedback.
7. Keep the dashboard usable at both narrow phone widths and laptop widths.
8. Do not remove existing calendar, authentication, or auto-monitor routes without checking their consumers.

When adding a feature, prefer this flow:

```text
database model -> backend service/analyzer -> API route -> frontend state -> responsive UI
```

Avoid putting business logic directly into HTML event handlers when it belongs in a backend service or analyzer.

## Known limitations

- The cause classifier is trained on one small, unbalanced, parent-labelled public dataset and performs at chance on unseen babies. Treat results as prompts, not answers.
- Personalization is instant (no retraining job) but needs a real per-baby study to measure properly.
- The hands-free page must stay open in a browser tab with microphone permission; there is no dedicated crib hardware yet.
- SQLite and the development secret key suit a prototype, not production.
- Not a medical device. Do not use it for medical decisions.
