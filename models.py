from datetime import datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)

    username = db.Column(
        db.String(80),
        unique=True,
        nullable=False,
        index=True,
    )

    email = db.Column(
        db.String(254),
        unique=True,
        nullable=False,
        index=True,
    )

    password_hash = db.Column(
        db.String(255),
        nullable=False,
    )

    # Optional phone details supplied during registration.
    phone_country_code = db.Column(
        db.String(8),
        nullable=True,
    )

    phone_number = db.Column(
        db.String(30),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    reports = db.relationship(
        "VerificationReport",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class VerificationReport(db.Model):
    __tablename__ = "verification_reports"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    # Retained for rental listing URL integration.
    listing_url = db.Column(
        db.String(2048),
        nullable=True,
    )

    address = db.Column(
        db.String(255),
        nullable=False,
    )

    monthly_rent = db.Column(
        db.Float,
        nullable=True,
    )

    bedrooms = db.Column(
        db.Integer,
        nullable=True,
    )

    report_json = db.Column(
        db.Text,
        nullable=False,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    user = db.relationship(
        "User",
        back_populates="reports",
    )
