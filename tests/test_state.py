from __future__ import annotations

import json

from todoist_sync import state


def test_load_state_missing_file_returns_empty(tmp_path):
    pairs, archive = state.load_state(tmp_path / "state.json")
    assert pairs == []
    assert archive == []


def test_load_state_reads_legacy_bare_list_as_all_active(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps([{"reminder_id": "r1", "task_id": "t1", "due": None, "all_day": False}]))

    pairs, archive = state.load_state(path)

    assert pairs == [{"reminder_id": "r1", "task_id": "t1", "due": None, "all_day": False}]
    assert archive == []


def test_save_then_load_round_trips_pairs_and_archive(tmp_path):
    path = tmp_path / "state.json"
    pairs = [{"reminder_id": "r1", "task_id": "t1"}]
    archive = [{"reminder_id": "r2", "task_id": "t2"}]

    state.save_state(path, pairs, archive)
    loaded_pairs, loaded_archive = state.load_state(path)

    assert loaded_pairs == pairs
    assert loaded_archive == archive


def test_save_state_writes_pairs_before_archive_in_file(tmp_path):
    path = tmp_path / "state.json"

    state.save_state(path, [{"reminder_id": "r1", "task_id": "t1"}], [{"reminder_id": "r2", "task_id": "t2"}])

    raw = path.read_text()
    assert raw.index('"pairs"') < raw.index('"archive"')


def test_save_state_sets_owner_only_permissions(tmp_path):
    path = tmp_path / "state.json"

    state.save_state(path, [], [])

    assert (path.stat().st_mode & 0o777) == 0o600


def test_save_state_is_atomic_no_partial_file_left_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"pairs": [{"reminder_id": "keep", "task_id": "keep"}], "archive": []}))

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated crash mid-write")

    monkeypatch.setattr(json, "dumps", boom)
    try:
        state.save_state(path, [{"reminder_id": "new", "task_id": "new"}], [])
    except RuntimeError:
        pass

    # The original file must be untouched, and no leftover temp file.
    pairs, _ = state.load_state(path)
    assert pairs == [{"reminder_id": "keep", "task_id": "keep"}]
    assert list(tmp_path.iterdir()) == [path]
