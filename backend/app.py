from flask import Flask, request, jsonify, send_from_directory, redirect, url_for
from flask_cors import CORS
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
import os
import datetime
from analyzers.audio import analyze_audio
from analyzers.vision import analyze_vision
from analyzers.history import get_historical_context, get_suggestions
from models import db, User, CareEvent

app = Flask(__name__, static_folder='static')
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
    return send_from_directory('static', 'login.html')

@app.route('/signup')
def signup_page():
    if current_user.is_authenticated:
        return redirect('/')
    return send_from_directory('static', 'signup.html')

# --- Protected Routes ---
@app.route('/')
@login_required
def index():
    return send_from_directory('static', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    # Public assets for login/signup pages
    return send_from_directory('static', path)

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "time": datetime.datetime.now().isoformat()}), 200

@app.route('/api/analyze', methods=['POST'])
@login_required
def analyze():
    try:
        # In a real scenario, we would handle multipart/form-data here
        # data = request.json
        
        # Mocking input type for now based on request
        # Assuming we receive a JSON with a 'type' field for testing, or files
        
        # mock inputs
        audio_result = analyze_audio("mock_path")
        vision_result = analyze_vision("mock_path")
        
        # Determine dominant result (mock logic: prioritize pain > hunger)
        combined_emotion = audio_result['type'] # simplified
        confidence = audio_result['confidence']
        
        # Get suggestions
        suggestions = get_suggestions(combined_emotion)
        

        # Save to History
        new_event = CareEvent(
            user_id=current_user.id,
            event_type="Analysis",
            cause=combined_emotion,
            confidence=confidence,
            notes=f"Suggestions: {', '.join(suggestions[:2])}..."
        )
        db.session.add(new_event)
        db.session.commit()

        return jsonify({
            "emotion": combined_emotion,
            "confidence": confidence,
            "suggestions": suggestions,
            "medical_alert": None, # implementation pending
            "details": {
                "audio": audio_result,
                "vision": vision_result
            }
        }), 200
    except Exception as e:
        print(f"Error during analysis: {e}")
        return jsonify({"error": "Analysis failed", "details": str(e)}), 500

# --- Calendar/History Routes ---
@app.route('/api/calendar', methods=['GET'])
@login_required
def get_calendar_events():
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
    return send_from_directory('static', 'calendar.html')





@app.route('/auto-monitor')
@login_required
def auto_monitor_page():
    return send_from_directory('static', 'auto_mode.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True, port=5001)
