from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time
from functools import wraps
import os
from pathlib import Path
from urllib.parse import quote_plus

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, inspect, text
from sqlalchemy.orm import joinedload
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "instance" / "eventmatch.db"
DEFAULT_TRACKING_MODE = "GPS ETA is shared only for confirmed attendees."
DEFAULT_BIO = "Add a short intro so people know what kind of plans you actually enjoy."
DEFAULT_TRACKING_OPTION = "Visible only after I join"
DEFAULT_RELIABILITY_SCORE = 80
DEFAULT_VENUE_COORDS = {"lat": -34.6037, "lng": -58.3816}
VALID_ETA_STATES = {"checked_in", "arriving_soon", "on_track", "delayed"}
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


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    DEFAULT_DB_PATH.parent.mkdir(exist_ok=True)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-key"),
        SQLALCHEMY_DATABASE_URI=get_database_uri(),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        GOOGLE_MAPS_API_KEY=os.getenv("GOOGLE_MAPS_API_KEY", "").strip(),
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


def register_routes(app: Flask) -> None:
    @app.context_processor
    def inject_template_state():
        current_user = get_current_user(optional=True)
        return {
            "viewer": serialize_user(current_user) if current_user else None,
            "interest_options": INTEREST_OPTIONS,
            "google_maps_api_key": app.config.get("GOOGLE_MAPS_API_KEY", ""),
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
                    eta_label="At venue",
                    eta_status="checked_in",
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
            if participation.user_id == current_user.id:
                flash("The host seat cannot be removed.", "error")
                return redirect(url_for("host_dashboard", activity_id=activity.id))
            db.session.delete(participation)
            db.session.flush()
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
                flash("No seats left. The participant stays on the waitlist.", "warning")
            else:
                participation.status = "joined"
                participation.reason = "Confirmed by host"
                if participation.eta_label == "Not committed":
                    participation.eta_label = "ETA hidden"
                flash("Participant confirmed.", "success")
        elif new_status == "interested":
            participation.status = "interested"
            participation.reason = "Moved to interested by host"
            participation.eta_label = "Not committed"
            participation.eta_status = "on_track"
            flash("Participant moved to interested.", "success")
        else:
            participation.status = "waitlist"
            participation.reason = "Pending host review"
            participation.eta_label = "Waitlisted"
            participation.eta_status = "delayed"
            flash("Participant moved to the waitlist.", "success")

        promote_waitlist(activity)
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
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template("edit_profile.html", form_data=form_data, tracking_options=TRACKING_OPTIONS), 400

            current_user.name = form_data["name"]
            current_user.email = form_data["email"]
            current_user.city = form_data["city"]
            current_user.home_area = form_data["home_area"]
            current_user.bio = form_data["bio"]
            current_user.avatar_url = form_data["avatar_url"]
            current_user.gps_tracking = form_data["gps_tracking"]
            current_user.interests_csv = "|".join(form_data["interests"])
            db.session.commit()
            flash("Profile updated.", "success")
            return redirect(url_for("profile"))

        return render_template("edit_profile.html", form_data=form_data, tracking_options=TRACKING_OPTIONS)

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


def load_activities() -> list[Activity]:
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


def get_activity_or_404(activity_id: int) -> Activity:
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
        if participation.eta_label in {"Not committed", "Waitlisted"}:
            participation.eta_label = "ETA hidden"
            participation.eta_status = "on_track"
        return "joined", "Confirmed"

    participation.status = "waitlist"
    participation.reason = waitlist_reason(activity, participation)
    participation.assigned_role = None
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
            if participation.eta_label == "Waitlisted":
                participation.eta_label = "ETA hidden"
                participation.eta_status = "on_track"


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
    attendees = []
    for participation in sorted(joined, key=lambda item: (0 if item.user_id == activity.host_id else 1, item.user.name.lower())):
        if participation.eta_status in eta_summary:
            eta_summary[participation.eta_status] += 1
        attendees.append(
            {
                "id": participation.id,
                "name": participation.user.name,
                "role": resolve_display_role(activity, participation),
                "eta": participation.eta_label,
                "status": participation.eta_status,
                "reliability": reliability_label(participation.user.reliability_score),
                "assigned_role_id": participation.assigned_role_id or "",
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
        "maps_query_url": google_maps_search_url(activity.location),
        "maps_directions_url": google_maps_directions_url(activity.venue_lat, activity.venue_lng),
        "host": activity.host.name,
        "description": activity.description,
        "summary": truncate(activity.description, 140),
        "capacity": activity.capacity,
        "joined": len(joined),
        "interested": len(interested),
        "waitlist_count": len(waitlist),
        "spots_left": max(activity.capacity - len(joined), 0),
        "venue_coords": {"lat": activity.venue_lat, "lng": activity.venue_lng},
        "tracking_mode": activity.tracking_mode,
        "reliability": {
            "minimum": reliability_label(activity.min_reliability),
            "note": reliability_note(activity.min_reliability),
        },
        "eta_summary": eta_summary,
        "status": activity_status(activity, roles),
        "roles": roles,
        "role_summary": role_summary(roles),
        "attendees": attendees,
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
        "is_weekly": activity.recurring_weekly,
        "recurrence_label": "Weekly circle" if activity.recurring_weekly else "One-time plan",
    }


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
        "maps_query_url": google_maps_search_url(activity.location),
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


def google_maps_search_url(query: str) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}"


def google_maps_directions_url(lat: float, lng: float) -> str:
    return f"https://www.google.com/maps/dir/?api=1&destination={lat},{lng}"


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
