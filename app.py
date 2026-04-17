from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from functools import wraps
import math
import os
from pathlib import Path
from urllib.parse import quote_plus

from flask import Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, inspect, or_, text
from sqlalchemy.orm import joinedload
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
import secrets


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "instance" / "eventmatch.db"
AVATAR_UPLOAD_DIR = BASE_DIR / "static" / "uploads" / "avatars"
ALLOWED_AVATAR_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_AVATAR_BYTES = 4 * 1024 * 1024
DEFAULT_TRACKING_MODE = "GPS ETA is shared only for confirmed attendees."
DEFAULT_BIO = "Add a short intro so people know what kind of plans you actually enjoy."
DEFAULT_TRACKING_OPTION = "Visible only after I join"
DEFAULT_RELIABILITY_SCORE = 80
DEFAULT_EVENT_DURATION_MINUTES = 120
DEFAULT_VENUE_COORDS = {"lat": -34.6037, "lng": -58.3816}
VALID_ETA_STATES = {"checked_in", "arriving_soon", "on_track", "delayed"}
ATTENDANCE_OUTCOMES = {"pending", "on_time", "late", "no_show"}
ATTENDANCE_SCORE_MAP = {"on_time": 100, "late": 65, "no_show": 20}
RELIABILITY_BASELINE_EVENTS = 4
STATUS_PRIORITY = {"joined": 0, "interested": 1, "waitlist": 2}
CATEGORIES = [
    "Workout",
    "Study",
    "Social",
    "Networking",
    "Wellness",
]
INTEREST_OPTIONS = [
    "Workout",
    "Study",
    "Running",
    "Gym partner",
    "Language exchange",
    "Coffee chats",
    "Dinner plans",
    "Coworking",
    "Weekend trips",
    "Sports",
    "Wellness",
    "Events",
]
TRACKING_OPTIONS = [
    "Visible only after I join",
    "Visible only to event hosts",
    "Do not show ETA automatically",
]
RELIABILITY_THRESHOLDS = [
    {"value": "50", "label": "50%+", "description": "Open group, low commitment."},
    {"value": "65", "label": "65%+", "description": "Balanced option for most plans."},
    {"value": "80", "label": "80%+", "description": "For time-sensitive activities."},
]

db = SQLAlchemy()


def get_database_uri() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
        elif database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return database_url
    instance_connection_name = os.getenv("INSTANCE_CONNECTION_NAME", "").strip()
    db_name = os.getenv("DB_NAME", "").strip()
    db_user = os.getenv("DB_USER", "").strip()
    if instance_connection_name and db_name and db_user:
        db_password = quote_plus(os.getenv("DB_PASSWORD", ""))
        socket_dir = quote_plus(f"/cloudsql/{instance_connection_name}")
        return f"postgresql+psycopg://{quote_plus(db_user)}:{db_password}@/{quote_plus(db_name)}?host={socket_dir}"
    return f"sqlite:///{DEFAULT_DB_PATH}"


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def eta_snapshot_for_distance(distance_km: float) -> tuple[str, str]:
    distance = max(distance_km, 0.0)
    if distance < 0.15:
        return "checked_in", "At venue"

    if distance < 1.5:
        speed_kmh = 9
        buffer_minutes = 2
    elif distance < 6:
        speed_kmh = 18
        buffer_minutes = 4
    else:
        speed_kmh = 28
        buffer_minutes = 6

    minutes_away = max(3, math.ceil((distance / speed_kmh) * 60 + buffer_minutes))
    if minutes_away <= 8:
        status = "arriving_soon"
        prefix = "Arriving soon"
    elif minutes_away <= 25:
        status = "on_track"
        prefix = "On track"
    else:
        status = "delayed"
        prefix = "Delayed"

    if distance < 1:
        distance_label = f"{round(distance * 1000):.0f} m"
    else:
        distance_label = f"{distance:.1f} km"
    return status, f"{prefix} • {minutes_away} min away ({distance_label})"


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    email = db.Column(db.String(120), nullable=True)
    password_hash = db.Column(db.Text, nullable=True)
    city = db.Column(db.String(80), nullable=False)
    bio = db.Column(db.Text, nullable=False, default=DEFAULT_BIO)
    interests_csv = db.Column(db.Text, default="", nullable=False)
    gps_tracking = db.Column(db.String(120), nullable=False, default=DEFAULT_TRACKING_OPTION)
    home_area = db.Column(db.String(80), nullable=False)
    avatar_url = db.Column(db.Text, nullable=True)
    reliability_score = db.Column(db.Integer, nullable=False, default=DEFAULT_RELIABILITY_SCORE)

    hosted_activities = db.relationship("Activity", back_populates="host", lazy="selectin")
    participations = db.relationship("Participation", back_populates="user", lazy="selectin")
    messages = db.relationship("Message", back_populates="author", lazy="selectin")

    @property
    def interests(self) -> list[str]:
        return [item for item in self.interests_csv.split("|") if item]


class Activity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(40), nullable=False)
    activity_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    location = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    min_reliability = db.Column(db.Integer, nullable=False)
    recurring_weekly = db.Column(db.Boolean, nullable=False, default=False)
    tracking_mode = db.Column(db.String(160), nullable=False, default=DEFAULT_TRACKING_MODE)
    venue_lat = db.Column(db.Float, nullable=False, default=DEFAULT_VENUE_COORDS["lat"])
    venue_lng = db.Column(db.Float, nullable=False, default=DEFAULT_VENUE_COORDS["lng"])
    host_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    host = db.relationship("User", back_populates="hosted_activities", lazy="joined")
    roles = db.relationship(
        "Role",
        back_populates="activity",
        cascade="all, delete-orphan",
        order_by="Role.id",
        lazy="selectin",
    )
    participations = db.relationship(
        "Participation",
        back_populates="activity",
        cascade="all, delete-orphan",
        order_by="Participation.created_at",
        lazy="selectin",
    )
    messages = db.relationship(
        "Message",
        back_populates="activity",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
        lazy="selectin",
    )


