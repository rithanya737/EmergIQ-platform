"""User model: id, email, password_hash, role (learner/instructor)."""
from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.Enum("learner", "instructor", name="user_role"), nullable=False, default="learner")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    attempts = db.relationship("Attempt", back_populates="user", lazy="dynamic")

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    @property
    def is_instructor(self) -> bool:
        return self.role == "instructor"

    def __repr__(self):
        return f"<User {self.id} {self.email}>"
