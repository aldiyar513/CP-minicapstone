from datetime import date
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import Activity, Participation, User, create_app, db


@pytest.fixture()
def client(tmp_path):
    database_path = tmp_path / "test.db"
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path}",
        }
    )
    with app.test_client() as test_client:
        yield test_client, app


def test_core_pages_render(client):
    test_client, _app = client

    assert test_client.get("/").status_code == 200
    assert test_client.get("/activities/new").status_code == 200
    assert test_client.get("/activities/1").status_code == 200
    assert test_client.get("/profile").status_code == 200
    assert test_client.get("/activities/4/host").status_code == 200


def test_create_activity_creates_hosted_activity(client):
    test_client, app = client

    response = test_client.post(
        "/activities/new",
        data={
            "title": "Test Review Session",
            "category": "Study Session",
            "date": date.today().isoformat(),
            "time": "18:30",
            "location": "North Hall",
            "description": "Final review before the exam.",
            "capacity": "7",
            "min_reliability": "65",
            "role_name": ["Whiteboard Lead", "Snack Runner"],
            "role_type": ["Mandatory", "Optional"],
            "role_needed": ["1", "2"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/host" in response.headers["Location"]

    with app.app_context():
        created = Activity.query.filter_by(title="Test Review Session").first()
        assert created is not None
        assert created.host.name == "Ari Foster"
        host_participation = Participation.query.filter_by(activity_id=created.id, user_id=created.host_id).first()
        assert host_participation is not None
        assert host_participation.status == "joined"
        assert len(created.roles) == 2


def test_interest_join_and_leave_flow_updates_participation(client):
    test_client, app = client

    response = test_client.post("/activities/1/interest", follow_redirects=True)
    assert response.status_code == 200

    with app.app_context():
        ari = User.query.filter_by(name="Ari Foster").first()
        activity = Activity.query.filter_by(id=1).first()
        participation = Participation.query.filter_by(activity_id=activity.id, user_id=ari.id).first()
        assert participation is not None
        assert participation.status == "interested"

    response = test_client.post("/activities/1/join", follow_redirects=True)
    assert response.status_code == 200

    with app.app_context():
        ari = User.query.filter_by(name="Ari Foster").first()
        activity = Activity.query.filter_by(id=1).first()
        participation = Participation.query.filter_by(activity_id=activity.id, user_id=ari.id).first()
        assert participation.status == "joined"

    response = test_client.post("/activities/1/leave", follow_redirects=True)
    assert response.status_code == 200

    with app.app_context():
        ari = User.query.filter_by(name="Ari Foster").first()
        activity = Activity.query.filter_by(id=1).first()
        participation = Participation.query.filter_by(activity_id=activity.id, user_id=ari.id).first()
        assert participation is None


def test_eta_update_requires_joined_member_and_persists(client):
    test_client, app = client

    not_joined = test_client.post(
        "/activities/1/eta",
        json={"eta_status": "on_track", "eta_label": "About 8-15 min away"},
    )
    assert not_joined.status_code == 400

    joined = test_client.post("/activities/1/join", follow_redirects=True)
    assert joined.status_code == 200

    response = test_client.post(
        "/activities/1/eta",
        json={"eta_status": "arriving_soon", "eta_label": "Arriving soon • About 3-6 min away"},
    )
    assert response.status_code == 200
    assert response.get_json()["ok"] is True

    with app.app_context():
        ari = User.query.filter_by(name="Ari Foster").first()
        activity = Activity.query.filter_by(id=1).first()
        participation = Participation.query.filter_by(activity_id=activity.id, user_id=ari.id).first()
        assert participation.eta_status == "arriving_soon"
        assert "3-6 min away" in participation.eta_label


def test_host_can_confirm_waitlisted_user(client):
    test_client, app = client

    with app.app_context():
        activity = Activity.query.filter_by(title="Sunday Brunch Planning").first()
        user = User.query.filter_by(name="Santi").first()
        participation = Participation.query.filter_by(activity_id=activity.id, user_id=user.id).first()
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
