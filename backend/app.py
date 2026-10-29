from flask import Flask, request, jsonify, send_from_directory, send_file, redirect, url_for
from flask_cors import CORS
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
import os
import datetime
import json
import mimetypes
import uuid

import numpy as np

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT_DIR, '.env'))
except ImportError:  # python-dotenv is optional; plain environment variables still work
    pass

from analyzers.audio import analyze_audio, CryClassifier
from analyzers.acoustics import AudioDecodeError, load_audio
from analyzers.history import get_historical_context, get_suggestions
from analyzers.longitudinal import CAUSES, LongitudinalAnalyzer, build_context, compute_stats, time_band
from analyzers.personalization import personalize
from analyzers.explain import explain
from models import db, User, CareEvent, InfantProfile, InfantProfilePhoto, FirstVoiceEvent, WeeklyReport

FRONTEND_DIR = os.path.join(ROOT_DIR, 'frontend')
RECORDINGS_DIR = os.path.join(ROOT_DIR, 'data', 'firstvoice_recordings')
FEATURES_DIR = os.path.join(ROOT_DIR, 'data', 'firstvoice_features')
PROFILE_PHOTOS_DIR = os.path.join(ROOT_DIR, 'data', 'profile_photos')
AUDIO_EXTENSIONS = {'.webm', '.wav', '.mp4', '.m4a', '.ogg', '.mp3', '.flac'}
DEMO_USERNAME = 'demo'
DEMO_PASSWORD = 'firstvoice'
REPORT_MAX_AGE = datetime.timedelta(days=7)
REPORT_NEW_EVENTS_TRIGGER = 10

