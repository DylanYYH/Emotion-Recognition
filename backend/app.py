from flask import Flask, request, jsonify, send_from_directory, send_file, redirect, url_for
from flask_cors import CORS
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
import os
import datetime
import json
import mimetypes
import uuid
from analyzers.audio import analyze_audio, CryClassifier
from analyzers.vision import analyze_vision
from analyzers.history import get_historical_context, get_suggestions
from analyzers.longitudinal import LongitudinalAnalyzer, build_demo_history
from models import db, User, CareEvent, InfantProfile, InfantProfilePhoto, FirstVoiceEvent

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'frontend'))
RECORDINGS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'firstvoice_recordings'))
PROFILE_PHOTOS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'profile_photos'))
app = Flask(__name__, static_folder=FRONTEND_DIR)
app.config['SECRET_KEY'] = 'dev-secret-key-change-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

CORS(app)
db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = 'login_page'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Initialize DB
with app.app_context():
    db.create_all()

classifier = CryClassifier()
longitudinal_analyzer = LongitudinalAnalyzer()


def _get_profile(user_id):
    profile = InfantProfile.query.filter_by(user_id=user_id).first()
    if not profile:
        profile = InfantProfile(user_id=user_id)
        db.session.add(profile)
        db.session.commit()
    return profile


def _profile_photo_url(user_id):
    photo = InfantProfilePhoto.query.filter_by(user_id=user_id).first()
    return url_for('firstvoice_profile_photo', filename=photo.filename) if photo else None


def _ensure_demo_history(user_id, age_months):
    """Give a new demo account a believable current-month history once."""
    existing_events = FirstVoiceEvent.query.filter_by(user_id=user_id).all()
    if any('"demo_seed": true' in (event.context_json or '') for event in existing_events):
        return
    seed_count = max(0, 42 - len(existing_events))
    if seed_count == 0:
        return

    now = datetime.datetime.now()
    last_past_day = max(1, now.day - 1)
    labels = ['Hungry', 'Hungry', 'Tired', 'Hungry', 'Discomfort', 'Hungry', 'Tired']
    demo_keys = ['feeding', 'evening', 'nap', 'feeding', 'comfort', 'evening', 'night']
    for index in range(seed_count):
        day = 1 + ((index * 3) % last_past_day)
        event_time = now.replace(
            day=day,
            hour=6 + ((index * 3) % 15),
            minute=(index * 11) % 60,
            second=0,
            microsecond=0,
        )
        label = labels[index % len(labels)]
        recommendation = longitudinal_analyzer.recommendation_for(label, [])
        db.session.add(FirstVoiceEvent(
            user_id=user_id,
            timestamp=event_time,
            age_months=age_months,
            demo_key=demo_keys[index % len(demo_keys)],
            predicted_cause=label,
            classifier_confidence=[72, 76, 68, 74][index % 4],
            parent_feedback=label if index % 5 else ('Tired' if label == 'Hungry' else 'Hungry'),
            feedback_confirmed=index % 5 != 0,
            recommendation=recommendation,
            recommendation_feedback=index % 4 != 0,
            recommendation_detail=['Feeding', 'Burping', 'Nap', 'Rocking'][index % 4],
            audio_reference='demo-cry',
            context_json=json.dumps({'source': 'demo-seed', 'demo_seed': True}),
        ))
    db.session.commit()


def _event_history(user_id, age_months):
    events = FirstVoiceEvent.query.filter_by(user_id=user_id).order_by(FirstVoiceEvent.timestamp.asc()).all()
    history = [] if events else build_demo_history(age_months)
    for event in events:
        history.append({
            'label': event.parent_feedback or event.predicted_cause,
            'time_of_day': event.timestamp.strftime('%H:%M'),
            'feedback_confirmed': event.feedback_confirmed,
            'recommendation_feedback': event.recommendation_feedback,
            'timestamp': event.timestamp.isoformat(),
        })
    return history


def _serialize_event(event):
    recording_filename = None
    if event.audio_reference and event.audio_reference.startswith(f'user/{event.user_id}/'):
        recording_filename = os.path.basename(event.audio_reference)
    return {
        'id': event.id,
        'timestamp': event.timestamp.isoformat(),
        'prediction': event.predicted_cause,
        'confidence': event.classifier_confidence,
        'parent_feedback': event.parent_feedback,
        'feedback_confirmed': event.feedback_confirmed,
        'recommendation': event.recommendation,
        'recommendation_feedback': event.recommendation_feedback,
        'recommendation_detail': event.recommendation_detail,
        'demo_key': event.demo_key,
        'audio_reference': event.audio_reference,
        'recording_url': url_for('firstvoice_recording', filename=recording_filename) if recording_filename else None,
        'source': 'live' if recording_filename else 'demo',
    }


