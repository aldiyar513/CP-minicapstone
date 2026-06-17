from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
import os
from pathlib import Path

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import joinedload


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "instance" / "eventmatch.db"
DEFAULT_TRACKING_MODE = "GPS ETA auto-detected while attendees are en route"
CURRENT_USER_NAME = "Ari Foster"
CATEGORIES = ["Movie Night", "Gym Session", "Study Session", "Weekend Plan"]
RELIABILITY_THRESHOLDS = [
    {
        "value": "50",
        "label": "50%+",
        "description": "Very open, best for casual plans.",
    },
    {
        "value": "65",
        "label": "65%+",
        "description": "Balanced default for most activities.",
    },
    {
        "value": "80",
        "label": "80%+",
        "description": "Highest allowed threshold for time-sensitive plans.",
    },
]
DEFAULT_VENUE_COORDS = {"lat": -34.6037, "lng": -58.3816}
VALID_ETA_STATES = {"checked_in", "arriving_soon", "on_track", "delayed"}
STATUS_PRIORITY = {"joined": 0, "interested": 1, "waitlist": 2}

db = SQLAlchemy()


def get_database_uri() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
        elif database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return database_url
    return f"sqlite:///{DEFAULT_DB_PATH}"


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    city = db.Column(db.String(80), nullable=False)
    bio = db.Column(db.Text, nullable=False)
    interests_csv = db.Column(db.Text, default="", nullable=False)
    gps_tracking = db.Column(db.String(120), nullable=False)
    home_area = db.Column(db.String(80), nullable=False)
    reliability_score = db.Column(db.Integer, nullable=False)

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
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)

    with app.app_context():
        db.create_all()
        seed_database()

    register_routes(app)
    return app