app = Flask(__name__, static_folder=FRONTEND_DIR)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('FIRSTVOICE_DB', 'sqlite:///users.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024

CORS(app)
db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = 'login_page'
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


with app.app_context():
    db.create_all()
    demo_user = User.query.filter_by(username=DEMO_USERNAME).first()
    if not demo_user:
        demo_user = User(username=DEMO_USERNAME)
        db.session.add(demo_user)
    demo_user.set_password(DEMO_PASSWORD)
    db.session.commit()

classifier = CryClassifier()
longitudinal_analyzer = LongitudinalAnalyzer()


def _warm_up_models():
    """Load YAMNet weights and JIT-compile librosa once, so the first real cry is fast."""
    try:
        classifier.predict(np.zeros(16000 * 2, dtype=np.float32) + 1e-4 * np.random.randn(32000).astype(np.float32))
    except Exception as exc:  # never block startup on the warm-up
        app.logger.warning('Model warm-up failed: %s', exc)


if os.getenv('FIRSTVOICE_WARMUP', '1') == '1':
    import threading
    threading.Thread(target=_warm_up_models, daemon=True).start()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_profile(user_id):
    profile = InfantProfile.query.filter_by(user_id=user_id).first()
    if not profile:
        profile = InfantProfile(user_id=user_id)
        db.session.add(profile)
        db.session.commit()
    return profile


def _profile_dict(profile):
    return {'name': profile.name, 'age_months': profile.age_months, 'gender': profile.gender,
            'feeding_interval': profile.feeding_interval, 'sleep_note': profile.sleep_note}


def _profile_photo_url(user_id):
    photo = InfantProfilePhoto.query.filter_by(user_id=user_id).first()
    return url_for('firstvoice_profile_photo', filename=photo.filename) if photo else None


def _context(event):
    try:
        return json.loads(event.context_json or '{}')
    except (TypeError, ValueError):
        return {}


def _features_path(user_id, event_id):
    return os.path.join(FEATURES_DIR, str(user_id), f'{event_id}.npz')


def _ensure_demo_history(user_id, age_months):
    """Only the shared demo account gets a seeded month of history."""
    user = db.session.get(User, user_id)
    if not user or user.username != DEMO_USERNAME:
        return
    existing_events = FirstVoiceEvent.query.filter_by(user_id=user_id).all()
    if any('"demo_seed": true' in (event.context_json or '') for event in existing_events):
        return
    now = datetime.datetime.now()
    labels = ['Hungry', 'Hungry', 'Tired', 'Hungry', 'Discomfort', 'Hungry', 'Tired']
    demo_keys = ['feeding', 'evening', 'nap', 'feeding', 'comfort', 'evening', 'night']
    hours = {'Hungry': [7, 10, 19, 20, 2], 'Tired': [13, 16, 21], 'Discomfort': [11, 18]}
    for index in range(42):
        label = labels[index % len(labels)]
        options = hours[label]
        event_time = (now - datetime.timedelta(days=index * 28 // 42 + 1)).replace(
            hour=options[index % len(options)], minute=(index * 11) % 60, second=0, microsecond=0)
        detail = {'Hungry': 'Feeding', 'Tired': 'Rocking', 'Discomfort': 'Burping'}[label]
        db.session.add(FirstVoiceEvent(
            user_id=user_id, timestamp=event_time, age_months=age_months,
            demo_key=demo_keys[index % len(demo_keys)], predicted_cause=label,
            classifier_confidence=[72, 76, 68, 74][index % 4],
            parent_feedback=label if index % 5 else ('Tired' if label == 'Hungry' else 'Hungry'),
            feedback_confirmed=index % 5 != 0,
            recommendation=longitudinal_analyzer.recommendation_for(label),
            recommendation_feedback=index % 4 != 0,
            recommendation_detail=detail if index % 4 else 'White noise',
            audio_reference='demo-cry',
            context_json=json.dumps({'source': 'demo-seed', 'demo_seed': True}),
        ))
    db.session.commit()


def _history(user_id):
    """Structured history for Layer 2 (parent label wins over the prediction)."""
    events = FirstVoiceEvent.query.filter_by(user_id=user_id).order_by(FirstVoiceEvent.timestamp.asc()).all()
    return [{
        'id': event.id,
        'timestamp': event.timestamp.isoformat(),
        'label': event.parent_feedback or event.predicted_cause,
        'predicted': event.predicted_cause,
        'confidence': event.classifier_confidence,
        'feedback_confirmed': event.feedback_confirmed,
        'recommendation': event.recommendation,
        'recommendation_feedback': event.recommendation_feedback,
        'recommendation_detail': event.recommendation_detail,
        'time_of_day': event.timestamp.strftime('%H:%M'),
    } for event in events]


def _personal_examples(user_id, exclude_id=None):
    """Loop 1 training data: this baby's reviewed cries that have stored embeddings."""
    examples = []
    events = FirstVoiceEvent.query.filter(
        FirstVoiceEvent.user_id == user_id, FirstVoiceEvent.feedback_confirmed.isnot(None),
        FirstVoiceEvent.parent_feedback.isnot(None)).all()
    for event in events:
        if event.id == exclude_id:
            continue
        path = _features_path(user_id, event.id)
        if os.path.isfile(path):
            try:
                examples.append((np.load(path)['embedding'], event.parent_feedback))
            except (OSError, KeyError, ValueError):
                continue
    return examples


def _pitch_baseline(user_id, exclude_id=None):
    pitches = []
    for event in FirstVoiceEvent.query.filter_by(user_id=user_id).all():
        if event.id == exclude_id:
            continue
        pitch = ((_context(event).get('analysis') or {}).get('features') or {}).get('pitch_hz')
        if pitch:
            pitches.append(pitch)
    return {'pitch_hz': int(np.median(pitches))} if len(pitches) >= 3 else None


def _latest_report_row(user_id):
    return WeeklyReport.query.filter_by(user_id=user_id).order_by(WeeklyReport.created_at.desc(), WeeklyReport.id.desc()).first()


def _report(user_id, profile, history, force=False, allow_generate=True):
    row = _latest_report_row(user_id)
    stale = row is None or force or (datetime.datetime.now() - row.created_at > REPORT_MAX_AGE) or \
        (len(history) - (row.event_count or 0) >= REPORT_NEW_EVENTS_TRIGGER) or \
        (not row.event_count and len(history) > 0)
    if row is not None and (not stale or not allow_generate):
        return json.loads(row.report_json)
    if not allow_generate:
        return None
    previous = json.loads(row.report_json) if row else None
    week_ago = datetime.datetime.now() - datetime.timedelta(days=7)
    care_entries = [{
        'when': entry.timestamp.strftime('%a %H:%M'), 'type': entry.event_type, 'detail': entry.cause,
        'notes': (entry.notes or '')[:120],
    } for entry in CareEvent.query.filter(CareEvent.user_id == user_id, CareEvent.event_type != 'FirstVoice cry',
                                          CareEvent.timestamp >= week_ago).order_by(CareEvent.timestamp.asc()).all()]
    context = build_context(_profile_dict(profile), history, care_entries, previous)
    report = longitudinal_analyzer.generate_report(context)
    db.session.add(WeeklyReport(user_id=user_id, provider=report.get('provider'), event_count=len(history),
                                report_json=json.dumps(report)))
    db.session.commit()
    return report


def _serialize_event(event):
    recording_filename = None
    if event.audio_reference and event.audio_reference.startswith(f'user/{event.user_id}/'):
        recording_filename = os.path.basename(event.audio_reference)
    context = _context(event)
    analysis = context.get('analysis') or {}
    alternatives = analysis.get('alternatives')
    if alternatives is None:
        alternatives = classifier.predict(None, event.demo_key).get('alternatives', [])
    return {
        'id': event.id,
        'timestamp': event.timestamp.isoformat(),
        'prediction': event.predicted_cause,
        'confidence': event.classifier_confidence,
        'parent_feedback': event.parent_feedback,
        'feedback_confirmed': event.feedback_confirmed,
        'recommendation': event.recommendation,
        'action_plan': analysis.get('action_plan') or ([event.recommendation] if event.recommendation else []),
        'explanation': analysis.get('explanation') or [],
        'recommendation_feedback': event.recommendation_feedback,
        'recommendation_detail': event.recommendation_detail,
        'demo_key': event.demo_key,
        'audio_reference': event.audio_reference,
        'recording_url': url_for('firstvoice_recording', filename=recording_filename) if recording_filename else None,
        'source': context.get('source', 'demo'),
        'model_source': analysis.get('model_source', 'demo preset'),
        'alternatives': alternatives,
        'probabilities': analysis.get('probabilities'),
        'personalization': analysis.get('personalization'),
        'detection': analysis.get('detection'),
        'features': analysis.get('features'),
        'spectrogram': analysis.get('spectrogram'),
    }


def _recent_pattern_data(history, period='week'):
    windows = {'day': 1, 'week': 7, 'month': 30}
    labels = {'day': 'Today', 'week': 'Last 7 days', 'month': 'Last 30 days'}
    selected_period = period if period in windows else 'week'
    cutoff = datetime.datetime.now() - datetime.timedelta(days=windows[selected_period])
    recent = [item for item in history if datetime.datetime.fromisoformat(item['timestamp']) >= cutoff]
    counts = {}
    for item in recent:
        counts[item['label']] = counts.get(item['label'], 0) + 1
    total = len(recent)
    reasons = [{'label': label, 'count': count, 'percent': round(count / total * 100) if total else 0}
               for label, count in sorted(counts.items(), key=lambda pair: pair[1], reverse=True)]
    return {'period': selected_period, 'label': labels[selected_period], 'events': total, 'reasons': reasons}


def _timeline(user_id):
    """Events per month of age, from the real history."""
    buckets = {}
    for event in FirstVoiceEvent.query.filter_by(user_id=user_id).all():
        month = int(event.age_months)
        buckets[month] = buckets.get(month, 0) + 1
    return [{'age_months': month, 'events': count} for month, count in sorted(buckets.items())]


def _dashboard_data(age_months=None, period='week', allow_generate=True, force_report=False):
    profile = _get_profile(current_user.id)
    selected_age = max(0, min(18, float(age_months if age_months is not None else profile.age_months)))
    _ensure_demo_history(current_user.id, selected_age)
    history = _history(current_user.id)
    stats = compute_stats(history)
    report = _report(current_user.id, profile, history, force=force_report, allow_generate=allow_generate)
    latest_event = FirstVoiceEvent.query.filter_by(user_id=current_user.id).order_by(FirstVoiceEvent.timestamp.desc()).first()
    confirmed = sum(1 for item in history if item.get('feedback_confirmed') is True)
    corrected = sum(1 for item in history if item.get('feedback_confirmed') is False)
    feedback_events = [item for item in history if item.get('recommendation_feedback') is not None]
    helpful = sum(1 for item in feedback_events if item.get('recommendation_feedback') is True)
    counts = {}
    for item in history:
        counts[item['label']] = counts.get(item['label'], 0) + 1
    recent_patterns = _recent_pattern_data(history, period)
    peak_band = max(stats['this_week']['by_time_band'], key=stats['this_week']['by_time_band'].get) if stats['this_week']['by_time_band'] else None
    personal_examples = len(_personal_examples(current_user.id))
    return {
        'profile': _profile_dict(profile) | {'age_months': selected_age, 'photo_url': _profile_photo_url(current_user.id)},
        'stats': {'events': len(history), 'recent_events': recent_patterns['events'], 'confirmed': confirmed,
                  'corrected': corrected, 'recommendation_feedback': len(feedback_events), 'helpful_recommendations': helpful,
                  'recommendation_effectiveness': round(helpful / len(feedback_events) * 100) if feedback_events else 0,
                  'causes': counts, 'peak_period': peak_band, 'personal_examples': personal_examples},
        'latest': _serialize_event(latest_event) if latest_event else None,
        'report': report,
        'recent_patterns': recent_patterns,
        'timeline': _timeline(current_user.id),
        'model': classifier.model_info(),
        'llm': {'configured': longitudinal_analyzer.refresh_provider().configured,
                'provider': longitudinal_analyzer.llm.display_name},
        'demo_mode': current_user.username == DEMO_USERNAME,
        'llm_configured': longitudinal_analyzer.llm.configured,
    }


def _sync_firstvoice_calendar_events(user_id):
    """Backfill FirstVoice analyses into the existing care calendar."""
    changed = False
    for event in FirstVoiceEvent.query.filter_by(user_id=user_id).all():
        marker = f'FirstVoice event #{event.id}'
        existing = CareEvent.query.filter(CareEvent.user_id == user_id, CareEvent.event_type == 'FirstVoice cry',
                                          CareEvent.notes.like(f'{marker}%')).first()
        if existing:
            continue
        db.session.add(CareEvent(
            user_id=user_id, timestamp=event.timestamp, event_type='FirstVoice cry',
            cause=event.parent_feedback or event.predicted_cause, confidence=event.classifier_confidence,
            notes=f'{marker}\nSuggested next step: {event.recommendation or "Observe your baby and try a familiar soothing routine."}',
        ))
        changed = True
    if changed:
        db.session.commit()


def _save_audio_upload(file_storage):
    extension = os.path.splitext(secure_filename(file_storage.filename or ''))[1].lower()
    if extension not in AUDIO_EXTENSIONS:
        extension = mimetypes.guess_extension(file_storage.mimetype or '') or '.wav'
    if extension not in AUDIO_EXTENSIONS:
        extension = '.wav'
    user_folder = os.path.join(RECORDINGS_DIR, str(current_user.id))
    os.makedirs(user_folder, exist_ok=True)
    filename = f"{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:12]}{extension}"
    file_storage.save(os.path.join(user_folder, filename))
    return filename, f'user/{current_user.id}/{filename}'


# --- Auth Routes ---
@app.route('/api/signup', methods=['POST'])
def signup():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username already exists'}), 400
        
    new_user = User(username=username)
    new_user.set_password(password)
    db.session.add(new_user)
    db.session.commit()
    
    login_user(new_user)
    return jsonify({'message': 'Registered successfully'}), 200

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password):
        login_user(user)
        return jsonify({'message': 'Logged in successfully'}), 200
    
    
    return jsonify({'error': 'Invalid credentials'}), 401

@app.route('/api/user', methods=['GET'])
@login_required
def get_user():
    return jsonify({
        'username': current_user.username,
        'auto_listen_enabled': current_user.auto_listen_enabled
    }), 200

@app.route('/api/settings', methods=['POST'])
@login_required
def update_settings():
    data = request.json
    if 'auto_listen' in data:
        current_user.auto_listen_enabled = data['auto_listen']
        db.session.commit()
    return jsonify({'message': 'Settings updated', 'auto_listen': current_user.auto_listen_enabled}), 200

@app.route('/api/logout', methods=['GET'])
@login_required
def logout():
    logout_user()
    return jsonify({'message': 'Logged out successfully'}), 200

# --- Sub-pages ---
@app.route('/login')
def login_page():
    if current_user.is_authenticated:
        return redirect('/')
    return send_from_directory(FRONTEND_DIR, 'login.html')

@app.route('/signup')
def signup_page():
    if current_user.is_authenticated:
        return redirect('/')
    return send_from_directory(FRONTEND_DIR, 'signup.html')

# --- Protected Routes ---
@app.route('/')
@login_required
def index():
    return send_from_directory(FRONTEND_DIR, 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    # Public assets for login/signup pages
    return send_from_directory(FRONTEND_DIR, path)

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "time": datetime.datetime.now().isoformat()}), 200


