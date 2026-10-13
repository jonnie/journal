"""Tests for the Journal app and the set_password script."""

import contextlib
import hashlib
import hmac
import importlib.util
import json
import secrets
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import app as appmod

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts" / "set_password.py"
SECRET = "test-session-secret-0123456789"


def load_set_password():
    spec = importlib.util.spec_from_file_location("set_password", SCRIPTS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def scrypt_hash(password: str, salt: bytes) -> str:
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **appmod.SCRYPT_KW)
    return f"{salt.hex()}${digest.hex()}"


class JournalAppTests(unittest.TestCase):
    """HTTP-level tests; DATA/CONFIG are redirected to a temp dir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data_path = self.root / "data"
        self.config_path = self.root / "config.json"
        self._orig = (appmod.DATA, appmod.CONFIG)
        appmod.DATA, appmod.CONFIG = self.data_path, self.config_path
        self.addCleanup(self._restore_paths)

        self._stack = contextlib.ExitStack()
        self.addCleanup(self._stack.close)
        self.client = self._stack.enter_context(TestClient(appmod.app))

    def _restore_paths(self):
        appmod.DATA, appmod.CONFIG = self._orig

    # -- helpers --

    def set_config(self, password="pw-123", secret=SECRET):
        salt = secrets.token_bytes(16)
        self.config_path.write_text(
            json.dumps(
                {
                    "password": scrypt_hash(password, salt),
                    "secret": secret,
                }
            )
        )

    def login(self, password="pw-123"):
        self.set_config(password=password)
        r = self.client.post("/login", json={"password": password})
        self.assertEqual(r.status_code, 200)
        return r

    def _make_file(self, name: str, content: str) -> Path:
        p = appmod.DATA / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def _read(self, name: str) -> str:
        return (appmod.DATA / name).read_text(encoding="utf-8")

    # -- auth --

    def test_login_without_password_set_returns_400(self):
        r = self.client.post("/login", json={"password": "x"})
        self.assertEqual(r.status_code, 400)

    def test_login_wrong_password_returns_401(self):
        self.set_config(password="right")
        r = self.client.post("/login", json={"password": "wrong"})
        self.assertEqual(r.status_code, 401)

    def test_login_success_sets_cookie(self):
        r = self.login()
        self.assertTrue(r.cookies.get(appmod.COOKIE))

    def test_index_serves_login_page_when_unauthenticated(self):
        self.set_config()
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("login-form", r.text)

    def test_index_serves_app_when_authenticated(self):
        self.login()
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('class="authed"', r.text)
        self.assertNotIn("login-form", r.text)

    def test_api_requires_auth(self):
        self.set_config()
        self.assertEqual(self.client.get("/api/tree").status_code, 401)
        self.assertEqual(
            self.client.get(
                "/api/file", params={"name": "2026/January/01-01-2026.md"}
            ).status_code,
            401,
        )
        self.assertEqual(self.client.post("/api/create").status_code, 401)

    def test_forged_token_with_wrong_secret_rejected(self):
        self.set_config(secret=SECRET)
        token, _ = appmod.make_token("some-other-secret")
        self.client.cookies.set(appmod.COOKIE, token)
        self.assertEqual(self.client.get("/api/tree").status_code, 401)

    def test_expired_token_rejected(self):
        self.set_config(secret=SECRET)
        expiry = str(int(time.time()) - 10)
        sig = hmac.new(SECRET.encode(), expiry.encode(), hashlib.sha256).hexdigest()
        self.client.cookies.set(appmod.COOKIE, f"{expiry}.{sig}")
        self.assertEqual(self.client.get("/api/tree").status_code, 401)

    def test_logout_invalidate_session(self):
        self.login()
        self.assertEqual(self.client.post("/logout").status_code, 200)
        self.assertEqual(self.client.get("/api/tree").status_code, 401)

    # -- path safety --

    def test_get_file_rejects_absolute_path(self):
        self.login()
        r = self.client.get("/api/file", params={"name": "/etc/passwd"})
        self.assertEqual(r.status_code, 400)

    def test_get_file_rejects_traversal(self):
        self.login()
        r = self.client.get(
            "/api/file", params={"name": "2026/July/../../../../app.py"}
        )
        self.assertEqual(r.status_code, 400)

    def test_put_rejects_traversal_outside_data(self):
        self.login()
        r = self.client.put(
            "/api/file",
            json={"name": "2026/July/../../../evil.md", "content": "x"},
        )
        self.assertEqual(r.status_code, 400)
        self.assertFalse((appmod.BASE / "evil.md").exists())

    def test_get_file_rejects_non_md(self):
        self.login()
        r = self.client.get("/api/file", params={"name": "2026/July/notes.txt"})
        self.assertEqual(r.status_code, 400)

    # -- entries --

    def test_create_entry_today(self):
        self.login()
        today = date.today()
        r = self.client.post("/api/create")
        self.assertEqual(r.status_code, 200)
        name = (
            f"{today.year}/{appmod.MONTHS[today.month - 1]}"
            f"/{today.strftime('%d-%m-%Y.md')}"
        )
        self.assertEqual(r.json()["name"], name)
        self.assertTrue((appmod.DATA / name).is_file())
        header = self._read(name).splitlines()[0]
        want = (
            f"# {today.strftime('%A')} {appmod._ordinal(today.day)} "
            f"{appmod.MONTHS[today.month - 1]} {today.year}"
        )
        self.assertEqual(header, want)

    def test_create_entry_is_idempotent(self):
        self.login()
        first = self.client.post("/api/create").json()["name"]
        self._make_file(first, "# keep me\n")
        second = self.client.post("/api/create").json()["name"]
        self.assertEqual(second, first)
        self.assertEqual(self._read(first), "# keep me\n")

    def test_create_entry_with_offset(self):
        self.login()
        r = self.client.post("/api/create", params={"offset": 1})
        tomorrow = date.today() + timedelta(days=1)
        name = (
            f"{tomorrow.year}/{appmod.MONTHS[tomorrow.month - 1]}"
            f"/{tomorrow.strftime('%d-%m-%Y.md')}"
        )
        self.assertEqual(r.json()["name"], name)
        self.assertTrue((appmod.DATA / name).is_file())

    def test_get_file_returns_content_and_hash(self):
        self.login()
        self._make_file("2026/January/05-01-2026.md", "# Jan 5\n")
        r = self.client.get("/api/file", params={"name": "2026/January/05-01-2026.md"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["content"], "# Jan 5\n")
        self.assertEqual(body["hash"], appmod.file_hash("# Jan 5\n"))

    def test_get_file_missing_returns_404(self):
        self.login()
        r = self.client.get("/api/file", params={"name": "2026/January/05-01-2026.md"})
        self.assertEqual(r.status_code, 404)

    def test_put_file_updates_with_matching_base_hash(self):
        self.login()
        name = "2026/January/05-01-2026.md"
        self._make_file(name, "v1")
        r = self.client.put(
            "/api/file",
            json={"name": name, "content": "v2", "base_hash": appmod.file_hash("v1")},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["hash"], appmod.file_hash("v2"))
        self.assertEqual(self._read(name), "v2")

    def test_put_file_after_create(self):
        # the UI flow: /api/create makes the parent dirs, then a save
        self.login()
        name = self.client.post("/api/create").json()["name"]
        r = self.client.put("/api/file", json={"name": name, "content": "# new\n- a"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._read(name), "# new\n- a")

    def test_put_file_missing_parent_returns_404(self):
        self.login()
        r = self.client.put(
            "/api/file",
            json={"name": "2099/January/05-01-2099.md", "content": "v1"},
        )
        self.assertEqual(r.status_code, 404)

    def test_put_stale_base_hash_returns_409_with_disk_content(self):
        self.login()
        name = "2026/January/05-01-2026.md"
        self._make_file(name, "disk version")
        r = self.client.put(
            "/api/file",
            json={"name": name, "content": "mine", "base_hash": "stale"},
        )
        self.assertEqual(r.status_code, 409)
        body = r.json()
        self.assertEqual(body["content"], "disk version")
        self.assertEqual(body["hash"], appmod.file_hash("disk version"))
        self.assertEqual(self._read(name), "disk version")

    def test_delete_file(self):
        self.login()
        name = "2026/January/05-01-2026.md"
        self._make_file(name, "bye")
        r = self.client.delete("/api/file", params={"name": name})
        self.assertEqual(r.status_code, 200)
        self.assertFalse((appmod.DATA / name).exists())
        r = self.client.get("/api/file", params={"name": name})
        self.assertEqual(r.status_code, 404)

    # -- tree --

    def test_tree_lists_entries_in_order(self):
        self.login()
        self._make_file("2026/December/01-12-2026.md", "d")
        self._make_file("2026/January/02-01-2026.md", "j")
        self._make_file("2026/January/01-01-2026.md", "j")
        self._make_file("2025/March/15-03-2025.md", "old")
        r = self.client.get("/api/tree")
        self.assertEqual(r.status_code, 200)
        tree = r.json()
        self.assertEqual([y["name"] for y in tree], ["2025", "2026"])
        y2026 = tree[1]
        self.assertEqual(
            [m["name"] for m in y2026["children"]], ["January", "December"]
        )
        jan = y2026["children"][0]
        self.assertEqual(
            [f["name"] for f in jan["children"]],
            ["2026/January/01-01-2026.md", "2026/January/02-01-2026.md"],
        )

    def test_tree_empty_when_no_data(self):
        self.login()
        self.assertEqual(self.client.get("/api/tree").json(), [])

    def test_tree_ignores_non_md_files(self):
        self.login()
        self._make_file("2026/January/01-01-2026.md", "x")
        (appmod.DATA / "2026" / "January" / "notes.txt").write_text("nope")
        tree = self.client.get("/api/tree").json()
        names = [f["name"] for f in tree[0]["children"][0]["children"]]
        self.assertEqual(names, ["2026/January/01-01-2026.md"])


class OrdinalTests(unittest.TestCase):
    def test_ordinals(self):
        cases = {
            1: "1st",
            2: "2nd",
            3: "3rd",
            4: "4th",
            10: "10th",
            11: "11th",
            12: "12th",
            13: "13th",
            21: "21st",
            22: "22nd",
            23: "23rd",
            31: "31st",
            100: "100th",
            111: "111th",
        }
        for day, want in cases.items():
            with self.subTest(day=day):
                self.assertEqual(appmod._ordinal(day), want)


class SetPasswordTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = Path(self._tmp.name) / "config.json"
        self.ps = load_set_password()
        self._orig_config = self.ps.CONFIG
        self.ps.CONFIG = self.config
        self.addCleanup(self._restore_config)

    def _restore_config(self):
        self.ps.CONFIG = self._orig_config

    def test_scrypt_params_match_app(self):
        self.assertEqual(self.ps.SCRYPT_KW, appmod.SCRYPT_KW)

    def test_empty_password_rejected(self):
        with mock.patch.object(self.ps.getpass, "getpass", return_value=""):
            self.assertEqual(self.ps.main(), 1)

    def test_mismatched_password_rejected(self):
        with mock.patch.object(self.ps.getpass, "getpass", side_effect=["a", "b"]):
            self.assertEqual(self.ps.main(), 1)

    def test_sets_password_and_secret(self):
        with mock.patch.object(self.ps.getpass, "getpass", return_value="pw-123"):
            self.assertEqual(self.ps.main(), 0)
        cfg = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(len(cfg["password"].split("$")), 2)
        self.assertEqual(len(cfg["secret"]), 64)
        # The hash the script writes must verify through the app's path.
        self.assertTrue(appmod.password_ok("pw-123", cfg))
        self.assertFalse(appmod.password_ok("wrong", cfg))

    def test_preserves_existing_secret(self):
        self.config.write_text(json.dumps({"secret": "keepme"}))
        with mock.patch.object(self.ps.getpass, "getpass", return_value="pw-123"):
            self.assertEqual(self.ps.main(), 0)
        cfg = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(cfg["secret"], "keepme")
        self.assertTrue(appmod.password_ok("pw-123", cfg))


if __name__ == "__main__":
    unittest.main()