def register_routes(app: Flask) -> None:
    @app.get("/healthz")
    def healthcheck():
        return {"ok": True}, 200

    @app.route("/")
    def feed():
        current_user = get_current_user()
        selected_category = request.args.get("category", "All")
        selected_date = request.args.get("date", "")

        activities = load_activities()
        if selected_category != "All":
            activities = [activity for activity in activities if activity.category == selected_category]
        if selected_date:
            activities = [activity for activity in activities if activity.activity_date.isoformat() == selected_date]

        categories = ["All"] + sorted({activity.category for activity in Activity.query.all()})
        return render_template(
            "feed.html",
            activities=[serialize_activity(activity, current_user) for activity in activities],
            categories=categories,
            current_user=serialize_user(current_user),
            selected_category=selected_category,
            selected_date=selected_date,
            today=date.today().isoformat(),
        )

    @app.route("/activities/new", methods=["GET", "POST"])
    def create_activity():
        current_user = get_current_user()
        default_roles = [
            {"name": "", "type": "Mandatory", "needed": "1"},
            {"name": "", "type": "Preferred", "needed": "1"},
        ]
        form_data = {
            "title": "",
            "category": CATEGORIES[0],
            "date": date.today().isoformat(),
            "time": "18:00",
            "location": "",
            "description": "",
            "capacity": "6",
            "min_reliability": RELIABILITY_THRESHOLDS[1]["value"],
            "roles": default_roles,
        }

        if request.method == "POST":
            cleaned, errors = validate_activity_form(request.form)
            form_data = cleaned
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template(
                    "create_activity.html",
                    categories=CATEGORIES,
                    current_user=serialize_user(current_user),
                    reliability_thresholds=RELIABILITY_THRESHOLDS,
                    form_data=form_data,
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
                tracking_mode=DEFAULT_TRACKING_MODE,
                venue_lat=DEFAULT_VENUE_COORDS["lat"],
                venue_lng=DEFAULT_VENUE_COORDS["lng"],
                host=current_user,
            )
            db.session.add(activity)
            db.session.flush()

            for role_data in cleaned["roles_clean"]:
                db.session.add(
                    Role(
                        activity=activity,
                        name=role_data["name"],
                        role_type=role_data["type"].lower(),
                        needed_count=role_data["needed"],
                    )
                )

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
            flash("Activity created. You can manage attendees from the host dashboard.", "success")
            return redirect(url_for("host_dashboard", activity_id=activity.id))

        return render_template(
            "create_activity.html",
            categories=CATEGORIES,
            current_user=serialize_user(current_user),
            reliability_thresholds=RELIABILITY_THRESHOLDS,
            form_data=form_data,
        )

    @app.route("/activities/<int:activity_id>")
    def activity_detail(activity_id: int):
        current_user = get_current_user()
        activity = get_activity_or_404(activity_id)
        return render_template(
            "activity_detail.html",
            activity=serialize_activity(activity, current_user),
            current_user=serialize_user(current_user),
        )

    @app.post("/activities/<int:activity_id>/join")
    def join_activity(activity_id: int):
        current_user = get_current_user()
        activity = get_activity_or_404(activity_id)
        participation = get_or_create_participation(activity, current_user)
        status, reason = apply_join_request(activity, participation)
        promote_waitlist(activity)
        db.session.commit()
        if status == "joined":
            flash("You are confirmed for this activity.", "success")
        else:
            flash(reason, "warning")
        return redirect(url_for("activity_detail", activity_id=activity.id))

    @app.post("/activities/<int:activity_id>/interest")
    def mark_interest(activity_id: int):
        current_user = get_current_user()
        activity = get_activity_or_404(activity_id)
        participation = get_or_create_participation(activity, current_user)
        if participation.status == "joined":
            flash("You already have a confirmed seat.", "info")
        else:
            participation.status = "interested"
            participation.reason = "Following this plan"
            participation.assigned_role = None
            participation.eta_label = "Not committed"
            participation.eta_status = "on_track"
            db.session.commit()
            flash("You are following this activity without taking a seat.", "success")
        return redirect(url_for("activity_detail", activity_id=activity.id))

    @app.post("/activities/<int:activity_id>/leave")
    def leave_activity(activity_id: int):
        current_user = get_current_user()
        activity = get_activity_or_404(activity_id)
        participation = find_participation(activity, current_user.id)
        if participation is None:
            flash("You are not part of this activity yet.", "info")
            return redirect(url_for("activity_detail", activity_id=activity.id))
        if activity.host_id == current_user.id:
            flash("Hosts cannot leave their own activity.", "error")
            return redirect(url_for("activity_detail", activity_id=activity.id))

        db.session.delete(participation)
        db.session.flush()
        promote_waitlist(activity)
        db.session.commit()
        flash("You have left this activity.", "success")
        return redirect(url_for("activity_detail", activity_id=activity.id))

    @app.post("/activities/<int:activity_id>/messages")
    def post_message(activity_id: int):
        current_user = get_current_user()
        activity = get_activity_or_404(activity_id)
        body = request.form.get("body", "").strip()
        if not body:
            flash("Message cannot be empty.", "error")
            return redirect(url_for("activity_detail", activity_id=activity.id))

        participation = find_participation(activity, current_user.id)
        if participation is None and activity.host_id != current_user.id:
            flash("Join or mark interest before posting in the activity chat.", "error")
            return redirect(url_for("activity_detail", activity_id=activity.id))

        db.session.add(Message(activity=activity, author=current_user, body=body))
        db.session.commit()
        flash("Message sent.", "success")
        return redirect(url_for("activity_detail", activity_id=activity.id))

    @app.post("/activities/<int:activity_id>/eta")
    def update_eta(activity_id: int):
        current_user = get_current_user()
        activity = get_activity_or_404(activity_id)
        participation = find_participation(activity, current_user.id)
        if participation is None or participation.status != "joined":
            return jsonify({"error": "Join the activity before sharing ETA."}), 400

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
    def host_dashboard(activity_id: int):
        current_user = get_current_user()
        activity = get_activity_or_404(activity_id)
        require_host_access(activity, current_user)
        return render_template(
            "host_dashboard.html",
            activity=serialize_activity(activity, current_user),
            current_user=serialize_user(current_user),
        )

    @app.post("/activities/<int:activity_id>/host/participants/<int:participation_id>")
    def update_participant(activity_id: int, participation_id: int):
        current_user = get_current_user()
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
            flash("Participant removed from the activity.", "success")
            return redirect(url_for("host_dashboard", activity_id=activity.id))

        if new_status not in {"joined", "interested", "waitlist"}:
            flash("Invalid participant update.", "error")
            return redirect(url_for("host_dashboard", activity_id=activity.id))

        if assigned_role_id:
            role = Role.query.filter_by(id=int(assigned_role_id), activity_id=activity.id).first()
            if role is None:
                flash("Selected role does not belong to this activity.", "error")
                return redirect(url_for("host_dashboard", activity_id=activity.id))
            participation.assigned_role = role
        else:
            participation.assigned_role = None

        if new_status == "joined":
            if not can_confirm_join(activity, participation, host_override=True):
                participation.status = "waitlist"
                participation.reason = "Activity is at capacity"
                flash("No capacity left. The participant stays on the waitlist.", "warning")
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
            participation.reason = "Pending host approval"
            participation.eta_label = "Waitlisted"
            participation.eta_status = "delayed"
            flash("Participant moved to the waitlist.", "success")

        promote_waitlist(activity)
        db.session.commit()
        return redirect(url_for("host_dashboard", activity_id=activity.id))

    @app.route("/profile")
    def profile():
        current_user = get_current_user()
        upcoming = (
            Activity.query.join(Participation)
            .filter(
                Participation.user_id == current_user.id,
                Participation.status == "joined",
                Activity.host_id != current_user.id,
                Activity.activity_date >= date.today(),
            )
            .options(joinedload(Activity.host), joinedload(Activity.roles), joinedload(Activity.participations))
            .order_by(Activity.activity_date, Activity.start_time)
            .all()
        )
        hosted = (
            Activity.query.filter_by(host_id=current_user.id)
            .options(joinedload(Activity.roles), joinedload(Activity.participations))
            .order_by(Activity.activity_date, Activity.start_time)
            .all()
        )
        return render_template(
            "profile.html",
            current_user=serialize_user(current_user),
            upcoming=[serialize_activity_summary(activity) for activity in upcoming],
            hosted=[serialize_activity_summary(activity) for activity in hosted],
        )

    @app.errorhandler(403)
    def forbidden(_error):
        current_user = get_current_user()
        return render_template("forbidden.html", current_user=serialize_user(current_user)), 403


