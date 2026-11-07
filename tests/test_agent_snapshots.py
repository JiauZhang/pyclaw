"""Published agent notes: when a project copy is offered and when it replaces."""

from pyclaw.team.snapshots import SnapshotStore


def _write(directory, text):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "MEMORY.md").write_text(text, encoding="utf-8")


def _snapshot(root, agent_type, text, when="2026-01-01T00:00:00Z"):
    _write(root / agent_type, text)
    (root / agent_type / "snapshot.json").write_text(f'{{"updatedAt": "{when}"}}', encoding="utf-8")


def test_a_first_snapshot_run_seeds_the_local_notes(tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    local = tmp_path / "local" / "reviewer"
    _snapshot(tmp_path / "snapshots", "reviewer", "snapshot note")
    assert store.sync("reviewer", local) == "initialized"
    assert "snapshot note" in (local / "MEMORY.md").read_text()
    assert store.sync("reviewer", local) == "none"


def test_a_newer_snapshot_is_offered_rather_than_applied(tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    local = tmp_path / "local" / "reviewer"
    _snapshot(tmp_path / "snapshots", "reviewer", "first")
    store.sync("reviewer", local)
    _snapshot(tmp_path / "snapshots", "reviewer", "second", when="2026-01-02T00:00:00Z")
    assert store.sync("reviewer", local) == "update"
    assert "first" in (local / "MEMORY.md").read_text()


def test_apply_the_snapshot_replaces_the_notes_it_had(tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    local = tmp_path / "local" / "reviewer"
    _snapshot(tmp_path / "snapshots", "reviewer", "first")
    store.sync("reviewer", local)
    _snapshot(tmp_path / "snapshots", "reviewer", "second", when="2026-01-02T00:00:00Z")
    assert store.apply("reviewer", local) == "applied"
    held = (local / "MEMORY.md").read_text()
    assert "second" in held and "first" not in held


def test_without_a_published_snapshot_there_is_nothing_to_sync(tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    (tmp_path / "snapshots" / "reviewer").mkdir(parents=True)
    assert store.sync("reviewer", tmp_path / "local" / "reviewer") == "none"
