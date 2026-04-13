from datetime import date, time
from pathlib import Path
import sys

import pytest
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import Activity, Message, Participation, Role, User, create_app, db


def create_user(name: str, email: str, password: str, **overrides) -> User:
    return User(
        name=name,
        email=email,
        password_hash=generate_password_hash(password),
        city=overrides.get("city", "Buenos Aires"),
        home_area=overrides.get("home_area", "Palermo"),
        bio=overrides.get("bio", f"{name} uses EventMatch for real plans."),
        interests_csv=overrides.get("interests_csv", "Workout|Study"),
        gps_tracking=overrides.get("gps_tracking", "Visible only after I join"),
        avatar_url=overrides.get("avatar_url", ""),
        reliability_score=overrides.get("reliability_score", 88),
    )


@pytest.fixture()
def client(tmp_path):
    database_path = tmp_path / "test.db"
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path}",
        }
    )

    with app.app_context():
        ari = create_user("Ari Foster", "ari@example.com", "password123", reliability_score=92)
        maya = create_user("Maya Cruz", "maya@example.com", "password123", reliability_score=95)
        bruno = create_user("Bruno Diaz", "bruno@example.com", "password123", reliability_score=78)
        db.session.add_all([ari, maya, bruno])
        db.session.flush()

        study = Activity(
            title="Calculus Sprint",
            category="Study",
            activity_date=date.today(),
            start_time=time.fromisoformat("18:30"),
            location="North Hall",
            description="Focused review block before the exam.",
            capacity=6,
            min_reliability=65,
            host=maya,
        )
        db.session.add(study)
        db.session.flush()
        whiteboard = Role(activity=study, name="Whiteboard Lead", role_type="mandatory", needed_count=1)
        db.session.add(whiteboard)
        db.session.add(
            Participation(
                activity=study,
                user=maya,
                status="joined",
                reason="Host",
                eta_label="At venue",
                eta_status="checked_in",
            )
        )

        weekly = Activity(
            title="Sunday Gym Circle",
            category="Workout",
            activity_date=date.today(),
            start_time=time.fromisoformat("09:00"),
            location="Club Central",
            description="Weekly gym session for people who want a consistent training group.",
            capacity=5,
            min_reliability=50,
            recurring_weekly=True,
            host=ari,
        )
        db.session.add(weekly)
        db.session.flush()
        warmup = Role(activity=weekly, name="Warmup Lead", role_type="mandatory", needed_count=1)
        db.session.add(warmup)
        db.session.add(
            Participation(
                activity=weekly,
                user=ari,
                status="joined",
                reason="Host",
                eta_label="At venue",
                eta_status="checked_in",
            )
        )
        waitlisted = Participation(
            activity=weekly,
            user=bruno,
            status="waitlist",
            reason="Pending host review",
            eta_label="Waitlisted",
            eta_status="delayed",
        )
        db.session.add(waitlisted)
        db.session.add(Message(activity=weekly, author=ari, body="Bring water and resistance bands."))
        db.session.commit()

    with app.test_client() as test_client:
        yield test_client, app


def login(test_client, email="ari@example.com", password="password123"):
    return test_client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


def test_landing_and_auth_routes_render(client):
    test_client, _app = client

    assert test_client.get("/").status_code == 200
    assert test_client.get("/login").status_code == 200
    assert test_client.get("/register").status_code == 200
    assert test_client.get("/feed").status_code == 302


def test_register_and_edit_profile(client):
    test_client, app = client

    response = test_client.post(
        "/register",
        data={
            "name": "Leila Noor",
            "email": "leila@example.com",
            "password": "password123",
            "city": "Buenos Aires",
            "home_area": "Belgrano",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Manage your account" in response.data

    edit_response = test_client.post(
        "/profile/edit",
        data={
            "name": "Leila Noor",
            "email": "leila@example.com",
            "city": "Buenos Aires",
            "home_area": "Belgrano",
            "avatar_url": "https://example.com/leila.jpg",
            "bio": "I like recurring workout groups and startup meetups.",
            "gps_tracking": "Visible only after I join",
            "interests": ["Workout", "Networking"],
            "custom_interests": "Padel, Founders",
        },
        follow_redirects=True,
    )

    assert edit_response.status_code == 200

    with app.app_context():
        created = User.query.filter_by(email="leila@example.com").first()
        assert created is not None
        assert created.avatar_url == "https://example.com/leila.jpg"
        assert "Padel" in created.interests
        assert "Networking" in created.interests


def test_create_activity_creates_hosted_weekly_activity(client):
    test_client, app = client
    login(test_client)

    response = test_client.post(
        "/activities/new",
        data={
            "title": "Morning Run Club",
            "category": "Workout",
            "date": date.today().isoformat(),
            "time": "07:00",
            "location": "City Park",
            "description": "Short weekday run before work.",
            "capacity": "8",
            "min_reliability": "65",
            "recurring_weekly": "on",
            "role_name": ["Route Lead"],
            "role_type": ["Mandatory"],
            "role_needed": ["1"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/host" in response.headers["Location"]

    with app.app_context():
        created = Activity.query.filter_by(title="Morning Run Club").first()
        assert created is not None
        assert created.host.email == "ari@example.com"
        assert created.recurring_weekly is True


def test_interest_join_leave_and_eta_flow(client):
    test_client, app = client
    login(test_client)

    with app.app_context():
        activity = Activity.query.filter_by(title="Calculus Sprint").first()

    interest_response = test_client.post(f"/activities/{activity.id}/interest", follow_redirects=True)
    assert interest_response.status_code == 200

    with app.app_context():
        ari = User.query.filter_by(email="ari@example.com").first()
        participation = Participation.query.filter_by(activity_id=activity.id, user_id=ari.id).first()
        assert participation is not None
        assert participation.status == "interested"

    join_response = test_client.post(f"/activities/{activity.id}/join", follow_redirects=True)
    assert join_response.status_code == 200

    eta_response = test_client.post(
        f"/activities/{activity.id}/eta",
        json={"eta_status": "arriving_soon", "eta_label": "Arriving soon • About 3-6 min away"},
    )
    assert eta_response.status_code == 200

    leave_response = test_client.post(f"/activities/{activity.id}/leave", follow_redirects=True)
    assert leave_response.status_code == 200

    with app.app_context():
        ari = User.query.filter_by(email="ari@example.com").first()
        participation = Participation.query.filter_by(activity_id=activity.id, user_id=ari.id).first()
        assert participation is None


def test_host_can_confirm_waitlisted_user(client):
    test_client, app = client
    login(test_client)

    with app.app_context():
        activity = Activity.query.filter_by(title="Sunday Gym Circle").first()
        bruno = User.query.filter_by(email="bruno@example.com").first()
        participation = Participation.query.filter_by(activity_id=activity.id, user_id=bruno.id).first()
        participant_id = participation.id

    response = test_client.post(
        f"/activities/{activity.id}/host/participants/{participant_id}",
        data={"status": "joined", "assigned_role_id": ""},
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        updated = db.session.get(Participation, participant_id)
        assert updated.status == "joined"