def validate_activity_form(form) -> tuple[dict, list[str]]:
    errors: list[str] = []
    title = form.get("title", "").strip()
    category = form.get("category", "").strip()
    activity_date_raw = form.get("date", "").strip()
    start_time_raw = form.get("time", "").strip()
    location = form.get("location", "").strip()
    description = form.get("description", "").strip()
    capacity_raw = form.get("capacity", "").strip()
    min_reliability_raw = form.get("min_reliability", "").strip()

    cleaned = {
        "title": title,
        "category": category,
        "date": activity_date_raw,
        "time": start_time_raw,
        "location": location,
        "description": description,
        "capacity": capacity_raw,
        "min_reliability": min_reliability_raw,
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


def get_current_user() -> User:
    user = User.query.filter_by(name=CURRENT_USER_NAME).first()
    if user is None:
        raise RuntimeError("Current user seed is missing.")
    return user


def load_activities() -> list[Activity]:
    return (
        Activity.query.options(
            joinedload(Activity.host),
            joinedload(Activity.roles),
            joinedload(Activity.participations).joinedload(Participation.user),
            joinedload(Activity.participations).joinedload(Participation.assigned_role),
            joinedload(Activity.messages).joinedload(Message.author),
        )
        .order_by(Activity.activity_date, Activity.start_time)
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
        return "Your reliability score is below this activity's minimum."
    protected = protected_mandatory_seats(activity, exclude_participation_id=participation.id)
    if remaining_capacity <= 0:
        return "The activity is currently full."
    if protected >= remaining_capacity:
        return "Mandatory role seats are still protected by the host."
    return "This activity requires host review before confirmation."


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
        "city": user.city,
        "bio": user.bio,
        "interests": user.interests,
        "gps_tracking": user.gps_tracking,
        "home_area": user.home_area,
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

    roles = []
    for role in activity.roles:
        roles.append(
            {
                "id": role.id,
                "name": role.name,
                "type": role.role_type,
                "filled": role_counts.get(role.id, 0),
                "needed": role.needed_count,
            }
        )

    attendees = []
    eta_summary = {"arriving_soon": 0, "on_track": 0, "delayed": 0}
    for participation in sorted(
        joined,
        key=lambda item: (
            0 if item.user_id == activity.host_id else 1,
            item.user.name.lower(),
        ),
    ):
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
                "current_status": participation.status,
                "assigned_role_id": participation.assigned_role_id or "",
            }
        )

    interested_users = [
        {
            "id": item.id,
            "name": item.user.name,
            "reliability": reliability_label(item.user.reliability_score),
            "assigned_role_id": item.assigned_role_id or "",
        }
        for item in sorted(interested, key=lambda item: item.user.name.lower())
    ]
    waitlist_users = [
        {
            "id": item.id,
            "name": item.user.name,
            "reliability": reliability_label(item.user.reliability_score),
            "reason": item.reason or waitlist_reason(activity, item),
            "assigned_role_id": item.assigned_role_id or "",
        }
        for item in sorted(waitlist, key=lambda item: (-item.user.reliability_score, item.user.name.lower()))
    ]

    return {
        "id": activity.id,
        "title": activity.title,
        "category": activity.category,
        "date": activity.activity_date.isoformat(),
        "time": display_time(activity.start_time),
        "location": activity.location,
        "host": activity.host.name,
        "description": activity.description,
        "capacity": activity.capacity,
        "joined": len(joined),
        "interested": len(interested),
        "waitlist_count": len(waitlist),
        "venue_coords": {"lat": activity.venue_lat, "lng": activity.venue_lng},
        "tracking_mode": activity.tracking_mode,
        "reliability": {
            "minimum": reliability_label(activity.min_reliability),
            "note": reliability_note(activity.min_reliability),
        },
        "eta_summary": eta_summary,
        "status": activity_status(activity, roles),
        "roles": roles,
        "attendees": attendees,
        "interested_users": interested_users,
        "waitlist": waitlist_users,
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
        "viewer_assigned_role_id": viewer_participation.assigned_role_id if viewer_participation else "",
    }


