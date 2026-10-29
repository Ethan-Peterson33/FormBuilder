"""Tests for scripts/ (start-up, database check, health check helpers).

Docker is replaced by a fake ``docker`` executable on PATH so container
discovery is exercised without Docker. Database-backed cases need
``DATABASE_URL`` and are skipped otherwise.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import _common  # noqa: E402
import _postgres  # noqa: E402

# -- _common -------------------------------------------------------------------

def test_parse_env_text_handles_quotes_comments_and_export():
    text = "# comment\nexport A=1\nB=\"two words\"\nC='x'\nD=val # trailing\nE=\nnot a line\n"
    assert _common.parse_env_text(text) == {"A": "1", "B": "two words", "C": "x", "D": "val", "E": ""}


def test_update_env_file_creates_from_template_and_preserves_comments(tmp_path: Path):
    template = tmp_path / ".env.example"
    template.write_text("# header\nPOSTGRES_PORT=55432\n# FORMBUILDER_DB_TIMEOUT=5\nDATABASE_URL=postgresql://a:b@localhost:55432/x\n")
    env = tmp_path / ".env"

    changed = _common.update_env_file(
        {"DATABASE_URL": "postgresql://u:p@localhost:54329/db", "POSTGRES_PORT": "54329",
         "FORMBUILDER_DB_TIMEOUT": "5", "EXTRA": "1"},
        env, template,
    )
    assert changed == ["DATABASE_URL", "POSTGRES_PORT", "FORMBUILDER_DB_TIMEOUT", "EXTRA"]
    text = env.read_text()
    assert text.startswith("# header\n")
    assert "POSTGRES_PORT=54329\n" in text
    assert "FORMBUILDER_DB_TIMEOUT=5\n" in text and "# FORMBUILDER_DB_TIMEOUT" not in text  # placeholder replaced
    assert "EXTRA=1" in text
    assert _common.read_env_file(env)["DATABASE_URL"] == "postgresql://u:p@localhost:54329/db"

    # Idempotent.
    assert _common.update_env_file({"POSTGRES_PORT": "54329"}, env, template) == []
    assert env.read_text() == text


def test_mask_and_split_url():
    assert _common.split_url("user=u password=p host=h port=1 dbname=d")["dbname"] == "d"
    url = "postgresql://formbuilder:secret@localhost:55432/formbuilder"
    assert _common.mask_url(url) == "postgresql://formbuilder:***@localhost:55432/formbuilder"
    assert _common.split_url(url) == {
        "user": "formbuilder", "password": "secret", "host": "localhost", "port": "55432", "dbname": "formbuilder",
    }
    assert _common.build_url("u", "p", "h", 1, "d") == "postgresql://u:p@h:1/d"


def test_supported_python_range():
    assert _common.is_supported_python((3, 11, 0))
    assert _common.is_supported_python((3, 13, 9))
    assert not _common.is_supported_python((3, 14, 0))
    assert not _common.is_supported_python((3, 10, 12))
    assert not _common.is_supported_python(None)


def test_find_python_returns_a_supported_interpreter_or_none():
    found = _common.find_python()
    if found is not None:
        executable, version = found
        assert _common.is_supported_python(version)
        assert _common.python_version_of(executable) == version


def test_find_python_honours_explicit_choice():
    executable, version = _common.find_python(sys.executable)
    assert executable == sys.executable and version[:2] == sys.version_info[:2]


# -- _postgres: docker discovery with a fake CLI ------------------------------

def _container(name: str, image: str, status: str, port: int, user: str, password: str, db: str) -> dict:
    return {
        "Name": f"/{name}", "State": {"Status": status, "Running": status == "running"},
        "Config": {"Image": image, "Env": [f"POSTGRES_USER={user}", f"POSTGRES_PASSWORD={password}", f"POSTGRES_DB={db}"]},
        "NetworkSettings": {"Ports": {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": str(port)}]}},
    }


DEFAULT_CONTAINERS = {
    "other-project-db-1": _container("other-project-db-1", "postgres:16", "running", 54329, "postgres", "pw", "other"),
    "cache": {"Name": "/cache", "State": {"Status": "running"}, "Config": {"Image": "redis:7", "Env": []}, "NetworkSettings": {"Ports": {}}},
    "formbuilder-postgres": _container("formbuilder-postgres", "postgres:15-alpine", "running", 55432, "formbuilder", "formbuilder", "formbuilder"),
    "old-db": _container("old-db", "postgres:14", "exited", 54330, "postgres", "x", "legacy"),
}

# A stateful fake `docker` CLI: `ps` lists running containers, `ps -a` all of
# them, `inspect` returns the stored JSON, `start` flips a container to running.
FAKE_DOCKER = """#!/usr/bin/env python3
import json, sys
path = {data!r}
data = json.load(open(path))
if data.get("daemon_down"):
    print("Cannot connect to the Docker daemon. Is the docker daemon running?", file=sys.stderr); sys.exit(1)
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "info":
    sys.exit(0)