@app.route('/api/firstvoice/recordings', methods=['POST'])
@login_required
def upload_firstvoice_recording():
    """Save a recording in a private folder for the signed-in parent."""
    audio = request.files.get('audio')
    if not audio or not audio.filename:
        return jsonify({'error': 'No audio recording was provided'}), 400
    filename, reference = _save_audio_upload(audio)
    return jsonify({
        'audio_reference': reference,
        'recording_url': url_for('firstvoice_recording', filename=filename),
        'message': 'Recording saved to your personalized history folder',
    }), 201


@app.route('/api/firstvoice/recordings/<filename>', methods=['GET'])
@login_required
def firstvoice_recording(filename):
    safe_filename = secure_filename(filename)
    if safe_filename != filename:
        return jsonify({'error': 'Recording not found'}), 404
    path = os.path.join(RECORDINGS_DIR, str(current_user.id), safe_filename)
    if not os.path.isfile(path):
        return jsonify({'error': 'Recording not found'}), 404
    return send_file(path)


@app.route('/api/firstvoice/profile/photo', methods=['POST'])
@login_required
def upload_firstvoice_profile_photo():
    """Save one private baby profile image for the signed-in parent."""
    photo = request.files.get('photo')
    allowed_types = {'image/jpeg', 'image/png', 'image/webp'}
    if not photo or not photo.filename or photo.mimetype not in allowed_types:
        return jsonify({'error': 'Please choose a JPG, PNG, or WebP image'}), 400

    extension = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}[photo.mimetype]
    user_folder = os.path.join(PROFILE_PHOTOS_DIR, str(current_user.id))
    os.makedirs(user_folder, exist_ok=True)
    filename = f"profile_{uuid.uuid4().hex[:16]}{extension}"
    photo.save(os.path.join(user_folder, filename))

    record = InfantProfilePhoto.query.filter_by(user_id=current_user.id).first()
    if not record:
        record = InfantProfilePhoto(user_id=current_user.id, filename=filename, content_type=photo.mimetype)
        db.session.add(record)
    else:
        old_path = os.path.join(user_folder, record.filename)
        if os.path.isfile(old_path):
            os.remove(old_path)
        record.filename = filename
        record.content_type = photo.mimetype
    db.session.commit()
    return jsonify({'photo_url': url_for('firstvoice_profile_photo', filename=filename)}), 201


