from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    auto_listen_enabled = db.Column(db.Boolean, default=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class CareEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=db.func.now())
    event_type = db.Column(db.String(50), nullable=False) # e.g. "Crying Detected"
    cause = db.Column(db.String(50), nullable=False)      # e.g. "Hungry"
    confidence = db.Column(db.Float)
    notes = db.Column(db.Text)


class InfantProfile(db.Model):
    """Demo profile kept separate from the original calendar data model."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    name = db.Column(db.String(100), default='Luca', nullable=False)
    age_months = db.Column(db.Float, default=4.0, nullable=False)
    gender = db.Column(db.String(50), default='')
    feeding_interval = db.Column(db.String(80), default='Every 2-3 hours')
    sleep_note = db.Column(db.String(120), default='')


class InfantProfilePhoto(db.Model):
    """Private uploaded profile image metadata, kept separate for easy demo upgrades."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    filename = db.Column(db.String(180), nullable=False)
    content_type = db.Column(db.String(80), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=db.func.now(), nullable=False)


class FirstVoiceEvent(db.Model):
    """One cry classification plus the two parent feedback loops."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=db.func.now(), nullable=False)
    age_months = db.Column(db.Float, nullable=False)
    demo_key = db.Column(db.String(40), default='evening', nullable=False)
    predicted_cause = db.Column(db.String(50), nullable=False)
    classifier_confidence = db.Column(db.Float, nullable=False)
    parent_feedback = db.Column(db.String(50))
    feedback_confirmed = db.Column(db.Boolean)
    recommendation = db.Column(db.Text)
    recommendation_feedback = db.Column(db.Boolean)
    recommendation_detail = db.Column(db.String(80))
    audio_reference = db.Column(db.String(120), default='demo-cry')
    context_json = db.Column(db.Text, default='{}')