if cmd == "ps":
    show_all = "-a" in sys.argv
    for name, c in data["containers"].items():
        if show_all or c["State"]["Status"] == "running":
            print(json.dumps({{"Image": c["Config"]["Image"], "Names": name, "Status": c["State"]["Status"]}}))
    sys.exit(0)
if cmd == "inspect":
    print(json.dumps([data["containers"][n] for n in sys.argv[2:]])); sys.exit(0)
if cmd == "start":
    c = data["containers"][sys.argv[2]]
    c["State"]["Status"] = "running"; c["State"]["Running"] = True
    json.dump(data, open(path, "w")); print(sys.argv[2]); sys.exit(0)
if cmd == "compose":
    print("Error response from daemon: Conflict. The container name is already in use", file=sys.stderr); sys.exit(1)
sys.exit(1)
"""


class FakeDocker:
    def __init__(self, data_file: Path):
        self.data_file = data_file

    def load(self) -> dict:
        return json.loads(self.data_file.read_text())

    def save(self, data: dict) -> None:
        self.data_file.write_text(json.dumps(data))

    def set_status(self, name: str, status: str) -> None:
        data = self.load()
        data["containers"][name]["State"]["Status"] = status
        data["containers"][name]["State"]["Running"] = status == "running"
        self.save(data)

    def status(self, name: str) -> str:
        return self.load()["containers"][name]["State"]["Status"]

    def set_containers(self, containers: dict) -> None:
        data = self.load()
        data["containers"] = containers
        self.save(data)

    def set_daemon_down(self, down: bool) -> None:
        data = self.load()
        data["daemon_down"] = down
        self.save(data)


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    if os.name == "nt":
        pytest.skip("fake docker script needs a POSIX shebang")
    data = tmp_path / "docker_data.json"
    data.write_text(json.dumps({"containers": DEFAULT_CONTAINERS, "daemon_down": False}))
    script = tmp_path / "docker"
    script.write_text(FAKE_DOCKER.format(data=str(data)))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    return FakeDocker(data)


def test_docker_postgres_containers_includes_stopped_and_ranks_running_first(fake_docker):
    containers = _postgres.docker_postgres_containers()
    assert [c.name for c in containers] == ["formbuilder-postgres", "other-project-db-1", "old-db"]
    ours, other, old = containers
    assert ours.running and other.running and not old.running and old.status == "exited"
    assert (ours.host_port, ours.user, ours.password, ours.dbname) == (55432, "formbuilder", "formbuilder", "formbuilder")
    assert (other.host_port, other.user, other.password, other.dbname) == (54329, "postgres", "pw", "other")
    assert _postgres.container_candidate(other).url == "postgresql://postgres:pw@localhost:54329/other"
    assert "exited" in old.summary and "host port 54330" in old.summary


def test_stopped_container_ranks_first_only_among_stopped(fake_docker):
    fake_docker.set_status("formbuilder-postgres", "exited")
    names = [c.name for c in _postgres.docker_postgres_containers()]
    assert names == ["other-project-db-1", "formbuilder-postgres", "old-db"]


def test_start_container_flips_state(fake_docker):
    assert fake_docker.status("old-db") == "exited"
    started, message = _postgres.start_container("old-db")
    assert started and message == "old-db"
    assert fake_docker.status("old-db") == "running"
    started, message = _postgres.start_container("does-not-exist")
    assert not started


def test_docker_state_reports_daemon_down(fake_docker):
    assert _common.docker_state() == "ok"
    fake_docker.set_daemon_down(True)
    assert _common.docker_state() == "daemon-down"
    assert _postgres.docker_postgres_containers() == []


def test_docker_state_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("PATH", str(tmp_path))  # nothing on PATH
    assert _common.docker_state() == "absent"


def test_gather_candidates_order_and_deduplication(fake_docker):
    candidates, containers = _postgres.gather_candidates(
        None, "postgresql://u:p@localhost:5432/formbuilder", "postgresql://u:p@localhost:5432/formbuilder",
        use_docker=True,
    )
    assert len(containers) == 3                      # stopped ones are reported ...
    assert all(c.container != "old-db" for c in candidates)  # ... but not tried
    assert [c.source.split(",")[0] for c in candidates] == [
        "DATABASE_URL in the environment",          # .env duplicate dropped
        "docker container formbuilder-postgres",
        "docker container other-project-db-1",
        "default credentials on localhost:55432",
        # localhost:5432 with the same credentials equals the configured URL -> de-duplicated
    ]
    # Fallbacks reuse the credentials from the configured URL.
    assert candidates[-1].url == "postgresql://u:p@localhost:55432/formbuilder"


def test_gather_candidates_without_docker_or_urls():
    candidates, containers = _postgres.gather_candidates(None, None, None, use_docker=False)
    assert containers == []
    assert [c.url for c in candidates] == [
        "postgresql://formbuilder:formbuilder@localhost:55432/formbuilder",
        "postgresql://formbuilder:formbuilder@localhost:5432/formbuilder",
    ]


def test_only_container_filter(fake_docker):
    candidates, containers = _postgres.gather_candidates(None, None, None, use_docker=True, only_container="other-project-db-1")
    assert [c.name for c in containers] == ["other-project-db-1"]
    assert candidates[0].container == "other-project-db-1"


def test_env_url_on_a_running_containers_port_is_tagged_with_the_container(fake_docker):
    """So a container whose PostgreSQL is still starting is waited for, even
    when the URL came from .env rather than from docker inspect."""
    candidates, _ = _postgres.gather_candidates(None, "postgresql://postgres:pw@127.0.0.1:54329/other", None, use_docker=True)
    assert candidates[0].source == "DATABASE_URL in the environment"
    assert candidates[0].container == "other-project-db-1"
    # A port nobody publishes stays untagged.
    candidates, _ = _postgres.gather_candidates(None, "postgresql://x:y@localhost:1/z", None, use_docker=True)
    assert candidates[0].container is None


def test_looks_transient():
    assert _postgres.looks_transient('connection to server at "127.0.0.1", port 54329 failed: Connection refused')
    assert _postgres.looks_transient("FATAL: the database system is starting up")
    assert not _postgres.looks_transient('FATAL: password authentication failed for user "x"')
    assert not _postgres.looks_transient('FATAL: database "nope" does not exist')


def test_wait_for_connection_gives_up_at_the_deadline():
    import time

    candidate = _postgres.Candidate("postgresql://x:y@127.0.0.1:1/nope", "test")
    started = time.monotonic()
    result = _postgres.wait_for_connection(candidate, 1.5, poll=0.2)
    assert not result.ok and 1.4 <= time.monotonic() - started < 6


# -- against a real database ----------------------------------------------------

from tests.conftest import DATABASE_URL  # noqa: E402


@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")
def test_try_connect_reports_success_and_failure():
    good = _postgres.try_connect(_postgres.Candidate(DATABASE_URL, "test"))
    assert good.ok and good.server_version
    bad = _postgres.try_connect(_postgres.Candidate("postgresql://x:y@127.0.0.1:1/nope", "test"), timeout=1)
    assert not bad.ok and "failed" in bad.message.lower()


@pytest.fixture
def make_scratch_db():
    """Factory for empty scratch databases on the DATABASE_URL server."""
    import uuid

    import psycopg

    created: list[str] = []

    def _make() -> str:
        name = f"formbuilder_test_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{name}"')
        created.append(name)
        parts = _common.split_url(DATABASE_URL)  # a URL, like the real DATABASE_URL
        return _common.build_url(parts["user"], parts["password"], parts["host"], parts["port"], name)

    yield _make
    with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
        for name in created:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")
def test_check_db_script_on_migrated_and_unmigrated_databases(pg_scratch_url: str, capsys):
    import check_db
    from db import migrate

    assert check_db.check_database(pg_scratch_url) == 0
    out = capsys.readouterr().out
    assert "schema matches" in out and all(v in out for v in (migrate.version_of(p) for p in migrate.migration_files()))

    import psycopg
    name = "formbuilder_test_checkdb_unmigrated"
    with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        url = psycopg.conninfo.make_conninfo(DATABASE_URL, dbname=name)
        assert check_db.check_database(url) == 1
        out = capsys.readouterr()
        assert "pending" in out.out and "db.migrate" in out.out
    finally:
        with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_check_db_reports_unreachable_server(capsys):
    import check_db

    assert check_db.check_database("postgresql://x:y@127.0.0.1:1/nope") == 1
    assert "cannot connect" in capsys.readouterr().err


def test_check_health_reports_unreachable_api(capsys):
    import check_health

    assert check_health.check_api("http://127.0.0.1:1") == 1
    assert "not reachable" in capsys.readouterr().err


# -- start.py stage 2 end-to-end (no venv/pip: runs in the current interpreter) --

@pytest.fixture
def isolated_start(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """stage2 without touching the real .env, environment or fallback ports."""
    import start

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(start, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(_postgres, "FALLBACK_PORTS", ())
    monkeypatch.chdir(tmp_path)
    return start


@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")
def test_start_stage2_migrates_imports_and_verifies(isolated_start, make_scratch_db, capsys):
    import psycopg

    url = make_scratch_db()
    code = isolated_start.main(["--stage2", "--no-serve", "--no-docker", "--database-url", url])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "applied 0001_initial" in out and "imported" in out and "verified" in out
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT count(*) FROM forms").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] >= 2
    # .env was written next to the test, not in the repository.
    written = _common.read_env_file(isolated_start.ENV_FILE)
    assert written["DATABASE_URL"] == url and "FORMBUILDER_STORAGE" not in written

    # Second run: nothing to do, nothing re-imported.
    code = isolated_start.main(["--stage2", "--no-serve", "--no-docker", "--database-url", url])
    out = capsys.readouterr().out
    assert code == 0 and "schema up to date" in out and "not importing" in out


@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")
def test_start_stage2_starts_a_stopped_container(isolated_start, make_scratch_db, fake_docker, capsys):
    """The field report: the postgres container exists but is stopped."""
    url = make_scratch_db()
    parts = _common.split_url(url)
    fake_docker.set_containers({
        "team-db": _container("team-db", "postgres:16", "exited", int(parts["port"]), parts["user"], parts["password"], parts["dbname"]),
    })
    code = isolated_start.main(["--stage2", "--no-serve"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "docker start team-db" in out
    assert fake_docker.status("team-db") == "running"
    assert "applied 0001_initial" in out
    assert _common.read_env_file(isolated_start.ENV_FILE)["POSTGRES_PORT"] == parts["port"]


def test_start_stage2_respects_no_start_containers(isolated_start, fake_docker, capsys):
    fake_docker.set_containers({"team-db": _container("team-db", "postgres:16", "exited", 1, "u", "p", "d")})
    code = isolated_start.main(["--stage2", "--no-serve", "--no-start-containers"])
    captured = capsys.readouterr()
    assert code == 1
    assert "--no-start-containers" in captured.out
    assert "No PostgreSQL instance answered" in captured.err
    assert fake_docker.status("team-db") == "exited"
    assert not isolated_start.ENV_FILE.exists()


def test_start_stage2_reports_daemon_down(isolated_start, fake_docker, capsys):
    fake_docker.set_daemon_down(True)
    code = isolated_start.main(["--stage2", "--no-serve"])
    captured = capsys.readouterr()
    assert code == 1
    assert "daemon is not running" in captured.out and "Start Docker" in captured.out


def test_start_stage2_in_codespaces_does_not_inspect_or_start_docker(isolated_start, fake_docker, monkeypatch, capsys):
    import start

    monkeypatch.setenv("CODESPACES", "true")
    fake_docker.set_containers({"team-db": _container("team-db", "postgres:16", "exited", 1, "u", "p", "d")})
    monkeypatch.setattr(start, "docker_state", lambda: pytest.fail("Docker should not be inspected in Codespaces"))

    code = isolated_start.main(["--stage2", "--no-serve"])
    captured = capsys.readouterr()

    assert code == 1
    assert "Codespaces detected" in captured.out
    assert "Set DATABASE_URL" in captured.out
    assert fake_docker.status("team-db") == "exited"


def test_start_stage2_unknown_container_name(isolated_start, fake_docker, capsys):
    code = isolated_start.main(["--stage2", "--no-serve", "--container", "nope"])
    assert code == 1 and "no container named 'nope'" in capsys.readouterr().err


def test_start_stage2_does_not_compose_when_a_container_exists(isolated_start, fake_docker, capsys):
    """A stopped container that cannot be reached must not lead to `compose up`
    (that is what produced the name conflict)."""
    fake_docker.set_containers({"team-db": _container("team-db", "postgres:16", "exited", 1, "u", "p", "d")})
    code = isolated_start.main(["--stage2", "--no-serve"])
    captured = capsys.readouterr()
    assert code == 1
    assert "docker start team-db" in captured.out
    assert "compose" not in captured.out and "Conflict" not in captured.err


# -- start.py stage 1: interpreter, venv, requirements, hand-over -------------------

def test_stage1_creates_venv_installs_and_hands_over(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd):
    """Real venv creation with the current interpreter, an empty requirements
    file (no network), then the hand-over to stage 2 inside that venv - which
    must fail clearly because psycopg is not installed there."""
    import start

    venv_dir = tmp_path / "venv"
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("# nothing\n")
    monkeypatch.setattr(start, "VENV_DIR", venv_dir)
    monkeypatch.setattr(start, "REQUIREMENTS", requirements)
    monkeypatch.setattr(start, "STAMP", venv_dir / ".requirements.sha256")

    code = start.main(["--python", sys.executable, "--no-serve", "--no-docker"])
    out = capfd.readouterr()
    assert code == 1, out
    assert "Creating" in out.out and "requirements installed" in out.out
    assert _common.venv_python(venv_dir).exists()
    assert (venv_dir / ".requirements.sha256").read_text()
    assert "psycopg is not installed" in out.err  # stage 2 ran in the new venv and said why it stopped

    # Second run: venv reused, requirements unchanged.
    code = start.main(["--python", sys.executable, "--no-serve", "--no-docker"])
    out = capfd.readouterr()
    assert "requirements unchanged" in out.out and "Creating" not in out.out

    # Changed requirements -> reinstall; --recreate-venv -> rebuilt.
    requirements.write_text("# changed\n")
    start.main(["--python", sys.executable, "--no-serve", "--no-docker"])
    assert "pip install" in capfd.readouterr().out
    start.main(["--python", sys.executable, "--no-serve", "--no-docker", "--recreate-venv"])
    assert "Rebuilding" in capfd.readouterr().out


def test_stage1_reports_missing_interpreter(monkeypatch: pytest.MonkeyPatch, capsys):
    import start

    monkeypatch.setattr(start, "find_python", lambda preferred=None: None)
    assert start.main(["--no-serve"]) == 1
    assert "No Python" in capsys.readouterr().err


def test_stage1_rejects_bogus_python():
    import start

    with pytest.raises(SystemExit, match="not a working Python"):
        start.main(["--python", "/definitely/not/python", "--no-serve"])


# -- API launch + health scripts against a live server ------------------------------

def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")
def test_launch_api_and_check_health_against_a_live_server(pg_scratch_url: str, monkeypatch: pytest.MonkeyPatch, capsys):
    import start
    import check_health

    monkeypatch.setenv("DATABASE_URL", pg_scratch_url)
    port = _free_port()
    process, status = start.launch_api("127.0.0.1", port, reload=False, python=Path(sys.executable))
    try:
        assert process.poll() is None, "uvicorn died"
        assert status is not None and status[0] == 200, status
        start.report_health(status)
        assert "healthy" in capsys.readouterr().out

        assert check_health.check_api(f"http://127.0.0.1:{port}") == 0
        out = capsys.readouterr().out
        assert "/health 200" in out and "GET /forms 200" in out and "GET /option-lists 200" in out and "submission(s)" in out
        assert check_health.main(["--url", f"http://127.0.0.1:{port}"]) == 0
        assert "RESULT: OK" in capsys.readouterr().out
    finally:
        process.terminate()
        process.wait(timeout=15)


def test_report_health_variants(capsys):
    import start

    start.report_health(None, wait_seconds=5)
    assert "did not answer" in capsys.readouterr().out
    start.report_health((503, {"database": {"error": {"message": "Database unavailable", "error": "refused", "hint": "check"}}}))
    captured = capsys.readouterr()
    assert "503" in captured.err and "check" in captured.out


def test_check_health_reports_unhealthy_api(monkeypatch: pytest.MonkeyPatch, capsys):
    import check_health

    responses = {
        "/health": (503, {"status": "error", "database": {"error": {"message": "Database unavailable", "error": "refused", "hint": "start it"},
                                                            "pendingMigrations": []}}),
        "/forms": (503, {"detail": {"message": "Database unavailable", "error": "refused"}}),
        "/option-lists": (503, {"detail": "plain detail"}),
        "/submissions?limit=1": (None, None),
    }
    monkeypatch.setattr(check_health, "get", lambda url, timeout=5.0: (*responses[url.split("http://x", 1)[1]], "boom"))
    assert check_health.check_api("http://x") == 1
    captured = capsys.readouterr()
    assert "/health 503" in captured.err and "start it" in captured.out
    assert "GET /forms 503" in captured.err and "plain detail" in captured.err


# -- check_db explanations -----------------------------------------------------------

def test_check_db_explains_stopped_container(fake_docker, capsys):
    import check_db

    fake_docker.set_containers({"team-db": _container("team-db", "postgres:16", "exited", 1, "u", "p", "d")})
    assert check_db.check_database("postgresql://u:p@127.0.0.1:1/d") == 1
    out = capsys.readouterr().out
    assert "team-db" in out and "docker start NAME" in out


def test_check_db_explains_daemon_down_and_running_container(fake_docker, capsys):
    import check_db

    fake_docker.set_containers({"team-db": _container("team-db", "postgres:16", "running", 1, "u", "p", "d")})
    assert check_db.check_database("postgresql://u:p@127.0.0.1:1/d") == 1
    assert "A container is running" in capsys.readouterr().out
    fake_docker.set_daemon_down(True)
    assert check_db.check_database("postgresql://u:p@127.0.0.1:1/d") == 1
    assert "daemon is not running" in capsys.readouterr().out
    fake_docker.set_containers({})
    fake_docker.set_daemon_down(False)
    assert check_db.check_database("postgresql://u:p@127.0.0.1:1/d") == 1
    assert "No postgres container exists" in capsys.readouterr().out


def test_check_db_main_resolves_url_sources(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys):
    import check_db

    monkeypatch.setattr(check_db, "reexec_in_venv_if_needed", lambda: None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(check_db, "ENV_FILE", tmp_path / ".env")
    assert check_db.main([]) == 1
    assert "DATABASE_URL is not set" in capsys.readouterr().err

    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://u:p@127.0.0.1:1/d\n")
    assert check_db.main([]) == 1
    out = capsys.readouterr().out
    assert "DATABASE_URL in" in out and "PROBLEMS FOUND" in out
    assert check_db.resolve_url("postgresql://flag@h/d")[1] == "--database-url"


@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")
def test_start_stage2_new_install_uses_compose_when_no_container_exists(isolated_start, make_scratch_db, fake_docker, tmp_path: Path, capsys):
    """No postgres container at all -> `docker compose up -d postgres`, then
    wait for it using the POSTGRES_* values from .env."""
    url = make_scratch_db()
    parts = _common.split_url(url)
    fake_docker.set_containers({})
    # Make the fake `compose up` succeed.
    data = fake_docker.load(); data["compose_ok"] = True; fake_docker.save(data)
    script = Path(os.environ["PATH"].split(os.pathsep)[0]) / "docker"
    script.write_text(script.read_text().replace(
        'if cmd == "compose":',
        'if cmd == "compose" and data.get("compose_ok"):\n    sys.exit(0)\nif cmd == "compose":'))
    isolated_start.ENV_FILE.write_text(
        f"POSTGRES_USER={parts['user']}\nPOSTGRES_PASSWORD={parts['password']}\n"
        f"POSTGRES_DB={parts['dbname']}\nPOSTGRES_PORT={parts['port']}\n"
    )
    code = isolated_start.main(["--stage2", "--no-serve"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "starting docker compose service" in out and "docker compose (this repository)" in out
    assert "applied 0001_initial" in out
    assert _common.read_env_file(isolated_start.ENV_FILE)["DATABASE_URL"].endswith(f"/{parts['dbname']}")


def test_start_stage2_compose_failure_is_reported(isolated_start, fake_docker, capsys):
    fake_docker.set_containers({})
    code = isolated_start.main(["--stage2", "--no-serve"])
    captured = capsys.readouterr()
    assert code == 1
    assert "docker compose up failed" in captured.err and "POSTGRES_PORT" in captured.err


@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")
def test_smoke_test_script_passes_against_a_live_postgres_server(make_scratch_db, monkeypatch: pytest.MonkeyPatch, capsys):
    """scripts/smoke_test.py: every endpoint, against uvicorn on a scratch database."""
    import smoke_test
    import start
    from db import migrate

    url = make_scratch_db()
    migrate.migrate(url)
    monkeypatch.setenv("DATABASE_URL", url)
    port = _free_port()
    process, status = start.launch_api("127.0.0.1", port, reload=False, python=Path(sys.executable))
    try:
        assert status is not None and status[0] == 200, status
        assert smoke_test.main(["--url", f"http://127.0.0.1:{port}"]) == 0
        out = capsys.readouterr().out
        assert "RESULT: OK" in out and "soft delete verified" in out
    finally:
        process.terminate()
        process.wait(timeout=15)


def test_smoke_test_reports_failures_clearly(capsys):
    import smoke_test

    assert smoke_test.main(["--url", "http://127.0.0.1:1"]) == 1
    captured = capsys.readouterr()
    assert "not reachable" in captured.err and "RESULT: FAILED" in captured.out