@app.route('/api/firstvoice/profile/photo/<filename>', methods=['GET'])
@login_required
def firstvoice_profile_photo(filename):
    safe_filename = secure_filename(filename)
    if safe_filename != filename:
        return jsonify({'error': 'Profile photo not found'}), 404
    photo = InfantProfilePhoto.query.filter_by(user_id=current_user.id, filename=safe_filename).first()
    if not photo:
        return jsonify({'error': 'Profile photo not found'}), 404
    path = os.path.join(PROFILE_PHOTOS_DIR, str(current_user.id), safe_filename)
    if not os.path.isfile(path):
        return jsonify({'error': 'Profile photo not found'}), 404
    return send_file(path, mimetype=photo.content_type)

def _classify_saved_recording(audio_path, profile, history, stats):
    """Layer 1 on real audio, personalized with this baby's feedback history."""
    examples = _personal_examples(current_user.id)
    result = classifier.predict(audio_path, personalizer=lambda probs, emb: personalize(probs, emb, examples, CAUSES))
    report = _report(current_user.id, profile, history, allow_generate=False)
    result['action_plan'] = longitudinal_analyzer.action_plan(result['prediction'], stats, (report or {}).get('playbook'))
    result['explanation'] = explain(result, profile.name, _pitch_baseline(current_user.id), stats, history)
    return result


