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

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
python backend\app.py
```

Open [http://localhost:5002](http://localhost:5002).

For repeatable demos, use the built-in account:

```text
Username: demo
Password: firstvoice
```

The account is created automatically when the app starts.

The server binds to `0.0.0.0`, so a phone on the same network can use the laptop's local IP address:

```text
http://<laptop-local-ip>:5002
```

The app uses SQLite during local development. The database file is created by Flask-SQLAlchemy when the application starts.

## Demo flow

1. Create an account or log in.
2. Choose a demo cry such as Evening fuss, Feeding cue, or Nap-time cry.
3. Run the Layer 1 classification.
4. Confirm or correct the prediction.
5. Review the personalized event counts and the longitudinal timeline.
6. Review the Layer 2 weekly care report.
7. Mark the suggested next step helpful or not helpful.
8. Change the age selector from 0 to 18 months to show the simulated history growing with the baby.

The demo mode is deterministic. It does not require a microphone or an audio file.

The dashboard supports three audio sources:

- **Labeled recordings**: parent-confirmed demo samples that can be reused as learning examples.
- **Upload recording**: choose a new audio file, save it to the signed-in parent's recording folder, preview it, and analyze it.
- **Live microphone**: record directly in the browser and keep the existing live capture flow.

Live microphone mode uses the browser's `MediaRecorder` API. After recording, the audio is uploaded to `data/firstvoice_recordings/<user_id>/` and linked to the resulting FirstVoice history event. The folder is ignored by git. The current prototype stores the live audio but still uses the deterministic classifier profile for the prediction; replacing that adapter with a real audio model is a future step.

Infant profiles support a name, age, gender, and optional baby photo. Uploaded profile photos are stored privately under `data/profile_photos/<user_id>/` and served only to the signed-in parent.

## Architecture

### Layer 1: cry classification

`backend/analyzers/audio.py` exposes the `CryClassifier` interface:

```python
result = classifier.predict(audio, demo_key="evening")
```

The result contains a prediction, confidence, alternatives, and a source label. The current repository has a deterministic demo classifier boundary rather than a trained checkpoint. A real acoustic model can replace the internals without changing the API routes or frontend contract.

### Layer 2: longitudinal analysis

`backend/analyzers/longitudinal.py` exposes `LongitudinalAnalyzer`.

It receives structured infant history and returns:

```json
{
  "summary": "...",
  "patterns": [],
  "changes": [],
  "recommendations": [],
  "cautions": []
}
```

Without an API key, the app uses a deterministic local fallback generated from the history. With `OPENAI_API_KEY` configured, the analyzer attempts an LLM-backed structured report and falls back locally if the request fails.

## The two feedback loops

### Classification personalization

```text
Cry -> classifier -> prediction -> parent confirmation/correction
    -> personalized history -> future personalization signal
```

This records how the individual baby communicates. It does not automatically retrain the underlying classifier.

### Recommendation personalization

```text
History -> Layer 2 report -> suggested next step -> parent feedback
        -> recommendation history -> future reports
```

These loops are stored separately in `FirstVoiceEvent` so classification feedback is not confused with recommendation effectiveness.

## Important API routes

| Route | Method | Purpose |
| --- | --- | --- |
| `/api/firstvoice/analyze` | POST | Create a demo classification event |
| `/api/firstvoice/dashboard` | GET | Return profile, metrics, timeline, and Layer 2 report |
| `/api/firstvoice/feedback` | POST | Confirm or correct a classification |
| `/api/firstvoice/recommendation-feedback` | POST | Record recommendation helpfulness |
| `/api/firstvoice/profile` | PUT | Update infant name and age |
| `/api/login` | POST | Log in |
| `/api/signup` | POST | Create an account |
| `/health` | GET | Health check |

The old `/api/analyze` endpoint remains as a compatibility alias.

## Optional LLM configuration

The demo works without external API access. To enable the LLM-backed Layer 2 path, set:

```powershell
$env:OPENAI_API_KEY = "your-api-key"
$env:OPENAI_MODEL = "gpt-4o-mini"
python backend\app.py
```

Keep API keys in environment variables. Do not commit keys to the repository. The application should continue to work through the local fallback when the key is absent or the request is unavailable.

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

- The current Layer 1 classifier is a deterministic demo adapter, not a validated trained model.
- Demo history is simulated to make the 0-18 month story visible.
- The default Layer 2 report is generated locally from simple history counts.
- SQLite and the development secret key are suitable for a prototype, not production.
- The current app has no automated browser test suite.
- Medical safety messaging is included, but this prototype must not be used for medical decisions.