def _recent_pattern_data(history, period='week'):
    windows = {'day': 1, 'week': 7, 'month': 30}
    labels = {'day': 'Today', 'week': 'Last 7 days', 'month': 'Last 30 days'}
    selected_period = period if period in windows else 'week'
    cutoff = datetime.datetime.now() - datetime.timedelta(days=windows[selected_period])
    recent = []
    for item in history:
        try:
            timestamp = datetime.datetime.fromisoformat(item['timestamp'].replace('Z', '+00:00'))
            if timestamp.tzinfo:
                timestamp = timestamp.replace(tzinfo=None)
            if timestamp >= cutoff:
                recent.append(item)
        except (KeyError, TypeError, ValueError):
            continue

    counts = {}
    for item in recent:
        counts[item['label']] = counts.get(item['label'], 0) + 1
    total = len(recent)
    reasons = [
        {'label': label, 'count': count, 'percent': round(count / total * 100) if total else 0}
        for label, count in sorted(counts.items(), key=lambda pair: pair[1], reverse=True)
    ]
    return {'period': selected_period, 'label': labels[selected_period], 'events': total, 'reasons': reasons}


def _dashboard_data(age_months=None, period='week'):
    profile = _get_profile(current_user.id)
    selected_age = max(0, min(18, float(age_months if age_months is not None else profile.age_months)))
    _ensure_demo_history(current_user.id, selected_age)
    history = _event_history(current_user.id, selected_age)
    report = longitudinal_analyzer.generate_weekly_summary(history, selected_age)
    events = FirstVoiceEvent.query.filter_by(user_id=current_user.id).order_by(FirstVoiceEvent.timestamp.desc()).all()
    actual_count = len(history)
    confirmed = sum(1 for item in history if item.get('feedback_confirmed') is True)
    corrected = sum(1 for item in history if item.get('feedback_confirmed') is False)
    feedback_events = [item for item in history if item.get('recommendation_feedback') is not None]
    helpful = sum(1 for item in feedback_events if item.get('recommendation_feedback') is True)
    effectiveness = round(helpful / len(feedback_events) * 100) if feedback_events else 0
    counts = {}
    for item in history:
        counts[item['label']] = counts.get(item['label'], 0) + 1
    latest = _serialize_event(events[0]) if events else None
    recent_patterns = _recent_pattern_data(history, period)
    return {
        'profile': {'name': profile.name, 'age_months': selected_age, 'gender': profile.gender, 'feeding_interval': profile.feeding_interval, 'sleep_note': profile.sleep_note, 'photo_url': _profile_photo_url(current_user.id)},
        'stats': {'events': actual_count, 'recent_events': recent_patterns['events'], 'confirmed': confirmed, 'corrected': corrected, 'recommendation_feedback': len(feedback_events), 'helpful_recommendations': helpful, 'recommendation_effectiveness': effectiveness, 'causes': counts},
        'latest': latest,
        'report': report,
        'recent_patterns': recent_patterns,
        'timeline': [{'age_months': month, 'events': round(month * 23), 'label': 'Beginning' if month == 0 else ('Building pattern' if month < 6 else ('Personalized' if month < 12 else 'Longitudinal'))} for month in (0, 3, 6, 9, 12, 18)],
        'demo_mode': True,
        'llm_configured': bool(os.getenv('OPENAI_API_KEY')),
    }


def _sync_firstvoice_calendar_events(user_id):
    """Backfill FirstVoice analyses into the existing care calendar."""
    firstvoice_events = FirstVoiceEvent.query.filter_by(user_id=user_id).all()
    changed = False
    for event in firstvoice_events:
        existing = CareEvent.query.filter_by(
            user_id=user_id,
            event_type='FirstVoice cry',
            timestamp=event.timestamp,
            cause=event.predicted_cause,
        ).first()
        if existing:
            continue
        marker = f'FirstVoice event #{event.id}'
        db.session.add(CareEvent(
            user_id=user_id,
            timestamp=event.timestamp,
            event_type='FirstVoice cry',
            cause=event.predicted_cause,
            confidence=event.classifier_confidence,
            notes=f'{marker}\nSuggested next step: {event.recommendation or "Observe your baby and try a familiar soothing routine."}',
        ))
        changed = True
    if changed:
        db.session.commit()

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
    """Save a microphone capture in a private folder for the signed-in parent."""
    audio = request.files.get('audio')
    if not audio or not audio.filename:
        return jsonify({'error': 'No audio recording was provided'}), 400

    extension = os.path.splitext(secure_filename(audio.filename))[1].lower()
    extension = extension if extension in {'.webm', '.wav', '.mp4', '.m4a', '.ogg', '.mp3'} else ''
    if not extension:
        extension = mimetypes.guess_extension(audio.mimetype or '') or '.webm'
    if extension not in {'.webm', '.wav', '.mp4', '.m4a', '.ogg', '.mp3'}:
        extension = '.webm'

    user_folder = os.path.join(RECORDINGS_DIR, str(current_user.id))
    os.makedirs(user_folder, exist_ok=True)
    filename = f"{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:12]}{extension}"
    audio.save(os.path.join(user_folder, filename))
    reference = f'user/{current_user.id}/{filename}'
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

