import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main import create_app, socketio


class ChatAppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "DATABASE": str(root / "test.sqlite3"),
                "UPLOAD_FOLDER": str(root / "uploads"),
            }
        )
        self.client = self.app.test_client()
        self.csrf = self._csrf_for(self.client)

    def tearDown(self):
        with self.app.app_context():
            from main import get_db

            get_db().close()
        self.temp_dir.cleanup()

    @staticmethod
    def _csrf_for(client):
        client.get("/register")
        with client.session_transaction() as session:
            return session["_csrf_token"]

    def _register(self, client, username, password="correct-horse-battery"):
        token = self._csrf_for(client)
        return client.post(
            "/register",
            data={
                "username": username,
                "password": password,
                "csrf_token": token,
            },
            follow_redirects=True,
        )

    def _api(self, path, data, method="post"):
        self.csrf = self._csrf_for(self.client)
        return getattr(self.client, method)(
            path,
            json=data,
            headers={"X-CSRF-Token": self.csrf},
        )

    def test_registration_and_password_login(self):
        response = self._register(self.client, "Alice_1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Alice_1", response.data)
        self.client.post(
            "/logout",
            data={"csrf_token": self.csrf},
            follow_redirects=True,
        )
        response = self.client.post(
            "/login",
            data={
                "username": "alice_1",
                "password": "correct-horse-battery",
                "csrf_token": self._csrf_for(self.client),
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Alice_1", response.data)

    def test_production_requires_a_strong_configured_secret_key(self):
        settings = {
            "TESTING": True,
            "REQUIRE_SECRET_KEY": True,
            "DATABASE": str(Path(self.temp_dir.name) / "production.sqlite3"),
            "UPLOAD_FOLDER": str(Path(self.temp_dir.name) / "production-uploads"),
        }
        for secret_key in (None, "too-short"):
            with self.subTest(secret_key=secret_key):
                with self.assertRaisesRegex(RuntimeError, "at least 32 characters"):
                    create_app({**settings, "SECRET_KEY": secret_key})

        app = create_app({**settings, "SECRET_KEY": "s" * 32})
        self.assertEqual(app.config["SECRET_KEY"], "s" * 32)

    def test_security_headers_are_present_and_hsts_is_https_only(self):
        response = self.client.get("/register")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertIn("https://cdn.socket.io", response.headers["Content-Security-Policy"])
        self.assertNotIn("Strict-Transport-Security", response.headers)
        self._register(self.client, "HeaderCheck")
        index_response = self.client.get("/")
        self.assertNotIn(b"onclick=", index_response.data)
        self.assertNotIn(b"fonts.googleapis.com", index_response.data)

        secure_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "SESSION_COOKIE_SECURE": True,
                "DATABASE": str(Path(self.temp_dir.name) / "secure.sqlite3"),
                "UPLOAD_FOLDER": str(Path(self.temp_dir.name) / "secure-uploads"),
            }
        )
        secure_response = secure_app.test_client().get("/register")
        self.assertEqual(
            secure_response.headers["Strict-Transport-Security"],
            "max-age=31536000",
        )

    def test_registration_validates_usernames_passwords_and_duplicates(self):
        response = self._register(self.client, "ab", password="short")
        self.assertIn(b"Usernames must be", response.data)
        self._register(self.client, "CaseName")
        second = self.app.test_client()
        response = self._register(second, "casename")
        self.assertIn(b"already taken", response.data)

    def test_admin_username_requires_configured_password(self):
        with patch.dict(
            "os.environ",
            {
                "ADMIN_USERNAME": "RootAdmin",
                "ADMIN_PASSWORD": "configured-admin-secret",
            },
        ):
            response = self._register(
                self.client,
                "rootadmin",
                password="ordinary-user-password",
            )
            self.assertIn(b"reserved for the configured administrator", response.data)
            self.assertEqual(
                self.client.get("/login").status_code,
                200,
            )
            self._register(
                self.client,
                "rootadmin",
                password="configured-admin-secret",
            )
        with self.app.app_context():
            from main import get_db

            role = get_db().execute(
                "SELECT role FROM users WHERE username = 'RootAdmin'"
            ).fetchone()["role"]
        self.assertEqual(role, "admin")

    def test_api_requires_csrf_and_profile_is_unique(self):
        self._register(self.client, "Alice")
        denied = self.client.post("/api/profile", json={"username": "Alice2"})
        self.assertEqual(denied.status_code, 400)
        second = self.app.test_client()
        self._register(second, "Bob")
        token = self._csrf_for(self.client)
        response = self.client.post(
            "/api/profile",
            json={"username": "Bob"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 409)

    def test_private_room_requires_invitation(self):
        self._register(self.client, "Owner")
        created = self._api(
            "/api/rooms",
            {"name": "Secret Room", "description": "private", "private": True},
        )
        self.assertEqual(created.status_code, 201)
        room_id = created.json["id"]
        guest = self.app.test_client()
        self._register(guest, "Guest")
        guest_token = self._csrf_for(guest)
        guest_socket = socketio.test_client(self.app, flask_test_client=guest)
        guest_socket.get_received()
        denied = guest.post(
            f"/api/rooms/{room_id}/join",
            headers={"X-CSRF-Token": guest_token},
        )
        self.assertEqual(denied.status_code, 403)
        invited = self._api(
            f"/api/rooms/{room_id}/invite",
            {"username": "Guest"},
        )
        self.assertEqual(invited.status_code, 200)
        event = next(
            event for event in guest_socket.get_received()
            if event["name"] == "room_invited"
        )
        self.assertEqual(event["args"][0]["room"]["id"], room_id)
        self.assertIn(
            room_id,
            [room["id"] for room in guest.get("/api/rooms").json["rooms"]],
        )
        joined = guest.post(
            f"/api/rooms/{room_id}/join",
            headers={"X-CSRF-Token": guest_token},
        )
        self.assertEqual(joined.status_code, 200)
        guest_socket.disconnect()

    def test_room_private_setting_must_be_boolean(self):
        self._register(self.client, "RoomCreator")
        response = self._api("/api/rooms", {"name": "Typed Room", "private": "false"})
        self.assertEqual(response.status_code, 400)
        room_names = [room["name"] for room in self.client.get("/api/rooms").json["rooms"]]
        self.assertNotIn("Typed Room", room_names)

    def test_socket_room_messages_persist_and_render_history(self):
        self._register(self.client, "Sender")
        socket_client = socketio.test_client(self.app, flask_test_client=self.client)
        self.assertTrue(socket_client.is_connected())
        socket_client.emit("join", {"room_id": 1})
        events = socket_client.get_received()
        self.assertTrue(any(event["name"] == "history" for event in events))
        acknowledgment = socket_client.emit(
            "message",
            {"room_id": 1, "body": "<b>plain text</b>"},
            callback=True,
        )
        self.assertEqual(acknowledgment, {"ok": True})
        events = socket_client.get_received()
        message = next(event["args"] for event in events if event["name"] == "message")
        self.assertEqual(message["body"], "<b>plain text</b>")
        response = self.client.get("/api/history?room=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["messages"][0]["body"], "<b>plain text</b>")
        socket_client.disconnect()

    def test_upload_must_be_allowed_and_is_private_until_attached(self):
        from io import BytesIO

        self._register(self.client, "Uploader")
        self.csrf = self._csrf_for(self.client)
        rejected = self.client.post(
            "/api/uploads",
            data={"file": (BytesIO(b"<html>"), "page.html")},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(rejected.status_code, 415)
        self.csrf = self._csrf_for(self.client)
        uploaded = self.client.post(
            "/api/uploads",
            data={"file": (BytesIO(b"test image bytes"), "photo.png")},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(uploaded.status_code, 201)
        socket_client = socketio.test_client(self.app, flask_test_client=self.client)
        socket_client.emit("join", {"room_id": 1})
        socket_client.get_received()
        socket_client.emit(
            "message",
            {
                "room_id": 1,
                "body": "",
                "attachment_id": uploaded.json["attachment_id"],
            },
        )
        events = socket_client.get_received()
        message = next(event["args"] for event in events if event["name"] == "message")
        self.assertEqual(message["attachment_id"], uploaded.json["attachment_id"])
        download = self.client.get(uploaded.json["url"])
        self.assertEqual(download.status_code, 200)
        socket_client.disconnect()

    def test_message_length_is_enforced(self):
        self._register(self.client, "LongMessage")
        socket_client = socketio.test_client(self.app, flask_test_client=self.client)
        socket_client.emit("join", {"room_id": 1})
        socket_client.get_received()
        acknowledgment = socket_client.emit(
            "message",
            {"room_id": 1, "body": "x" * 2001},
            callback=True,
        )
        self.assertEqual(acknowledgment, {"ok": False})
        events = socket_client.get_received()
        self.assertTrue(any(event["name"] == "error_message" for event in events))
        self.assertEqual(self.client.get("/api/history?room=1").json["messages"], [])
        socket_client.disconnect()

    def test_private_messages_persist_and_blocking_is_enforced(self):
        self._register(self.client, "Sender")
        receiver = self.app.test_client()
        self._register(receiver, "Receiver")
        sender_socket = socketio.test_client(self.app, flask_test_client=self.client)
        receiver_socket = socketio.test_client(self.app, flask_test_client=receiver)
        sender_socket.get_received()
        receiver_socket.get_received()
        sender_socket.emit(
            "message",
            {"recipient": "Receiver", "body": "private hello"},
        )
        received = receiver_socket.get_received()
        self.assertTrue(any(event["name"] == "private_message" for event in received))
        history = self.client.get("/api/private-history?user=Receiver")
        self.assertEqual(history.json["messages"][0]["body"], "private hello")

        receiver_token = self._csrf_for(receiver)
        blocked = receiver.post(
            "/api/users/Sender/block",
            headers={"X-CSRF-Token": receiver_token},
        )
        self.assertEqual(blocked.status_code, 200)
        receiver_socket.get_received()
        sender_socket.emit(
            "message",
            {"recipient": "Receiver", "body": "blocked hello"},
        )
        errors = sender_socket.get_received()
        self.assertTrue(any(event["name"] == "error_message" for event in errors))
        sender_socket.emit(
            "typing",
            {"recipient": "Receiver", "typing": True},
        )
        self.assertFalse(
            any(event["name"] == "typing" for event in receiver_socket.get_received())
        )
        self.assertEqual(
            self.client.get("/api/private-history?user=Receiver").status_code,
            403,
        )
        self.assertEqual(
            self.client.get("/api/search?q=private%20hello").json["messages"],
            [],
        )
        sender_socket.disconnect()
        receiver_socket.disconnect()

    def test_search_reactions_pins_and_read_receipts(self):
        self._register(self.client, "RoomOwner")
        created = self._api("/api/rooms", {"name": "Feature Room"})
        room_id = created.json["id"]
        second = self.app.test_client()
        self._register(second, "Reader")
        sender_socket = socketio.test_client(self.app, flask_test_client=self.client)
        reader_socket = socketio.test_client(self.app, flask_test_client=second)
        sender_socket.emit("join", {"room_id": room_id})
        reader_socket.emit("join", {"room_id": room_id})
        sender_socket.get_received()
        reader_socket.get_received()
        sender_socket.emit(
            "message",
            {"room_id": room_id, "body": "searchable rocketmessage"},
        )
        event = next(
            event for event in sender_socket.get_received()
            if event["name"] == "message"
        )
        message_id = event["args"]["id"]
        searched = self.client.get("/api/search?q=rocketmessage")
        self.assertEqual(searched.json["messages"][0]["id"], message_id)
        wildcard_search = self.client.get("/api/search?q=rocket%")
        self.assertEqual(wildcard_search.json["messages"], [])

        self.assertEqual(
            self._api(f"/api/messages/{message_id}/react", {"emoji": "👍"}).status_code,
            200,
        )
        pinned = self._api(f"/api/messages/{message_id}/pin", {})
        self.assertTrue(pinned.json["ok"])
        reader_socket.emit("read", {"room_id": room_id})
        reader_socket.get_received()
        self.assertEqual(
            self.client.get(f"/api/history?room={room_id}").json["messages"][0]["read_count"],
            1,
        )
        sender_socket.disconnect()
        reader_socket.disconnect()

    def test_room_ban_revokes_active_and_api_access(self):
        self._register(self.client, "Moderator")
        created = self._api("/api/rooms", {"name": "Moderated Room"})
        room_id = created.json["id"]
        target = self.app.test_client()
        self._register(target, "TargetUser")
        moderator_socket = socketio.test_client(self.app, flask_test_client=self.client)
        target_socket = socketio.test_client(self.app, flask_test_client=target)
        moderator_socket.emit("join", {"room_id": room_id})
        target_socket.emit("join", {"room_id": room_id})
        moderator_socket.get_received()
        target_socket.get_received()

        target_socket.emit(
            "message",
            {"room_id": room_id, "body": "message from soon-to-be-banned user"},
        )
        target_message = next(
            event["args"] for event in target_socket.get_received()
            if event["name"] == "message"
        )
        banned = self._api(
            f"/api/rooms/{room_id}/ban",
            {"username": "TargetUser"},
        )
        self.assertEqual(banned.status_code, 200)
        ban_events = target_socket.get_received()
        self.assertTrue(any(event["name"] == "room_banned" for event in ban_events))
        self.assertEqual(
            target.get(f"/api/history?room={room_id}").status_code,
            403,
        )
        target_csrf = self._csrf_for(target)
        self.assertEqual(
            target.post(
                f"/api/messages/{target_message['id']}/delete",
                json={},
                headers={"X-CSRF-Token": target_csrf},
            ).status_code,
            404,
        )
        self.assertEqual(
            target.post(
                f"/api/messages/{target_message['id']}/react",
                json={"emoji": "👍"},
                headers={"X-CSRF-Token": target_csrf},
            ).status_code,
            404,
        )
        self.assertEqual(
            target.get("/api/search?q=soon-to-be-banned").json["messages"],
            [],
        )

        target_socket.get_received()
        moderator_socket.emit(
            "message",
            {"room_id": room_id, "body": "message after ban"},
        )
        self.assertTrue(
            any(event["name"] == "message" for event in moderator_socket.get_received())
        )
        self.assertFalse(
            any(event["name"] == "message" for event in target_socket.get_received())
        )
        moderator_socket.disconnect()
        target_socket.disconnect()


if __name__ == "__main__":
    unittest.main()