@app.route('/api/analyze', methods=['POST'])
@app.route('/api/firstvoice/analyze', methods=['POST'])
@login_required
def analyze():
    """Analyze a cry. Accepts JSON (demo preset or a saved audio_reference) or
    multipart form data with an 'audio' file (used by hands-free mode)."""
    try:
        data = request.form.to_dict() if request.files else (request.get_json(silent=True) or {})
        source = data.get('source', 'demo')
        if source not in {'demo', 'upload', 'live', 'auto'}:
            source = 'upload' if request.files else 'demo'
        demo_key = data.get('demo_key', 'evening')
        profile = _get_profile(current_user.id)
        age_months = float(data.get('age_months') or profile.age_months)
        history = _history(current_user.id)
        stats = compute_stats(history)

        if request.files.get('audio'):
            _, audio_reference = _save_audio_upload(request.files['audio'])
        else:
            audio_reference = data.get('audio_reference') or ('demo-cry' if source == 'demo' else None)

        if source == 'demo':
            preset = classifier.predict(None, demo_key)
            result = preset | {'action_plan': longitudinal_analyzer.action_plan(preset['prediction'], stats,
                                                                                (_report(current_user.id, profile, history, allow_generate=False) or {}).get('playbook'))}
            result['explanation'] = ['Demo preset: a parent-confirmed sample used to walk through the flow. Upload a recording or use the microphone to run the real model.']
        else:
            expected_prefix = f'user/{current_user.id}/'
            if not isinstance(audio_reference, str) or not audio_reference.startswith(expected_prefix):
                return jsonify({'error': 'Record or upload a recording before analyzing it'}), 400
            audio_path = os.path.join(RECORDINGS_DIR, str(current_user.id), os.path.basename(audio_reference))
            if not os.path.isfile(audio_path):
                return jsonify({'error': 'Saved recording could not be found'}), 404
            try:
                result = _classify_saved_recording(audio_path, profile, history, stats)
            except AudioDecodeError as exc:
                return jsonify({'error': str(exc)}), 415

        plan = result['action_plan']
        analysis = {
            'model_source': result.get('source'),
            'alternatives': result.get('alternatives'),
            'probabilities': result.get('probabilities'),
            'personalization': result.get('personalization'),
            'detection': result.get('detection'),
            'features': result.get('features'),
            'spectrogram': result.get('spectrogram'),
            'action_plan': plan,
            'explanation': result.get('explanation'),
        }
        event = FirstVoiceEvent(
            user_id=current_user.id, timestamp=datetime.datetime.now(), age_months=age_months, demo_key=demo_key if source == 'demo' else source,
            predicted_cause=result['prediction'], classifier_confidence=result['confidence'],
            parent_feedback=result['prediction'] if source == 'demo' else None,
            feedback_confirmed=True if source == 'demo' else None,
            recommendation=plan[0], audio_reference=audio_reference,
            context_json=json.dumps({'source': source, 'time_band': time_band(datetime.datetime.now().hour), 'analysis': analysis}),
        )
        db.session.add(event)
        db.session.flush()
        if result.get('embedding') is not None:
            os.makedirs(os.path.dirname(_features_path(current_user.id, event.id)), exist_ok=True)
            np.savez(_features_path(current_user.id, event.id), embedding=result['embedding'], acoustic=result['acoustic_vector'])
        db.session.add(CareEvent(user_id=current_user.id, timestamp=event.timestamp, event_type='FirstVoice cry',
                                 cause=result['prediction'], confidence=result['confidence'],
                                 notes=f'FirstVoice event #{event.id}\nSuggested next step: {plan[0]}'))
        db.session.commit()
        return jsonify({
            'event': _serialize_event(event),
            'prediction': {k: result.get(k) for k in ('prediction', 'confidence', 'alternatives', 'source', 'probabilities')},
            'suggestions': plan,
            'medical_alert': plan[-1] if result['prediction'] == 'Pain' else None,
            'dashboard': _dashboard_data(age_months, allow_generate=False),
            'emotion': result['prediction'],
            'confidence': result['confidence'],
        }), 200
    except Exception as e:
        app.logger.exception('Error during analysis')
        return jsonify({"error": "Analysis failed", "details": str(e)}), 500


