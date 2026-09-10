"""Room, RoomMember models — a host-created assessment session with a real
lifecycle (WAITING -> ACTIVE -> COMPLETED, or EXPIRED / CLOSED), matching
the RAMCO reference app's room behaviour."""
import json
import random
import string
from datetime import date, datetime

from app.extensions import db

MAX_ASSESSMENT_DAYS = 7
EXPIRY_WARNING_DAYS = 2


def generate_room_code(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choices(alphabet, k=length))


class Room(db.Model):
    __tablename__ = "rooms"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(12), unique=True, nullable=False, default=generate_room_code)
    name = db.Column(db.String(150), nullable=False)
    instructor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    difficulty = db.Column(db.String(20), default="standard")
    participant_limit = db.Column(db.Integer, nullable=False, default=50)
    level_set_json = db.Column("level_set", db.Text, nullable=False)  # JSON list of ints (currently always one)
    from_date = db.Column(db.Date, nullable=True)
    to_date = db.Column(db.Date, nullable=True)
    status = db.Column(
        db.Enum("waiting", "active", "completed", "expired", "closed", name="room_status"),
        default="waiting",
        nullable=False,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    closed_at = db.Column(db.DateTime, nullable=True)

    instructor = db.relationship("User", foreign_keys=[instructor_id])
    members = db.relationship("RoomMember", back_populates="room", lazy="dynamic")
    attempts = db.relationship("Attempt", back_populates="room", lazy="dynamic")

    @property
    def level_set(self) -> list[int]:
        return json.loads(self.level_set_json) if self.level_set_json else []

    @level_set.setter
    def level_set(self, levels: list[int]) -> None:
        self.level_set_json = json.dumps(sorted(levels))

    @property
    def window_days(self) -> int:
        if not (self.from_date and self.to_date):
            return 0
        return (self.to_date - self.from_date).days + 1

    @property
    def window_open(self) -> bool:
        today = date.today()
        return bool(self.from_date and self.to_date and self.from_date <= today <= self.to_date)

    @property
    def days_left(self):
        return (self.to_date - date.today()).days if self.to_date else None

    @property
    def expiring_soon(self) -> bool:
        return (
            self.status in ("waiting", "active")
            and self.days_left is not None
            and self.days_left <= EXPIRY_WARNING_DAYS
        )

    def participant_count(self) -> int:
        return self.members.count()

    def refresh_expiry(self) -> bool:
        """Flip to 'expired' once the assessment window has passed.
        Returns True if this call just changed the status (caller commits)."""
        if self.status in ("waiting", "active") and self.to_date and date.today() > self.to_date:
            self.status = "expired"
            return True
        return False

    def __repr__(self):
        return f"<Room {self.code}>"


class RoomMember(db.Model):
    __tablename__ = "room_members"
    __table_args__ = (db.UniqueConstraint("room_id", "user_id", name="uq_room_member"),)

    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("rooms.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)  # set once every level in level_set is completed

    room = db.relationship("Room", back_populates="members")
    user = db.relationship("User")

    def levels_completed(self) -> set:
        from app.game.models import Attempt
        rows = (
            Attempt.query.filter_by(room_id=self.room_id, user_id=self.user_id)
            .filter(Attempt.completed_at.isnot(None))
            .with_entities(Attempt.level)
            .all()
        )
        return {r[0] for r in rows}

    def is_finished(self) -> bool:
        return set(self.room.level_set).issubset(self.levels_completed())

    def latest_attempt(self):
        from app.game.models import Attempt
        return (
            Attempt.query.filter_by(room_id=self.room_id, user_id=self.user_id)
            .order_by(Attempt.id.desc())
            .first()
        )