def serialize_activity_summary(activity: Activity) -> dict:
    return {
        "id": activity.id,
        "title": activity.title,
        "date": activity.activity_date.isoformat(),
        "time": display_time(activity.start_time),
        "category": activity.category,
        "status": activity_status(
            activity,
            [
                {
                    "id": role.id,
                    "name": role.name,
                    "type": role.role_type,
                    "filled": role_fill_counts(activity).get(role.id, 0),
                    "needed": role.needed_count,
                }
                for role in activity.roles
            ],
        ),
        "joined": count_joined(activity),
        "capacity": activity.capacity,
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
        label = "seat" if mandatory_remaining == 1 else "seats"
        return f"{mandatory_remaining} mandatory {label} still protected"
    if preferred_remaining:
        label = "role" if preferred_remaining == 1 else "roles"
        return f"{preferred_remaining} preferred {label} still open"
    if count_joined(activity) >= activity.capacity:
        return "Activity is full"
    return "Mandatory coverage complete"


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
        return "This session is strict because it starts on time and depends on reliable attendance."
    if min_reliability >= 65:
        return "The host wants a dependable group, but there is still some flexibility."
    return "This is an open plan, but the host still prefers people who usually show up."


def display_time(value: time) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def display_chat_time(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def seed_database() -> None:
    if User.query.first() is not None:
        return

    user_specs = {
        "Ari Foster": {
            "city": "Buenos Aires",
            "bio": "I like structured study sessions, pickup sports, and plans that actually start on time.",
            "interests": ["Study Session", "Gym Session", "Weekend Plan"],
            "gps_tracking": "Enabled for joined activities only",
            "home_area": "Palermo",
            "reliability_score": 96,
        },
        "Maya": {"reliability_score": 98},
        "Lucas": {"reliability_score": 94},
        "Sara": {"reliability_score": 79},
        "Nico": {"reliability_score": 91},
        "Ana": {"reliability_score": 88},
        "Paula": {"reliability_score": 83},
        "Iker": {"reliability_score": 76},
        "Caro": {"reliability_score": 92},
        "Jules": {"reliability_score": 81},
        "Jin": {"reliability_score": 97},
        "Leila": {"reliability_score": 96},
        "Omar": {"reliability_score": 98},
        "Tina": {"reliability_score": 95},
        "Milo": {"reliability_score": 84},
        "Nadia": {"reliability_score": 90},
        "Sofia": {"reliability_score": 78},
        "Rayan": {"reliability_score": 87},
        "Camila": {"reliability_score": 93},
        "Mateo": {"reliability_score": 89},
        "Priya": {"reliability_score": 86},
        "Lena": {"reliability_score": 72},
        "Bruno": {"reliability_score": 67},
        "Ava": {"reliability_score": 91},
        "Noah": {"reliability_score": 88},
        "Elena": {"reliability_score": 85},
        "Santi": {"reliability_score": 82},
        "Tomas": {"reliability_score": 77},
    }

    users: dict[str, User] = {}
    for name, spec in user_specs.items():
        interests = spec.get("interests", [])
        user = User(
            name=name,
            city=spec.get("city", "Buenos Aires"),
            bio=spec.get("bio", f"{name} likes campus activities that start when everyone is actually ready."),
            interests_csv="|".join(interests),
            gps_tracking=spec.get("gps_tracking", "Enabled while joined activities are active"),
            home_area=spec.get("home_area", "Palermo"),
            reliability_score=spec["reliability_score"],
        )
        db.session.add(user)
        users[name] = user

    db.session.flush()

    today = date.today()
    activity_specs = [
        {
            "title": "Friday Movie Night",
            "category": "Movie Night",
            "day_offset": 1,
            "time": "20:00",
            "location": "Recoleta Student Residence",
            "host": "Maya",
            "description": "Watching a feel-good movie, bringing snacks, and keeping it low-key.",
            "capacity": 8,
            "min_reliability": 50,
            "coords": {"lat": -34.5895, "lng": -58.3974},
            "roles": [
                {"name": "Snack Lead", "type": "mandatory", "needed": 1},
                {"name": "Projector Setup", "type": "mandatory", "needed": 1},
                {"name": "Playlist Helper", "type": "preferred", "needed": 2},
            ],
            "joined": [
                {"name": "Maya", "role": None, "eta": "At venue", "eta_status": "checked_in", "reason": "Host"},
                {"name": "Lucas", "role": "Snack Lead", "eta": "6 min away", "eta_status": "arriving_soon"},
                {"name": "Sara", "role": None, "eta": "14 min away", "eta_status": "delayed"},
                {"name": "Nico", "role": "Playlist Helper", "eta": "9 min away", "eta_status": "on_track"},
                {"name": "Ana", "role": None, "eta": "4 min away", "eta_status": "arriving_soon"},
            ],
            "interested": [{"name": "Paula"}, {"name": "Iker"}, {"name": "Caro"}],
            "waitlist": [{"name": "Jules", "reason": "Mandatory role seats are still protected by the host."}],
            "messages": [
                {"author": "Maya", "body": "Bring a blanket if you want, the living room gets cold.", "minutes_ago": 75},
                {"author": "Lucas", "body": "I can handle chips and drinks.", "minutes_ago": 72},
                {"author": "Sara", "body": "Running a bit late but still coming.", "minutes_ago": 68},
            ],
        },
        {
            "title": "Campus Study Sprint",
            "category": "Study Session",
            "day_offset": 2,
            "time": "14:00",
            "location": "Engineering Library",
            "host": "Jin",
            "description": "Two-hour focused study block for calculus and physics.",
            "capacity": 6,
            "min_reliability": 80,
            "coords": {"lat": -34.6032, "lng": -58.3817},
            "roles": [
                {"name": "Whiteboard Spotter", "type": "mandatory", "needed": 1},
                {"name": "Problem Set Lead", "type": "preferred", "needed": 1},
            ],
            "joined": [
                {"name": "Jin", "role": None, "eta": "At venue", "eta_status": "checked_in", "reason": "Host"},
                {"name": "Ari Foster", "role": None, "eta": "ETA hidden", "eta_status": "on_track"},
                {"name": "Leila", "role": "Whiteboard Spotter", "eta": "3 min away", "eta_status": "arriving_soon"},
                {"name": "Omar", "role": "Problem Set Lead", "eta": "8 min away", "eta_status": "on_track"},
            ],
            "interested": [{"name": "Milo"}, {"name": "Nadia"}],
            "waitlist": [
                {"name": "Sofia", "reason": "Your reliability score is below this activity's minimum."},
                {"name": "Rayan", "reason": "Mandatory role seats are still protected by the host."},
            ],
            "messages": [
                {"author": "Jin", "body": "Bring your own notes. I reserved a table for six.", "minutes_ago": 95},
                {"author": "Omar", "body": "I will share the toughest practice problems first.", "minutes_ago": 84},
            ],
        },
        {
            "title": "Saturday Gym Crew",
            "category": "Gym Session",
            "day_offset": 3,
            "time": "10:00",
            "location": "Club Atletico Center",
            "host": "Camila",
            "description": "Leg day, cardio finisher, then smoothies nearby.",
            "capacity": 5,
            "min_reliability": 65,
            "coords": {"lat": -34.6158, "lng": -58.4333},
            "roles": [
                {"name": "Warmup Lead", "type": "mandatory", "needed": 1},
                {"name": "Form Check Buddy", "type": "optional", "needed": 2},
            ],
            "joined": [
                {"name": "Camila", "role": None, "eta": "At venue", "eta_status": "checked_in", "reason": "Host"},
                {"name": "Mateo", "role": "Warmup Lead", "eta": "7 min away", "eta_status": "on_track"},
                {"name": "Ari Foster", "role": None, "eta": "ETA hidden", "eta_status": "on_track"},
            ],
            "interested": [{"name": "Lena"}, {"name": "Bruno"}, {"name": "Ava"}],
            "waitlist": [],
            "messages": [
                {"author": "Camila", "body": "Meet near the front desk before we head in.", "minutes_ago": 50},
            ],
        },
        {
            "title": "Sunday Brunch Planning",
            "category": "Weekend Plan",
            "day_offset": 4,
            "time": "11:30",
            "location": "Palermo Coffee Lab",
            "host": "Ari Foster",
            "description": "Planning a relaxed brunch route and splitting who books the table and who checks nearby spots.",
            "capacity": 6,
            "min_reliability": 65,
            "coords": {"lat": -34.5875, "lng": -58.4262},
            "roles": [
                {"name": "Table Booker", "type": "mandatory", "needed": 1},
                {"name": "Backup Cafe Scout", "type": "preferred", "needed": 1},
            ],
            "joined": [
                {"name": "Ari Foster", "role": None, "eta": "At venue", "eta_status": "checked_in", "reason": "Host"},
                {"name": "Noah", "role": "Table Booker", "eta": "5 min away", "eta_status": "arriving_soon"},
            ],
            "interested": [{"name": "Elena"}],
            "waitlist": [
                {"name": "Santi", "reason": "Mandatory role seats are still protected by the host."},
                {"name": "Tomas", "reason": "Mandatory role seats are still protected by the host."},
            ],
            "messages": [
                {"author": "Ari Foster", "body": "If we confirm four people, I will book one of the bigger tables.", "minutes_ago": 42},
            ],
        },
    ]

    for spec in activity_specs:
        activity = Activity(
            title=spec["title"],
            category=spec["category"],
            activity_date=today + timedelta(days=spec["day_offset"]),
            start_time=time.fromisoformat(spec["time"]),
            location=spec["location"],
            description=spec["description"],
            capacity=spec["capacity"],
            min_reliability=spec["min_reliability"],
            tracking_mode=DEFAULT_TRACKING_MODE,
            venue_lat=spec["coords"]["lat"],
            venue_lng=spec["coords"]["lng"],
            host=users[spec["host"]],
        )
        db.session.add(activity)
        db.session.flush()

        role_map: dict[str, Role] = {}
        for role_spec in spec["roles"]:
            role = Role(
                activity=activity,
                name=role_spec["name"],
                role_type=role_spec["type"],
                needed_count=role_spec["needed"],
            )
            db.session.add(role)
            db.session.flush()
            role_map[role.name] = role

        for joined_spec in spec["joined"]:
            db.session.add(
                Participation(
                    activity=activity,
                    user=users[joined_spec["name"]],
                    status="joined",
                    reason=joined_spec.get("reason", "Confirmed"),
                    assigned_role=role_map.get(joined_spec["role"]) if joined_spec["role"] else None,
                    eta_label=joined_spec["eta"],
                    eta_status=joined_spec["eta_status"],
                )
            )

        for interested_spec in spec["interested"]:
            db.session.add(
                Participation(
                    activity=activity,
                    user=users[interested_spec["name"]],
                    status="interested",
                    reason="Following this plan",
                    eta_label="Not committed",
                    eta_status="on_track",
                )
            )

        for waitlist_spec in spec["waitlist"]:
            db.session.add(
                Participation(
                    activity=activity,
                    user=users[waitlist_spec["name"]],
                    status="waitlist",
                    reason=waitlist_spec["reason"],
                    eta_label="Waitlisted",
                    eta_status="delayed",
                )
            )

        for message_spec in spec["messages"]:
            db.session.add(
                Message(
                    activity=activity,
                    author=users[message_spec["author"]],
                    body=message_spec["body"],
                    created_at=utcnow() - timedelta(minutes=message_spec["minutes_ago"]),
                )
            )

    db.session.commit()


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    host = os.getenv("HOST", "0.0.0.0")
    debug = os.getenv("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug)