@app.route('/api/firstvoice/detect', methods=['POST'])
@login_required
def detect_cry():
    """Hands-free mode stage 1: does this short window contain an infant cry?
    The window is analyzed in memory and not stored."""
    audio = request.files.get('audio')
    if not audio:
        return jsonify({'error': 'No audio provided'}), 400
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.wav') as handle:
        audio.save(handle.name)
        try:
            wav = load_audio(handle.name, max_seconds=6)
        except AudioDecodeError as exc:
            return jsonify({'error': str(exc)}), 415
    detection, _ = classifier.detect(wav)
    return jsonify(detection), 200


@app.route('/api/firstvoice/dashboard', methods=['GET'])
@login_required
def firstvoice_dashboard():
    return jsonify(_dashboard_data(request.args.get('age_months'), request.args.get('period', 'week'))), 200


@app.route('/api/firstvoice/report/refresh', methods=['POST'])
@login_required
def refresh_report():
    """Ask Layer 2 to write a fresh report now (calls the LLM when configured)."""
    return jsonify(_dashboard_data(force_report=True)), 200


@app.route('/api/firstvoice/model', methods=['GET'])
@login_required
def model_card():
    return jsonify({'layer1': classifier.model_info(),
                    'layer2': {'configured': longitudinal_analyzer.refresh_provider().configured,
                               'provider': longitudinal_analyzer.llm.display_name}}), 200