@app.route('/api/analyze', methods=['POST'])
@app.route('/api/firstvoice/analyze', methods=['POST'])
@login_required
def analyze():
    try:
        data = request.get_json(silent=True) or {}
        demo_key = data.get('demo_key', 'evening')
        source = data.get('source', 'demo')
        profile = _get_profile(current_user.id)
        age_months = float(data.get('age_months', profile.age_months))
        audio_reference = data.get('audio_reference') or ('demo-cry' if source == 'demo' else None)
        if source == 'live':
            expected_prefix = f'user/{current_user.id}/'
            if not isinstance(audio_reference, str) or not audio_reference.startswith(expected_prefix):
                return jsonify({'error': 'Record and save a live recording before analyzing it'}), 400
            saved_filename = os.path.basename(audio_reference)
            if not os.path.isfile(os.path.join(RECORDINGS_DIR, str(current_user.id), saved_filename)):
                return jsonify({'error': 'Saved recording could not be found'}), 404
        result = classifier.predict(None, demo_key)
        recommendation = longitudinal_analyzer.recommendation_for(result['prediction'], _event_history(current_user.id, age_months))
        event = FirstVoiceEvent(user_id=current_user.id, age_months=age_months, demo_key=demo_key, predicted_cause=result['prediction'], classifier_confidence=result['confidence'], recommendation=recommendation, audio_reference=audio_reference, context_json=json.dumps({'source': source}))
        db.session.add(event)
        db.session.flush()
        db.session.add(CareEvent(user_id=current_user.id, timestamp=event.timestamp, event_type='FirstVoice cry', cause=result['prediction'], confidence=result['confidence'], notes=f'FirstVoice event #{event.id}\nSuggested next step: {recommendation}'))
        db.session.commit()
        suggestions = get_suggestions(result['prediction'])
        return jsonify({
            'event': _serialize_event(event),
            'prediction': result,
            'suggestions': suggestions,
            'medical_alert': None,
            'dashboard': _dashboard_data(age_months),
            # Original response keys remain available to existing clients/tests.
            'emotion': result['prediction'],
            'confidence': result['confidence'],
        }), 200
    except Exception as e:
        print(f"Error during analysis: {e}")
        return jsonify({"error": "Analysis failed", "details": str(e)}), 500


@app.route('/api/firstvoice/dashboard', methods=['GET'])
@login_required
def firstvoice_dashboard():
    return jsonify(_dashboard_data(request.args.get('age_months'), request.args.get('period', 'week'))), 200


@app.route('/api/firstvoice/feedback', methods=['POST'])
@login_required
def firstvoice_feedback():
    data = request.get_json(silent=True) or {}
    event = FirstVoiceEvent.query.filter_by(id=data.get('event_id'), user_id=current_user.id).first()
    if not event:
        return jsonify({'error': 'Classification event not found'}), 404
    confirmed = bool(data.get('confirmed'))
    corrected = data.get('corrected_cause')
    event.feedback_confirmed = confirmed
    event.parent_feedback = event.predicted_cause if confirmed else corrected
    db.session.commit()
    return jsonify({'event': _serialize_event(event), 'dashboard': _dashboard_data(event.age_months)}), 200


@app.route('/api/firstvoice/recommendation-feedback', methods=['POST'])
@login_required
def recommendation_feedback():
    data = request.get_json(silent=True) or {}
    event = FirstVoiceEvent.query.filter_by(id=data.get('event_id'), user_id=current_user.id).first()
    if not event:
        return jsonify({'error': 'Recommendation event not found'}), 404
    event.recommendation_feedback = bool(data.get('helpful'))
    event.recommendation_detail = data.get('detail')
    db.session.commit()
    return jsonify({'event': _serialize_event(event), 'dashboard': _dashboard_data(event.age_months)}), 200


@app.route('/api/firstvoice/profile', methods=['PUT'])
@login_required
def update_firstvoice_profile():
    profile = _get_profile(current_user.id)
    data = request.get_json(silent=True) or {}
    profile.name = data.get('name', profile.name)[:100]
    profile.age_months = max(0, min(18, float(data.get('age_months', profile.age_months))))
    profile.gender = data.get('gender', profile.gender)[:50]
    profile.feeding_interval = data.get('feeding_interval', profile.feeding_interval)[:80]
    db.session.commit()
    return jsonify(_dashboard_data(profile.age_months)), 200

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
    event = CareEvent.query.get(event_id)
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
    event = CareEvent.query.get(event_id)
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
    app.run(host='0.0.0.0', debug=True, port=5002)