class Role(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.Integer, db.ForeignKey("activity.id"), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    role_type = db.Column(db.String(20), nullable=False)
    needed_count = db.Column(db.Integer, nullable=False)

    activity = db.relationship("Activity", back_populates="roles", lazy="joined")
    assigned_participations = db.relationship("Participation", back_populates="assigned_role", lazy="selectin")


class Participation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.Integer, db.ForeignKey("activity.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.String(160), nullable=True)
    assigned_role_id = db.Column(db.Integer, db.ForeignKey("role.id"), nullable=True)
    eta_label = db.Column(db.String(80), nullable=False, default="ETA hidden")
    eta_status = db.Column(db.String(20), nullable=False, default="on_track")
    attendance_outcome = db.Column(db.String(20), nullable=False, default="pending")
    attendance_recorded_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    activity = db.relationship("Activity", back_populates="participations", lazy="joined")
    user = db.relationship("User", back_populates="participations", lazy="joined")
    assigned_role = db.relationship("Role", back_populates="assigned_participations", lazy="joined")

    __table_args__ = (UniqueConstraint("activity_id", "user_id", name="uq_participation_activity_user"),)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.Integer, db.ForeignKey("activity.id"), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    activity = db.relationship("Activity", back_populates="messages", lazy="joined")
    author = db.relationship("User", back_populates="messages", lazy="joined")


class Friendship(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    addressee_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    requester = db.relationship("User", foreign_keys=[requester_id], lazy="joined")
    addressee = db.relationship("User", foreign_keys=[addressee_id], lazy="joined")

    __table_args__ = (UniqueConstraint("requester_id", "addressee_id", name="uq_friendship_pair"),)


class Invite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.Integer, db.ForeignKey("activity.id"), nullable=False)
    inviter_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    invitee_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    activity = db.relationship("Activity", lazy="joined")
    inviter = db.relationship("User", foreign_keys=[inviter_id], lazy="joined")
    invitee = db.relationship("User", foreign_keys=[invitee_id], lazy="joined")

    __table_args__ = (UniqueConstraint("activity_id", "invitee_id", name="uq_invite_activity_invitee"),)


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    DEFAULT_DB_PATH.parent.mkdir(exist_ok=True)
    AVATAR_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-key"),
        SQLALCHEMY_DATABASE_URI=get_database_uri(),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)

    with app.app_context():
        db.create_all()
        ensure_schema()

    register_routes(app)
    return app


def ensure_schema() -> None:
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    if "user" in tables:
        user_columns = {column["name"] for column in inspector.get_columns("user")}
        with db.engine.begin() as connection:
            if "email" not in user_columns:
                connection.execute(text('ALTER TABLE "user" ADD COLUMN email VARCHAR(120)'))
            if "password_hash" not in user_columns:
                connection.execute(text('ALTER TABLE "user" ADD COLUMN password_hash TEXT'))
            if "avatar_url" not in user_columns:
                connection.execute(text('ALTER TABLE "user" ADD COLUMN avatar_url TEXT'))
    if "activity" in tables:
        activity_columns = {column["name"] for column in inspector.get_columns("activity")}
        with db.engine.begin() as connection:
            if "recurring_weekly" not in activity_columns:
                connection.execute(text("ALTER TABLE activity ADD COLUMN recurring_weekly BOOLEAN DEFAULT 0"))
    if "participation" in tables:
        participation_columns = {column["name"] for column in inspector.get_columns("participation")}
        with db.engine.begin() as connection:
            if "attendance_outcome" not in participation_columns:
                connection.execute(text("ALTER TABLE participation ADD COLUMN attendance_outcome VARCHAR(20) DEFAULT 'pending'"))
            if "attendance_recorded_at" not in participation_columns:
                connection.execute(text("ALTER TABLE participation ADD COLUMN attendance_recorded_at DATETIME"))


def register_routes(app: Flask) -> None:
    @app.context_processor
    def inject_template_state():
        current_user = get_current_user(optional=True)
        pending_invite_count = (
            Invite.query.filter_by(invitee_id=current_user.id, status="pending").count()
            if current_user
            else 0
        )
        pending_friend_count = (
            Friendship.query.filter_by(addressee_id=current_user.id, status="pending").count()
            if current_user
            else 0
        )
        return {
            "viewer": serialize_user(current_user) if current_user else None,
            "interest_options": INTEREST_OPTIONS,
            "pending_invite_count": pending_invite_count,
            "pending_friend_count": pending_friend_count,
        }

    @app.get("/healthz")
    def healthcheck():
        return {"ok": True}, 200

    @app.route("/")
    def landing():
        if get_current_user(optional=True):
            return redirect(url_for("feed"))
        return render_template("landing.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if get_current_user(optional=True):
            return redirect(url_for("feed"))

        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = User.query.filter(db.func.lower(User.email) == email).first()
            if user is None or not user.password_hash or not check_password_hash(user.password_hash, password):
                flash("Incorrect email or password.", "error")
                return render_template("login.html", email=email), 400

            session.clear()
            session["user_id"] = user.id
            flash("Welcome back.", "success")
            return redirect(resolve_next_url(request.args.get("next")) or url_for("feed"))

        return render_template("login.html", email="")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if get_current_user(optional=True):
            return redirect(url_for("feed"))

        form_data = {
            "name": "",
            "email": "",
            "city": "",
            "home_area": "",
        }
        if request.method == "POST":
            form_data = {
                "name": request.form.get("name", "").strip(),
                "email": request.form.get("email", "").strip().lower(),
                "city": request.form.get("city", "").strip(),
                "home_area": request.form.get("home_area", "").strip(),
            }
            password = request.form.get("password", "")
            errors = validate_registration_form(form_data, password)
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template("register.html", form_data=form_data), 400

            user = User(
                name=form_data["name"],
                email=form_data["email"],
                password_hash=generate_password_hash(password),
                city=form_data["city"],
                home_area=form_data["home_area"],
                bio=DEFAULT_BIO,
                interests_csv="",
                gps_tracking=DEFAULT_TRACKING_OPTION,
                reliability_score=DEFAULT_RELIABILITY_SCORE,
                avatar_url="",
            )
            db.session.add(user)
            db.session.commit()
            session.clear()
            session["user_id"] = user.id
            flash("Account created. Finish your profile before joining plans.", "success")
            return redirect(url_for("edit_profile"))

        return render_template("register.html", form_data=form_data)

    @app.post("/logout")
    def logout():
        session.clear()
        flash("You have been signed out.", "info")
        return redirect(url_for("landing"))

    @app.route("/feed")
    @login_required
    def feed():
        current_user = require_current_user()
        selected_category = request.args.get("category", "All")
        selected_date = request.args.get("date", "")

        activities = load_activities()
        if selected_category != "All":
            activities = [activity for activity in activities if activity.category == selected_category]
        if selected_date:
            activities = [activity for activity in activities if activity.activity_date.isoformat() == selected_date]

        serialized = [serialize_activity(activity, current_user) for activity in activities]
        weekly_events = [activity for activity in serialized if activity["is_weekly"]]
        upcoming_events = [activity for activity in serialized if not activity["is_weekly"]]
        categories = ["All"] + sorted({activity.category for activity in Activity.query.all()})

        return render_template(
            "feed.html",
            weekly_events=weekly_events,
            upcoming_events=upcoming_events,
            categories=categories,
            selected_category=selected_category,
            selected_date=selected_date,
            today=date.today().isoformat(),
        )

    @app.route("/activities/new", methods=["GET", "POST"])
    @login_required
    def create_activity():
        current_user = require_current_user()
        form_data = empty_activity_form_defaults()

        if request.method == "POST":
            cleaned, errors = validate_activity_form(request.form)
            form_data = cleaned
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template(
                    "create_activity.html",
                    categories=CATEGORIES,
                    reliability_thresholds=RELIABILITY_THRESHOLDS,
                    form_data=form_data,
                    form_mode="create",
                ), 400

            activity = Activity(
                title=cleaned["title"],
                category=cleaned["category"],
                activity_date=cleaned["activity_date"],
                start_time=cleaned["start_time"],
                location=cleaned["location"],
                description=cleaned["description"],
                capacity=cleaned["capacity_value"],
                min_reliability=cleaned["min_reliability_value"],
                recurring_weekly=cleaned["recurring_weekly"],
                tracking_mode=DEFAULT_TRACKING_MODE,
                venue_lat=cleaned["venue_lat"],
                venue_lng=cleaned["venue_lng"],
                host=current_user,
            )
            db.session.add(activity)
            db.session.flush()

            replace_activity_roles(activity, cleaned["roles_clean"])

            db.session.add(
                Participation(
                    activity=activity,
                    user=current_user,
                    status="joined",
                    reason="Host",
                    eta_label="ETA hidden",
                    eta_status="on_track",
                )
            )
            db.session.commit()
            flash("Event created. You can manage attendance from the host dashboard.", "success")
            return redirect(url_for("host_dashboard", activity_id=activity.id))

        return render_template(
            "create_activity.html",
            categories=CATEGORIES,
            reliability_thresholds=RELIABILITY_THRESHOLDS,
            form_data=form_data,
            form_mode="create",
        )

    @app.route("/activities/<int:activity_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_activity(activity_id: int):
        current_user = require_current_user()
        activity = get_activity_or_404(activity_id)
        require_host_access(activity, current_user)

        form_data = activity_form_defaults(activity)
        if request.method == "POST":
            cleaned, errors = validate_activity_form(
                request.form,
                default_coords={"lat": activity.venue_lat, "lng": activity.venue_lng},
            )
            if cleaned.get("capacity_value", activity.capacity) < count_joined(activity):
                errors.append("Capacity cannot be lower than the number of confirmed attendees.")
            form_data = cleaned
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template(
                    "create_activity.html",
                    categories=CATEGORIES,
                    reliability_thresholds=RELIABILITY_THRESHOLDS,
                    form_data=form_data,
                    form_mode="edit",
                    activity=serialize_activity(activity, current_user),
                ), 400

            activity.title = cleaned["title"]
            activity.category = cleaned["category"]
            activity.activity_date = cleaned["activity_date"]
            activity.start_time = cleaned["start_time"]
            activity.location = cleaned["location"]
            activity.description = cleaned["description"]
            activity.capacity = cleaned["capacity_value"]
            activity.min_reliability = cleaned["min_reliability_value"]
            activity.recurring_weekly = cleaned["recurring_weekly"]
            activity.venue_lat = cleaned["venue_lat"]
            activity.venue_lng = cleaned["venue_lng"]
            replace_activity_roles(activity, cleaned["roles_clean"])
            db.session.commit()
            flash("Event updated.", "success")
            return redirect(url_for("activity_detail", activity_id=activity.id))

        return render_template(
            "create_activity.html",
            categories=CATEGORIES,
            reliability_thresholds=RELIABILITY_THRESHOLDS,
            form_data=form_data,
            form_mode="edit",
            activity=serialize_activity(activity, current_user),
        )

    @app.post("/activities/<int:activity_id>/delete")
    @login_required
    def delete_activity(activity_id: int):
        current_user = require_current_user()
        activity = get_activity_or_404(activity_id)
        require_host_access(activity, current_user)
        db.session.delete(activity)
        db.session.commit()
        flash("Event deleted.", "success")
        return redirect(url_for("feed"))

    @app.route("/activities/<int:activity_id>")
    @login_required
    def activity_detail(activity_id: int):
        current_user = require_current_user()
        activity = get_activity_or_404(activity_id)
        return render_template("activity_detail.html", activity=serialize_activity(activity, current_user))

    @app.get("/activities/<int:activity_id>/calendar.ics")
    @login_required
    def download_calendar_invite(activity_id: int):
        activity = get_activity_or_404(activity_id)
        current_user = require_current_user()
        ics_body = build_ics_invite(activity)
        response = Response(ics_body, mimetype="text/calendar; charset=utf-8")
        response.headers["Content-Disposition"] = f'attachment; filename="eventmatch-{activity.id}.ics"'
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/activities/<int:activity_id>/join")
    @login_required
    def join_activity(activity_id: int):
        current_user = require_current_user()
        activity = get_activity_or_404(activity_id)
        participation = get_or_create_participation(activity, current_user)
        status, reason = apply_join_request(activity, participation)
        promote_waitlist(activity)
        db.session.commit()
        flash("You are confirmed for this plan." if status == "joined" else reason, "success" if status == "joined" else "warning")
        return redirect(url_for("activity_detail", activity_id=activity.id))

    @app.post("/activities/<int:activity_id>/interest")
    @login_required
    def mark_interest(activity_id: int):
        current_user = require_current_user()
        activity = get_activity_or_404(activity_id)
        participation = get_or_create_participation(activity, current_user)
        if participation.status == "joined":
            flash("You already have a confirmed spot.", "info")
        else:
            participation.status = "interested"
            participation.reason = "Following this plan"
            participation.assigned_role = None
            participation.eta_label = "Not committed"
            participation.eta_status = "on_track"
            db.session.commit()
            flash("Saved. You will stay visible without taking a seat yet.", "success")
        return redirect(url_for("activity_detail", activity_id=activity.id))

    @app.post("/activities/<int:activity_id>/leave")
    @login_required
    def leave_activity(activity_id: int):
        current_user = require_current_user()
        activity = get_activity_or_404(activity_id)
        participation = find_participation(activity, current_user.id)
        if participation is None:
            flash("You are not part of this event.", "info")
            return redirect(url_for("activity_detail", activity_id=activity.id))
        if activity.host_id == current_user.id:
            flash("Hosts cannot leave their own event.", "error")
            return redirect(url_for("activity_detail", activity_id=activity.id))

        db.session.delete(participation)
        db.session.flush()
        promote_waitlist(activity)
        db.session.commit()
        flash("You have left the event.", "success")
        return redirect(url_for("activity_detail", activity_id=activity.id))

    @app.post("/activities/<int:activity_id>/messages")
    @login_required
    def post_message(activity_id: int):
        current_user = require_current_user()
        activity = get_activity_or_404(activity_id)
        body = request.form.get("body", "").strip()
        if not body:
            flash("Message cannot be empty.", "error")
            return redirect(url_for("activity_detail", activity_id=activity.id))

        participation = find_participation(activity, current_user.id)
        if participation is None and activity.host_id != current_user.id:
            flash("Join or follow the event before using the chat.", "error")
            return redirect(url_for("activity_detail", activity_id=activity.id))

        db.session.add(Message(activity=activity, author=current_user, body=body[:400]))
        db.session.commit()
        flash("Message sent.", "success")
        return redirect(url_for("activity_detail", activity_id=activity.id))

    @app.post("/activities/<int:activity_id>/eta")
    @login_required
    def update_eta(activity_id: int):
        current_user = require_current_user()
        activity = get_activity_or_404(activity_id)
        participation = find_participation(activity, current_user.id)
        if participation is None or participation.status != "joined":
            return jsonify({"error": "Join the event before sharing ETA."}), 400

        payload = request.get_json(silent=True) or {}
        distance_km_raw = payload.get("distance_km")
        if distance_km_raw is not None:
            try:
                distance_km = float(distance_km_raw)
            except (TypeError, ValueError):
                return jsonify({"error": "Invalid ETA payload."}), 400
            if not math.isfinite(distance_km):
                return jsonify({"error": "Invalid ETA payload."}), 400
            eta_status, eta_label = eta_snapshot_for_distance(distance_km)
        else:
            eta_status = str(payload.get("eta_status", "")).strip()
            eta_label = str(payload.get("eta_label", "")).strip()
            if eta_status not in VALID_ETA_STATES or not eta_label:
                return jsonify({"error": "Invalid ETA payload."}), 400

        participation.eta_status = eta_status
        participation.eta_label = eta_label[:80]
        db.session.commit()
        return jsonify({"ok": True, "eta_status": eta_status, "eta_label": participation.eta_label})

    @app.route("/activities/<int:activity_id>/host")
    @login_required
    def host_dashboard(activity_id: int):
        current_user = require_current_user()
        activity = get_activity_or_404(activity_id)
        require_host_access(activity, current_user)
        return render_template("host_dashboard.html", activity=serialize_activity(activity, current_user))

    @app.post("/activities/<int:activity_id>/host/attendance/<int:participation_id>")
    @login_required
    def record_attendance_outcome(activity_id: int, participation_id: int):
        current_user = require_current_user()
        activity = get_activity_or_404(activity_id)
        require_host_access(activity, current_user)

        if not attendance_review_open(activity):
            flash("Attendance review opens once the event start time has passed.", "warning")
            return redirect(url_for("host_dashboard", activity_id=activity.id))

        participation = Participation.query.filter_by(id=participation_id, activity_id=activity.id).first_or_404()
        if participation.status != "joined":
            flash("Only confirmed attendees can receive an attendance outcome.", "error")
            return redirect(url_for("host_dashboard", activity_id=activity.id))
        if participation.user_id == activity.host_id:
            flash("The host seat is not reviewed from this control.", "info")
            return redirect(url_for("host_dashboard", activity_id=activity.id))

        outcome = request.form.get("attendance_outcome", "").strip()
        if outcome not in ATTENDANCE_OUTCOMES or outcome == "pending":
            flash("Choose a valid attendance outcome.", "error")
            return redirect(url_for("host_dashboard", activity_id=activity.id))

        participation.attendance_outcome = outcome
        participation.attendance_recorded_at = utcnow()
        recalculate_reliability(participation.user)
        db.session.commit()
        flash("Attendance outcome saved and reliability updated.", "success")
        return redirect(url_for("host_dashboard", activity_id=activity.id))

    @app.post("/activities/<int:activity_id>/host/participants/<int:participation_id>")
    @login_required
    def update_participant(activity_id: int, participation_id: int):
        current_user = require_current_user()
        activity = get_activity_or_404(activity_id)
        require_host_access(activity, current_user)

        participation = Participation.query.filter_by(id=participation_id, activity_id=activity.id).first_or_404()
        new_status = request.form.get("status", participation.status)
        assigned_role_id = request.form.get("assigned_role_id", "").strip()

        if new_status == "remove":
            had_scored_attendance = participation.attendance_outcome in ATTENDANCE_SCORE_MAP
            if participation.user_id == current_user.id:
                flash("The host seat cannot be removed.", "error")
                return redirect(url_for("host_dashboard", activity_id=activity.id))
            db.session.delete(participation)
            db.session.flush()
            if had_scored_attendance:
                recalculate_reliability(participation.user)
            promote_waitlist(activity)
            db.session.commit()
            flash("Participant removed.", "success")
            return redirect(url_for("host_dashboard", activity_id=activity.id))

        if new_status not in {"joined", "interested", "waitlist"}:
            flash("Invalid participant update.", "error")
            return redirect(url_for("host_dashboard", activity_id=activity.id))

        if assigned_role_id:
            role = Role.query.filter_by(id=int(assigned_role_id), activity_id=activity.id).first()
            if role is None:
                flash("Selected role does not belong to this event.", "error")
                return redirect(url_for("host_dashboard", activity_id=activity.id))
            participation.assigned_role = role
        else:
            participation.assigned_role = None

        if new_status == "joined":
            if not can_confirm_join(activity, participation, host_override=True):
                participation.status = "waitlist"
                participation.reason = "Event is already at capacity."
                clear_attendance_outcome(participation)
                flash("No seats left. The participant stays on the waitlist.", "warning")
            else:
                participation.status = "joined"
                participation.reason = "Confirmed by host"
                if participation.eta_label == "Not committed":
                    participation.eta_label = "ETA hidden"
                clear_attendance_outcome(participation)
                flash("Participant confirmed.", "success")
        elif new_status == "interested":
            participation.status = "interested"
            participation.reason = "Moved to interested by host"
            participation.eta_label = "Not committed"
            participation.eta_status = "on_track"
            clear_attendance_outcome(participation)
            flash("Participant moved to interested.", "success")
        else:
            participation.status = "waitlist"
            participation.reason = "Pending host review"
            participation.eta_label = "Waitlisted"
            participation.eta_status = "delayed"
            clear_attendance_outcome(participation)
            flash("Participant moved to the waitlist.", "success")

        promote_waitlist(activity)
        recalculate_reliability(participation.user)
        db.session.commit()
        return redirect(url_for("host_dashboard", activity_id=activity.id))

    @app.route("/profile")
    @login_required
    def profile():
        current_user = require_current_user()
        upcoming = (
            Activity.query.join(Participation)
            .filter(
                Participation.user_id == current_user.id,
                Participation.status == "joined",
                Activity.activity_date >= date.today(),
            )
            .options(joinedload(Activity.host), joinedload(Activity.roles), joinedload(Activity.participations))
            .order_by(Activity.recurring_weekly.desc(), Activity.activity_date, Activity.start_time)
            .all()
        )
        hosted = (
            Activity.query.filter_by(host_id=current_user.id)
            .options(joinedload(Activity.roles), joinedload(Activity.participations))
            .order_by(Activity.recurring_weekly.desc(), Activity.activity_date, Activity.start_time)
            .all()
        )
        recurring = [serialize_activity_summary(activity) for activity in upcoming if activity.recurring_weekly]
        return render_template(
            "profile.html",
            current_user=serialize_user(current_user),
            upcoming=[serialize_activity_summary(activity) for activity in upcoming],
            hosted=[serialize_activity_summary(activity) for activity in hosted],
            recurring=recurring,
        )

    @app.route("/profile/edit", methods=["GET", "POST"])
    @login_required
    def edit_profile():
        current_user = require_current_user()
        form_data = profile_form_defaults(current_user)
        if request.method == "POST":
            form_data = extract_profile_form(request.form, current_user)
            errors = validate_profile_form(current_user, form_data)

            uploaded_file = request.files.get("avatar_file")
            saved_upload_path: str | None = None
            if uploaded_file and uploaded_file.filename:
                saved_upload_path, upload_errors = save_avatar_upload(uploaded_file, current_user)
                errors.extend(upload_errors)

            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template("edit_profile.html", form_data=form_data, tracking_options=TRACKING_OPTIONS), 400

            current_user.name = form_data["name"]
            current_user.email = form_data["email"]
            current_user.city = form_data["city"]
            current_user.home_area = form_data["home_area"]
            current_user.bio = form_data["bio"]
            current_user.avatar_url = saved_upload_path if saved_upload_path else form_data["avatar_url"]
            current_user.gps_tracking = form_data["gps_tracking"]
            current_user.interests_csv = "|".join(form_data["interests"])
            db.session.commit()
            flash("Profile updated.", "success")
            return redirect(url_for("profile"))

        return render_template("edit_profile.html", form_data=form_data, tracking_options=TRACKING_OPTIONS)

    @app.route("/friends")
    @login_required
    def friends_page():
        current_user = require_current_user()

        friend_rows = Friendship.query.filter(
            Friendship.status == "accepted",
            or_(Friendship.requester_id == current_user.id, Friendship.addressee_id == current_user.id),
        ).all()
        friends_list = []
        for row in friend_rows:
            other = row.addressee if row.requester_id == current_user.id else row.requester
            friends_list.append(
                {
                    "id": other.id,
                    "friendship_id": row.id,
                    "name": other.name,
                    "initials": initials_for_name(other.name),
                    "home_area": other.home_area,
                    "reliability": other.reliability_score,
                }
            )
        friends_list.sort(key=lambda item: item["name"].lower())
        incoming = [
            {
                "id": row.id,
                "user_name": row.requester.name,
                "user_initials": initials_for_name(row.requester.name),
                "home_area": row.requester.home_area,
            }
            for row in pending_incoming_friend_requests(current_user.id)
        ]
        outgoing = [
            {
                "id": row.id,
                "user_name": row.addressee.name,
                "user_initials": initials_for_name(row.addressee.name),
            }
            for row in pending_outgoing_friend_requests(current_user.id)
        ]
        pending_invites = [serialize_invite(invite) for invite in pending_invites_for(current_user.id)]

        return render_template(
            "friends.html",
            friends_list=friends_list,
            incoming=incoming,
            outgoing=outgoing,
            pending_invites=pending_invites,
        )

    @app.post("/friends/request")
    @login_required
    def send_friend_request():
        current_user = require_current_user()
        identifier = request.form.get("identifier", "").strip()
        if not identifier:
            flash("Enter a name or email to send a friend request.", "error")
            return redirect(url_for("friends_page"))

        query = User.query.filter(
            or_(
                User.name == identifier,
                db.func.lower(User.email) == identifier.lower(),
            )
        )
        target = query.first()
        if target is None:
            flash("No user found with that name or email.", "error")
            return redirect(url_for("friends_page"))
        if target.id == current_user.id:
            flash("You cannot send a friend request to yourself.", "error")
            return redirect(url_for("friends_page"))

        existing = find_friendship(current_user.id, target.id)
        if existing is not None:
            if existing.status == "accepted":
                flash(f"You are already friends with {target.name}.", "info")
            elif existing.requester_id == current_user.id:
                flash("Your friend request is still pending.", "info")
            else:
                flash(f"{target.name} already sent you a friend request — accept it from your friends page.", "info")
            return redirect(url_for("friends_page"))

        db.session.add(Friendship(requester_id=current_user.id, addressee_id=target.id, status="pending"))
        db.session.commit()
        flash(f"Friend request sent to {target.name}.", "success")
        return redirect(url_for("friends_page"))

    @app.post("/friends/<int:friendship_id>/accept")
    @login_required
    def accept_friend_request(friendship_id: int):
        current_user = require_current_user()
        friendship = Friendship.query.filter_by(id=friendship_id, addressee_id=current_user.id).first_or_404()
        if friendship.status != "pending":
            flash("This friend request is no longer pending.", "info")
            return redirect(url_for("friends_page"))
        friendship.status = "accepted"
        db.session.commit()
        flash(f"You and {friendship.requester.name} are now friends.", "success")
        return redirect(url_for("friends_page"))

    @app.post("/friends/<int:friendship_id>/remove")
    @login_required
    def remove_friend(friendship_id: int):
        current_user = require_current_user()
        friendship = Friendship.query.filter(
            Friendship.id == friendship_id,
            or_(Friendship.requester_id == current_user.id, Friendship.addressee_id == current_user.id),
        ).first_or_404()
        db.session.delete(friendship)
        db.session.commit()
        flash("Friend removed.", "success")
        return redirect(url_for("friends_page"))

    @app.post("/activities/<int:activity_id>/invite")
    @login_required
    def invite_friend_to_activity(activity_id: int):
        current_user = require_current_user()
        activity = get_activity_or_404(activity_id)

        viewer_participation = find_participation(activity, current_user.id)
        if activity.host_id != current_user.id and viewer_participation is None:
            flash("Join or follow this event before inviting friends.", "error")
            return redirect(url_for("activity_detail", activity_id=activity.id))

        friend_id_raw = request.form.get("friend_id", "").strip()
        try:
            friend_id = int(friend_id_raw)
        except ValueError:
            flash("Choose a friend to invite.", "error")
            return redirect(url_for("activity_detail", activity_id=activity.id))

        if friend_id not in accepted_friend_ids(current_user.id):
            flash("You can only invite people who are already your friends.", "error")
            return redirect(url_for("activity_detail", activity_id=activity.id))

        if find_participation(activity, friend_id) is not None:
            flash("That friend is already part of this event.", "info")
            return redirect(url_for("activity_detail", activity_id=activity.id))

        existing = Invite.query.filter_by(activity_id=activity.id, invitee_id=friend_id).first()
        if existing is not None:
            flash("You already invited that friend to this event.", "info")
            return redirect(url_for("activity_detail", activity_id=activity.id))

        db.session.add(
            Invite(
                activity_id=activity.id,
                inviter_id=current_user.id,
                invitee_id=friend_id,
                status="pending",
            )
        )
        db.session.commit()
        friend = db.session.get(User, friend_id)
        flash(f"Invite sent to {friend.name}.", "success")
        return redirect(url_for("activity_detail", activity_id=activity.id))

    @app.post("/invites/<int:invite_id>/accept")
    @login_required
    def accept_invite(invite_id: int):
        current_user = require_current_user()
        invite = Invite.query.filter_by(id=invite_id, invitee_id=current_user.id, status="pending").first_or_404()
        activity = get_activity_or_404(invite.activity_id)

        participation = find_participation(activity, current_user.id)
        if participation is None:
            participation = Participation(
                activity=activity,
                user=current_user,
                status="interested",
                reason=f"Invited by {invite.inviter.name}",
                eta_label="Not committed",
                eta_status="on_track",
            )
            db.session.add(participation)
        db.session.delete(invite)
        db.session.commit()
        flash(f"You accepted the invite to {activity.title}. Join when you're ready.", "success")
        return redirect(url_for("activity_detail", activity_id=activity.id))

    @app.post("/invites/<int:invite_id>/decline")
    @login_required
    def decline_invite(invite_id: int):
        current_user = require_current_user()
        invite = Invite.query.filter_by(id=invite_id, invitee_id=current_user.id, status="pending").first_or_404()
        db.session.delete(invite)
        db.session.commit()
        flash("Invite declined.", "info")
        return redirect(url_for("friends_page"))

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("forbidden.html"), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("not_found.html"), 404


def resolve_next_url(candidate: str | None) -> str | None:
    if candidate and candidate.startswith("/"):
        return candidate
    return None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if get_current_user(optional=True) is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def get_current_user(*, optional: bool = False) -> User | None:
    if getattr(g, "_current_user_loaded", False):
        user = getattr(g, "current_user", None)
        if user is None and not optional:
            abort(401)
        return user

    user_id = session.get("user_id")
    user = db.session.get(User, user_id) if user_id else None
    g._current_user_loaded = True
    g.current_user = user
    if user is None and not optional:
        abort(401)
    return user


def require_current_user() -> User:
    user = get_current_user(optional=True)
    if user is None:
        abort(401)
    return user


def validate_registration_form(form_data: dict, password: str) -> list[str]:
    errors: list[str] = []
    if not form_data["name"]:
        errors.append("Name is required.")
    if not form_data["email"] or "@" not in form_data["email"]:
        errors.append("Use a valid email address.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if not form_data["city"]:
        errors.append("City is required.")
    if not form_data["home_area"]:
        errors.append("Neighborhood or area is required.")
    if User.query.filter_by(name=form_data["name"]).first():
        errors.append("That display name is already taken.")
    if User.query.filter(db.func.lower(User.email) == form_data["email"]).first():
        errors.append("An account with that email already exists.")
    return errors


def empty_activity_form_defaults() -> dict:
    return {
        "title": "",
        "category": CATEGORIES[0],
        "date": date.today().isoformat(),
        "time": "18:00",
        "location": "",
        "description": "",
        "capacity": "6",
        "min_reliability": RELIABILITY_THRESHOLDS[1]["value"],
        "recurring_weekly": False,
        "venue_lat": DEFAULT_VENUE_COORDS["lat"],
        "venue_lng": DEFAULT_VENUE_COORDS["lng"],
        "roles": [
            {"name": "", "type": "Mandatory", "needed": "1"},
            {"name": "", "type": "Preferred", "needed": "1"},
        ],
    }


def activity_form_defaults(activity: Activity) -> dict:
    return {
        "title": activity.title,
        "category": activity.category,
        "date": activity.activity_date.isoformat(),
        "time": activity.start_time.isoformat(timespec="minutes"),
        "location": activity.location,
        "description": activity.description,
        "capacity": str(activity.capacity),
        "min_reliability": str(activity.min_reliability),
        "recurring_weekly": activity.recurring_weekly,
        "venue_lat": activity.venue_lat,
        "venue_lng": activity.venue_lng,
        "roles": [
            {"name": role.name, "type": role.role_type.capitalize(), "needed": str(role.needed_count)}
            for role in activity.roles
        ]
        or [
            {"name": "", "type": "Mandatory", "needed": "1"},
            {"name": "", "type": "Preferred", "needed": "1"},
        ],
    }


def validate_activity_form(form, *, default_coords: dict[str, float] | None = None) -> tuple[dict, list[str]]:
    errors: list[str] = []
    title = form.get("title", "").strip()
    category = form.get("category", "").strip()
    activity_date_raw = form.get("date", "").strip()
    start_time_raw = form.get("time", "").strip()
    location = form.get("location", "").strip()
    description = form.get("description", "").strip()
    capacity_raw = form.get("capacity", "").strip()
    min_reliability_raw = form.get("min_reliability", "").strip()
    recurring_weekly = form.get("recurring_weekly") == "on"
    venue_lat_raw = form.get("venue_lat", "").strip()
    venue_lng_raw = form.get("venue_lng", "").strip()
    coords = default_coords or DEFAULT_VENUE_COORDS

    cleaned = {
        "title": title,
        "category": category,
        "date": activity_date_raw,
        "time": start_time_raw,
        "location": location,
        "description": description,
        "capacity": capacity_raw,
        "min_reliability": min_reliability_raw,
        "recurring_weekly": recurring_weekly,
        "venue_lat": coords["lat"],
        "venue_lng": coords["lng"],
        "roles": [],
    }

    if not title:
        errors.append("Title is required.")
    if category not in CATEGORIES:
        errors.append("Choose a valid category.")

    try:
        cleaned["activity_date"] = date.fromisoformat(activity_date_raw)
    except ValueError:
        errors.append("Choose a valid activity date.")
    else:
        if cleaned["activity_date"] < date.today():
            errors.append("Activity date cannot be in the past.")

    try:
        cleaned["start_time"] = time.fromisoformat(start_time_raw)
    except ValueError:
        errors.append("Choose a valid start time.")

    if not location:
        errors.append("Location is required.")
    if not description:
        errors.append("Description is required.")

    try:
        capacity_value = int(capacity_raw)
        if capacity_value < 2:
            raise ValueError
        cleaned["capacity_value"] = capacity_value
    except ValueError:
        errors.append("Capacity must be at least 2.")

    threshold_values = {item["value"] for item in RELIABILITY_THRESHOLDS}
    if min_reliability_raw not in threshold_values:
        errors.append("Choose a valid reliability threshold.")
    else:
        cleaned["min_reliability_value"] = int(min_reliability_raw)

    if venue_lat_raw and venue_lng_raw:
        try:
            cleaned["venue_lat"] = float(venue_lat_raw)
            cleaned["venue_lng"] = float(venue_lng_raw)
        except ValueError:
            errors.append("Pinned map coordinates are invalid. Re-pick the location.")

    role_names = form.getlist("role_name")
    role_types = form.getlist("role_type")
    role_needed = form.getlist("role_needed")
    roles_clean = []
    roles_for_form = []
    for name_raw, role_type_raw, needed_raw in zip(role_names, role_types, role_needed):
        role_name = name_raw.strip()
        role_type = role_type_raw.strip().capitalize() or "Preferred"
        needed_value = needed_raw.strip() or "1"
        roles_for_form.append({"name": role_name, "type": role_type, "needed": needed_value})
        if not role_name:
            continue
        if role_type not in {"Mandatory", "Preferred", "Optional"}:
            errors.append(f"Role type for '{role_name}' is invalid.")
            continue
        try:
            needed_count = int(needed_value)
            if needed_count < 1:
                raise ValueError
        except ValueError:
            errors.append(f"Role count for '{role_name}' must be at least 1.")
            continue
        roles_clean.append({"name": role_name, "type": role_type, "needed": needed_count})

    cleaned["roles"] = roles_for_form or [
        {"name": "", "type": "Mandatory", "needed": "1"},
        {"name": "", "type": "Preferred", "needed": "1"},
    ]
    cleaned["roles_clean"] = roles_clean
    return cleaned, errors


def replace_activity_roles(activity: Activity, roles_clean: list[dict]) -> None:
    for participation in activity.participations:
        participation.assigned_role = None
    activity.roles.clear()
    db.session.flush()
    for role_data in roles_clean:
        db.session.add(
            Role(
                activity=activity,
                name=role_data["name"],
                role_type=role_data["type"].lower(),
                needed_count=role_data["needed"],
            )
        )


def profile_form_defaults(user: User) -> dict:
    return {
        "name": user.name,
        "email": user.email or "",
        "city": user.city,
        "home_area": user.home_area,
        "bio": user.bio,
        "avatar_url": user.avatar_url or "",
        "gps_tracking": user.gps_tracking,
        "interests": list(user.interests),
        "custom_interests": "",
    }


def extract_profile_form(form, current_user: User) -> dict:
    selected = [item.strip() for item in form.getlist("interests") if item.strip()]
    custom = [
        item.strip()
        for item in form.get("custom_interests", "").split(",")
        if item.strip()
    ]
    deduped: list[str] = []
    for item in selected + custom:
        if item not in deduped:
            deduped.append(item)

    return {
        "name": form.get("name", "").strip(),
        "email": form.get("email", "").strip().lower(),
        "city": form.get("city", "").strip(),
        "home_area": form.get("home_area", "").strip(),
        "bio": form.get("bio", "").strip(),
        "avatar_url": form.get("avatar_url", "").strip(),
        "gps_tracking": form.get("gps_tracking", "").strip() or current_user.gps_tracking,
        "interests": deduped,
        "custom_interests": form.get("custom_interests", "").strip(),
    }


def save_avatar_upload(uploaded_file, user: User) -> tuple[str | None, list[str]]:
    filename = secure_filename(uploaded_file.filename or "")
    if not filename or "." not in filename:
        return None, ["Upload a valid image file (png, jpg, gif, or webp)."]
    extension = filename.rsplit(".", 1)[-1].lower()
    if extension not in ALLOWED_AVATAR_EXTENSIONS:
        return None, ["Profile photo must be a png, jpg, gif, or webp image."]
    uploaded_file.stream.seek(0, os.SEEK_END)
    size = uploaded_file.stream.tell()
    uploaded_file.stream.seek(0)
    if size > MAX_AVATAR_BYTES:
        return None, ["Profile photo must be 4 MB or smaller."]
    if size == 0:
        return None, ["Uploaded file is empty."]
    unique_suffix = secrets.token_hex(6)
    stored_name = f"user_{user.id}_{unique_suffix}.{extension}"
    destination = AVATAR_UPLOAD_DIR / stored_name
    uploaded_file.save(destination)
    return url_for("static", filename=f"uploads/avatars/{stored_name}"), []


def validate_profile_form(current_user: User, form_data: dict) -> list[str]:
    errors: list[str] = []
    if not form_data["name"]:
        errors.append("Name is required.")
    if not form_data["email"] or "@" not in form_data["email"]:
        errors.append("Use a valid email address.")
    if not form_data["city"]:
        errors.append("City is required.")
    if not form_data["home_area"]:
        errors.append("Neighborhood or area is required.")
    if not form_data["bio"]:
        errors.append("Bio is required.")
    if not form_data["interests"]:
        errors.append("Choose at least one interest.")
    if form_data["gps_tracking"] not in TRACKING_OPTIONS:
        errors.append("Choose a valid tracking preference.")

    duplicate_name = User.query.filter(User.name == form_data["name"], User.id != current_user.id).first()
    duplicate_email = User.query.filter(db.func.lower(User.email) == form_data["email"], User.id != current_user.id).first()
    if duplicate_name:
        errors.append("That display name is already in use.")
    if duplicate_email:
        errors.append("That email is already in use.")
    return errors


def find_friendship(user_a_id: int, user_b_id: int) -> Friendship | None:
    return Friendship.query.filter(
        or_(
            db.and_(Friendship.requester_id == user_a_id, Friendship.addressee_id == user_b_id),
            db.and_(Friendship.requester_id == user_b_id, Friendship.addressee_id == user_a_id),
        )
    ).first()


def accepted_friend_ids(user_id: int) -> set[int]:
    rows = Friendship.query.filter(
        Friendship.status == "accepted",
        or_(Friendship.requester_id == user_id, Friendship.addressee_id == user_id),
    ).all()
    return {row.addressee_id if row.requester_id == user_id else row.requester_id for row in rows}


def get_friends(user: User) -> list[User]:
    friend_ids = accepted_friend_ids(user.id)
    if not friend_ids:
        return []
    return User.query.filter(User.id.in_(friend_ids)).order_by(User.name).all()


def pending_incoming_friend_requests(user_id: int) -> list[Friendship]:
    return (
        Friendship.query.filter_by(addressee_id=user_id, status="pending")
        .order_by(Friendship.created_at.desc())
        .all()
    )


def pending_outgoing_friend_requests(user_id: int) -> list[Friendship]:
    return (
        Friendship.query.filter_by(requester_id=user_id, status="pending")
        .order_by(Friendship.created_at.desc())
        .all()
    )


def pending_invites_for(user_id: int) -> list[Invite]:
    return (
        Invite.query.filter_by(invitee_id=user_id, status="pending")
        .order_by(Invite.created_at.desc())
        .all()
    )


def serialize_invite(invite: Invite) -> dict:
    activity = invite.activity
    return {
        "id": invite.id,
        "activity_id": activity.id,
        "activity_title": activity.title,
        "activity_date_label": display_event_date(activity.activity_date),
        "activity_time": display_time(activity.start_time),
        "activity_location": activity.location,
        "inviter_name": invite.inviter.name,
    }


def load_activities() -> list[Activity]:
    refresh_recurring_activities()
    return (
        Activity.query.options(
            joinedload(Activity.host),
            joinedload(Activity.roles),
            joinedload(Activity.participations).joinedload(Participation.user),
            joinedload(Activity.participations).joinedload(Participation.assigned_role),
            joinedload(Activity.messages).joinedload(Message.author),
        )
        .order_by(Activity.recurring_weekly.desc(), Activity.activity_date, Activity.start_time)
        .all()
    )


def refresh_recurring_activities() -> None:
    """Auto-advance any past-due recurring weekly activities to the next valid week.

    When a weekly activity's date has passed, bump it forward by 7 days until it
    lands on today or later, and reset per-week state (non-host participations,
    messages, ETAs) so the new week starts fresh.
    """
    today = date.today()
    stale = Activity.query.filter(
        Activity.recurring_weekly.is_(True),
        Activity.activity_date < today,
    ).all()
    if not stale:
        return

    for activity in stale:
        weeks_behind = (today - activity.activity_date).days // 7 + 1
        activity.activity_date = activity.activity_date + timedelta(days=weeks_behind * 7)

        # Clear per-week state: non-host participations, messages, and invites.
        for participation in list(activity.participations):
            if participation.user_id == activity.host_id:
                participation.eta_label = "At venue"
                participation.eta_status = "checked_in"
                participation.reason = "Host"
            else:
                db.session.delete(participation)
        for message in list(activity.messages):
            db.session.delete(message)
        Invite.query.filter_by(activity_id=activity.id).delete(synchronize_session=False)

    db.session.commit()


def get_activity_or_404(activity_id: int) -> Activity:
    refresh_recurring_activities()
    activity = (
        Activity.query.options(
            joinedload(Activity.host),
            joinedload(Activity.roles),
            joinedload(Activity.participations).joinedload(Participation.user),
            joinedload(Activity.participations).joinedload(Participation.assigned_role),
            joinedload(Activity.messages).joinedload(Message.author),
        )
        .filter_by(id=activity_id)
        .first()
    )
    if activity is None:
        abort(404)
    return activity


def require_host_access(activity: Activity, current_user: User) -> None:
    if activity.host_id != current_user.id:
        abort(403)


def find_participation(activity: Activity, user_id: int) -> Participation | None:
    return next((item for item in activity.participations if item.user_id == user_id), None)


def get_or_create_participation(activity: Activity, user: User) -> Participation:
    participation = find_participation(activity, user.id)
    if participation is None:
        participation = Participation(activity=activity, user=user, status="interested", reason="Created from action")
        db.session.add(participation)
        db.session.flush()
    return participation


def count_joined(activity: Activity, exclude_participation_id: int | None = None) -> int:
    return sum(
        1
        for participation in activity.participations
        if participation.status == "joined" and participation.id != exclude_participation_id
    )


def role_fill_counts(activity: Activity, exclude_participation_id: int | None = None) -> dict[int, int]:
    counts: dict[int, int] = defaultdict(int)
    for participation in activity.participations:
        if participation.id == exclude_participation_id or participation.status != "joined" or participation.assigned_role_id is None:
            continue
        counts[participation.assigned_role_id] += 1
    return counts


def protected_mandatory_seats(
    activity: Activity,
    *,
    exclude_participation_id: int | None = None,
    extra_role_id: int | None = None,
) -> int:
    counts = role_fill_counts(activity, exclude_participation_id=exclude_participation_id)
    if extra_role_id is not None:
        counts[extra_role_id] += 1

    protected = 0
    for role in activity.roles:
        if role.role_type != "mandatory":
            continue
        protected += max(role.needed_count - counts.get(role.id, 0), 0)
    return protected


def can_confirm_join(activity: Activity, participation: Participation, *, host_override: bool = False) -> bool:
    occupied = count_joined(activity, exclude_participation_id=participation.id)
    remaining_capacity = activity.capacity - occupied
    if remaining_capacity <= 0:
        return False
    if host_override:
        return True
    if participation.user.reliability_score < activity.min_reliability:
        return False
    protected = protected_mandatory_seats(
        activity,
        exclude_participation_id=participation.id,
        extra_role_id=participation.assigned_role_id,
    )
    return remaining_capacity > protected


def waitlist_reason(activity: Activity, participation: Participation) -> str:
    occupied = count_joined(activity, exclude_participation_id=participation.id)
    remaining_capacity = activity.capacity - occupied
    if participation.user.reliability_score < activity.min_reliability:
        return "Your reliability score is below this event's minimum."
    protected = protected_mandatory_seats(activity, exclude_participation_id=participation.id)
    if remaining_capacity <= 0:
        return "This event is already full."
    if protected >= remaining_capacity:
        return "Mandatory role seats are still protected by the host."
    return "This event still needs host review."


def apply_join_request(activity: Activity, participation: Participation) -> tuple[str, str]:
    if can_confirm_join(activity, participation):
        participation.status = "joined"
        participation.reason = "Confirmed"
        clear_attendance_outcome(participation)
        if participation.eta_label in {"Not committed", "Waitlisted"}:
            participation.eta_label = "ETA hidden"
            participation.eta_status = "on_track"
        return "joined", "Confirmed"

    participation.status = "waitlist"
    participation.reason = waitlist_reason(activity, participation)
    participation.assigned_role = None
    clear_attendance_outcome(participation)
    participation.eta_label = "Waitlisted"
    participation.eta_status = "delayed"
    return "waitlist", participation.reason


def promote_waitlist(activity: Activity) -> None:
    waitlisted = sorted(
        [item for item in activity.participations if item.status == "waitlist"],
        key=lambda item: (-item.user.reliability_score, item.created_at),
    )
    for participation in waitlisted:
        if can_confirm_join(activity, participation):
            participation.status = "joined"
            participation.reason = "Promoted from waitlist"
            clear_attendance_outcome(participation)
            if participation.eta_label == "Waitlisted":
                participation.eta_label = "ETA hidden"
                participation.eta_status = "on_track"


def clear_attendance_outcome(participation: Participation) -> None:
    participation.attendance_outcome = "pending"
    participation.attendance_recorded_at = None


def attendance_review_open(activity: Activity) -> bool:
    return datetime.now() >= event_start_datetime(activity)


def event_start_datetime(activity: Activity) -> datetime:
    return datetime.combine(activity.activity_date, activity.start_time)


def event_end_datetime(activity: Activity) -> datetime:
    return event_start_datetime(activity) + timedelta(minutes=DEFAULT_EVENT_DURATION_MINUTES)


def recalculate_reliability(user: User) -> None:
    scored = (
        Participation.query.join(Activity)
        .filter(
            Participation.user_id == user.id,
            Participation.status == "joined",
            Participation.attendance_outcome.in_(tuple(ATTENDANCE_SCORE_MAP)),
            Activity.host_id != user.id,
        )
        .order_by(Activity.activity_date.desc(), Activity.start_time.desc())
        .all()
    )
    if not scored:
        user.reliability_score = DEFAULT_RELIABILITY_SCORE
        return

    baseline_total = DEFAULT_RELIABILITY_SCORE * RELIABILITY_BASELINE_EVENTS
    total = baseline_total + sum(ATTENDANCE_SCORE_MAP[item.attendance_outcome] for item in scored)
    divisor = RELIABILITY_BASELINE_EVENTS + len(scored)
    user.reliability_score = max(20, min(100, round(total / divisor)))


def attendance_outcome_label(outcome: str) -> str:
    return {
        "pending": "Pending review",
        "on_time": "Arrived on time",
        "late": "Late",
        "no_show": "Did not attend",
    }.get(outcome, "Pending review")


def event_datetime_for_calendar(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S")


def build_google_calendar_url(activity: Activity) -> str:
    start = event_datetime_for_calendar(event_start_datetime(activity))
    end = event_datetime_for_calendar(event_end_datetime(activity))
    details = quote_plus(f"{activity.description}\n\nOpen the activity in EventMatch.")
    location = quote_plus(activity.location)
    text = quote_plus(activity.title)
    return (
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
        f"&text={text}&dates={start}/{end}&details={details}&location={location}"
    )


def escape_ics_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")


def build_ics_invite(activity: Activity) -> str:
    start = event_datetime_for_calendar(event_start_datetime(activity))
    end = event_datetime_for_calendar(event_end_datetime(activity))
    stamp = event_datetime_for_calendar(utcnow())
    uid = f"eventmatch-{activity.id}@local"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//EventMatch//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{start}",
        f"DTEND:{end}",
        f"SUMMARY:{escape_ics_text(activity.title)}",
        f"DESCRIPTION:{escape_ics_text(activity.description)}",
        f"LOCATION:{escape_ics_text(activity.location)}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


def serialize_user(user: User) -> dict:
    joined_count = sum(1 for item in user.participations if item.status == "joined")
    hosted_count = len(user.hosted_activities)
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email or "",
        "city": user.city,
        "bio": user.bio,
        "interests": user.interests,
        "gps_tracking": user.gps_tracking,
        "home_area": user.home_area,
        "avatar_url": user.avatar_url or "",
        "initials": initials_for_name(user.name),
        "stats": {
            "joined": joined_count,
            "hosted": hosted_count,
            "reliability": reliability_label(user.reliability_score),
        },
    }


def serialize_activity(activity: Activity, current_user: User) -> dict:
    role_counts = role_fill_counts(activity)
    joined = [item for item in activity.participations if item.status == "joined"]
    interested = [item for item in activity.participations if item.status == "interested"]
    waitlist = [item for item in activity.participations if item.status == "waitlist"]
    viewer_participation = find_participation(activity, current_user.id)
    review_open = attendance_review_open(activity)

    roles = [
        {
            "id": role.id,
            "name": role.name,
            "type": role.role_type,
            "filled": role_counts.get(role.id, 0),
            "needed": role.needed_count,
        }
        for role in activity.roles
    ]

    eta_summary = {"arriving_soon": 0, "on_track": 0, "delayed": 0}
    attendance_summary = {"on_time": 0, "late": 0, "no_show": 0}
    attendees = []
    for participation in sorted(joined, key=lambda item: (0 if item.user_id == activity.host_id else 1, item.user.name.lower())):
        if participation.eta_status in eta_summary:
            eta_summary[participation.eta_status] += 1
        if participation.attendance_outcome in attendance_summary:
            attendance_summary[participation.attendance_outcome] += 1
        attendees.append(
            {
                "id": participation.id,
                "name": participation.user.name,
                "role": resolve_display_role(activity, participation),
                "eta": participation.eta_label,
                "status": participation.eta_status,
                "reliability": reliability_label(participation.user.reliability_score),
                "assigned_role_id": participation.assigned_role_id or "",
                "attendance_outcome": participation.attendance_outcome,
                "attendance_label": attendance_outcome_label(participation.attendance_outcome),
                "can_review_attendance": review_open and participation.user_id != activity.host_id,
            }
        )

    return {
        "id": activity.id,
        "title": activity.title,
        "category": activity.category,
        "date": activity.activity_date.isoformat(),
        "date_label": display_event_date(activity.activity_date),
        "weekday": activity.activity_date.strftime("%a"),
        "time": display_time(activity.start_time),
        "location": activity.location,
        "map_url": openstreetmap_search_url(activity.location),
        "host": activity.host.name,
        "description": activity.description,
        "summary": truncate(activity.description, 140),
        "calendar_google_url": build_google_calendar_url(activity),
        "calendar_ics_url": url_for("download_calendar_invite", activity_id=activity.id),
        "capacity": activity.capacity,
        "joined": len(joined),
        "interested": len(interested),
        "waitlist_count": len(waitlist),
        "spots_left": max(activity.capacity - len(joined), 0),
        "venue_coords": {"lat": activity.venue_lat, "lng": activity.venue_lng},
        "tracking_mode": activity.tracking_mode,
        "attendance_review_open": review_open,
        "attendance_summary": attendance_summary,
        "reliability": {
            "minimum": reliability_label(activity.min_reliability),
            "note": reliability_note(activity.min_reliability),
        },
        "eta_summary": eta_summary,
        "status": activity_status(activity, roles),
        "roles": roles,
        "role_summary": role_summary(roles),
        "attendees": attendees,
        "reviewable_attendees": [item for item in attendees if item["can_review_attendance"]],
        "interested_users": [
            {
                "id": item.id,
                "name": item.user.name,
                "reliability": reliability_label(item.user.reliability_score),
                "assigned_role_id": item.assigned_role_id or "",
            }
            for item in sorted(interested, key=lambda item: item.user.name.lower())
        ],
        "waitlist": [
            {
                "id": item.id,
                "name": item.user.name,
                "reliability": reliability_label(item.user.reliability_score),
                "reason": item.reason or waitlist_reason(activity, item),
                "assigned_role_id": item.assigned_role_id or "",
            }
            for item in sorted(waitlist, key=lambda item: (-item.user.reliability_score, item.user.name.lower()))
        ],
        "messages": [
            {
                "author": message.author.name,
                "time": display_chat_time(message.created_at),
                "body": message.body,
            }
            for message in activity.messages
        ],
        "viewer_status": viewer_participation.status if viewer_participation else "none",
        "viewer_eta": viewer_participation.eta_label if viewer_participation else "Join to share ETA",
        "viewer_can_manage": activity.host_id == current_user.id,
        "viewer_is_host": activity.host_id == current_user.id,
        "viewer_can_invite": activity.host_id == current_user.id or viewer_participation is not None,
        "invitable_friends": _invitable_friends_for(activity, current_user),
        "is_weekly": activity.recurring_weekly,
        "recurrence_label": "Weekly circle" if activity.recurring_weekly else "One-time plan",
    }


def _invitable_friends_for(activity: Activity, current_user: User) -> list[dict]:
    friend_ids = accepted_friend_ids(current_user.id)
    if not friend_ids:
        return []
    already_in_activity = {participation.user_id for participation in activity.participations}
    already_invited = {
        invite.invitee_id
        for invite in Invite.query.filter_by(activity_id=activity.id, status="pending").all()
    }
    candidate_ids = friend_ids - already_in_activity - already_invited
    if not candidate_ids:
        return []
    friends = User.query.filter(User.id.in_(candidate_ids)).order_by(User.name).all()
    return [
        {
            "id": friend.id,
            "name": friend.name,
            "initials": initials_for_name(friend.name),
        }
        for friend in friends
    ]


def serialize_activity_summary(activity: Activity) -> dict:
    serialized_roles = [
        {
            "id": role.id,
            "name": role.name,
            "type": role.role_type,
            "filled": role_fill_counts(activity).get(role.id, 0),
            "needed": role.needed_count,
        }
        for role in activity.roles
    ]
    return {
        "id": activity.id,
        "title": activity.title,
        "date": activity.activity_date.isoformat(),
        "date_label": display_event_date(activity.activity_date),
        "time": display_time(activity.start_time),
        "category": activity.category,
        "location": activity.location,
        "map_url": openstreetmap_search_url(activity.location),
        "status": activity_status(activity, serialized_roles),
        "joined": count_joined(activity),
        "capacity": activity.capacity,
        "is_weekly": activity.recurring_weekly,
    }


def activity_status(activity: Activity, serialized_roles: list[dict]) -> str:
    mandatory_remaining = sum(
        max(role["needed"] - role["filled"], 0)
        for role in serialized_roles
        if role["type"] == "mandatory"
    )
    preferred_remaining = sum(
        max(role["needed"] - role["filled"], 0)
        for role in serialized_roles
        if role["type"] == "preferred"
    )
    if mandatory_remaining:
        return f"{mandatory_remaining} protected seat{'s' if mandatory_remaining != 1 else ''}"
    if preferred_remaining:
        return f"{preferred_remaining} open role{'s' if preferred_remaining != 1 else ''}"
    if count_joined(activity) >= activity.capacity:
        return "Full"
    return "Open"


def role_summary(roles: list[dict]) -> str:
    if not roles:
        return "No roles assigned"
    important = [f"{role['name']} {role['filled']}/{role['needed']}" for role in roles[:2]]
    return " • ".join(important)


def resolve_display_role(activity: Activity, participation: Participation) -> str:
    if participation.user_id == activity.host_id:
        return "Host"
    if participation.assigned_role is not None:
        return participation.assigned_role.name
    return "Guest"


def reliability_label(score: int) -> str:
    return f"{score}%"


def reliability_note(min_reliability: int) -> str:
    if min_reliability >= 80:
        return "This event expects reliable attendance and on-time arrivals."
    if min_reliability >= 65:
        return "Balanced commitment: useful for small groups that still need accountability."
    return "Open group. People can signal interest before fully committing."


def display_time(value: time) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def openstreetmap_search_url(query: str) -> str:
    return f"https://www.openstreetmap.org/search?query={quote_plus(query)}"


def openstreetmap_embed_url(lat: float, lng: float, *, padding: float = 0.01) -> str:
    west = lng - padding
    south = lat - padding
    east = lng + padding
    north = lat + padding
    return (
        "https://www.openstreetmap.org/export/embed.html"
        f"?bbox={west}%2C{south}%2C{east}%2C{north}&layer=mapnik&marker={lat}%2C{lng}"
    )


def display_chat_time(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def display_event_date(value: date) -> str:
    return value.strftime("%b %d, %Y")


def initials_for_name(name: str) -> str:
    parts = [part for part in name.split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][0].upper()
    return f"{parts[0][0]}{parts[-1][0]}".upper()


def truncate(text_value: str, limit: int) -> str:
    if len(text_value) <= limit:
        return text_value
    return f"{text_value[: limit - 1].rstrip()}…"


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    host = os.getenv("HOST", "0.0.0.0")
    debug = os.getenv("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug)