@app.route('/api/firstvoice/feedback', methods=['POST'])
@login_required
def firstvoice_feedback():
    data = request.get_json(silent=True) or {}
    event = FirstVoiceEvent.query.filter_by(id=data.get('event_id'), user_id=current_user.id).first()
    if not event:
        return jsonify({'error': 'Classification event not found'}), 404
    confirmed = bool(data.get('confirmed'))
    corrected = data.get('corrected_cause')
    if not confirmed and corrected not in CAUSES:
        return jsonify({'error': f'corrected_cause must be one of {CAUSES}'}), 400
    event.feedback_confirmed = confirmed
    event.parent_feedback = event.predicted_cause if confirmed else corrected
    calendar_entry = CareEvent.query.filter(CareEvent.user_id == current_user.id, CareEvent.event_type == 'FirstVoice cry',
                                            CareEvent.notes.like(f'FirstVoice event #{event.id}%')).first()
    if calendar_entry:
        calendar_entry.cause = event.parent_feedback
    db.session.commit()
    return jsonify({'event': _serialize_event(event), 'dashboard': _dashboard_data(event.age_months, allow_generate=False)}), 200


@app.route('/api/firstvoice/recommendation-feedback', methods=['POST'])
@login_required
def recommendation_feedback():
    data = request.get_json(silent=True) or {}
    event = FirstVoiceEvent.query.filter_by(id=data.get('event_id'), user_id=current_user.id).first()
    if not event:
        return jsonify({'error': 'Recommendation event not found'}), 404
    event.recommendation_feedback = bool(data.get('helpful'))
    event.recommendation_detail = (data.get('detail') or '')[:80] or None
    db.session.commit()
    return jsonify({'event': _serialize_event(event), 'dashboard': _dashboard_data(event.age_months, allow_generate=False)}), 200


@app.route('/api/firstvoice/profile', methods=['PUT'])
@login_required
def update_firstvoice_profile():
    profile = _get_profile(current_user.id)
    data = request.get_json(silent=True) or {}
    profile.name = (data.get('name') or profile.name)[:100]
    profile.age_months = max(0, min(18, float(data.get('age_months', profile.age_months))))
    profile.gender = (data.get('gender') if data.get('gender') is not None else profile.gender)[:50]
    profile.feeding_interval = (data.get('feeding_interval') or profile.feeding_interval)[:80]
    db.session.commit()
    return jsonify(_dashboard_data(profile.age_months, allow_generate=False)), 200

# --- Calendar/History Routes ---
@app.route('/api/calendar', methods=['GET'])
@login_required
def get_calendar_events():
    _sync_firstvoice_calendar_events(current_user.id)
    events = CareEvent.query.filter_by(user_id=current_user.id).order_by(CareEvent.timestamp.desc()).all()
    events_data = []
    for e in events:
        events_data.append({
            'id': e.id,
            'timestamp': e.timestamp.isoformat(),
            'event_type': e.event_type,
            'cause': e.cause,
            'confidence': e.confidence,
            'notes': e.notes
        })
    return jsonify(events_data), 200

@app.route('/api/calendar', methods=['POST'])
@login_required
def add_calendar_event():
    data = request.json
    new_event = CareEvent(
        user_id=current_user.id,
        event_type=data.get('event_type', 'Manual Entry'),
        cause=data.get('cause', 'Unknown'),
        confidence=data.get('confidence', 100.0),
        notes=data.get('notes', '')
    )
    db.session.add(new_event)
    db.session.commit()
    return jsonify({'message': 'Event added', 'id': new_event.id}), 201

@app.route('/api/calendar/<int:event_id>', methods=['PUT'])
@login_required
def update_calendar_event(event_id):
    event = db.session.get(CareEvent, event_id)
    if not event or event.user_id != current_user.id:
        return jsonify({'error': 'Event not found'}), 404
        
    data = request.json
    if 'cause' in data:
        event.cause = data['cause']
    if 'notes' in data:
        event.notes = data['notes']
        
    db.session.commit()
    return jsonify({'message': 'Event updated'}), 200

@app.route('/api/calendar/<int:event_id>', methods=['DELETE'])
@login_required
def delete_calendar_event(event_id):
    event = db.session.get(CareEvent, event_id)
    if not event or event.user_id != current_user.id:
        return jsonify({'error': 'Event not found'}), 404
        
    db.session.delete(event)
    db.session.commit()
    return jsonify({'message': 'Event deleted'}), 200

@app.route('/calendar')
@login_required
def calendar_page():
    return send_from_directory(FRONTEND_DIR, 'calendar.html')





@app.route('/auto-monitor')
@login_required
def auto_monitor_page():
    return send_from_directory(FRONTEND_DIR, 'auto_mode.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=os.getenv('FLASK_DEBUG', '1') == '1', port=int(os.getenv('PORT', 5002)))
