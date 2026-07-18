import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import app as receipt_app
from receipt_markdown import editor_document, layout_document, normalize_source


def configure_vault(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(receipt_app, "VAULT_ROOT", tmp_path.resolve())
    receipt_app.app.config.update(TESTING=True)


def test_receipt_markdown_mixed_styles_and_wrapping():
    source = "::: double-size\nBIG\n:::\nplain ++under++ ==reverse=="
    lines = layout_document(source)

    assert [run.text for run in lines[0].runs] == ["BIG"]
    assert (lines[0].runs[0].style.width, lines[0].runs[0].style.height) == (2, 2)
    assert [
        (
            run.text,
            run.style.font,
            run.style.underline,
            run.style.invert,
        )
        for run in lines[1].runs
    ] == [
        ("plain ", "a", 0, False),
        ("under", "a", 1, False),
        (" ", "a", 0, False),
        ("reverse", "a", 0, True),
    ]

    wrapped = layout_document("x" * 43)
    assert ["".join(run.text for run in line.runs) for line in wrapped] == ["x" * 42, "x"]

    checklist = layout_document("- [ ] open\n- [x] done")
    assert ["".join(run.text for run in line.runs) for line in checklist] == [
        "- [ ] open",
        "- [x] done",
    ]


def test_receipt_markdown_directives_and_validation():
    lines = layout_document("::: center font-b double-size\nsmall and large\n:::")
    assert lines[0].align == "center"
    assert lines[0].runs[0].style.font == "b"
    assert lines[0].runs[0].style.width == 2
    assert lines[0].runs[0].style.height == 2

    right = layout_document("::: right\naligned\n:::")
    assert right[0].align == "right"

    for text in ("café", "one\ttwo", "hello\x00"):
        try:
            normalize_source(text)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected invalid note text: {text!r}")

    for text in (
        "::: unknown\ntext\n:::",
        "::: double-width\ntext\n:::",
        "::: center\ntext",
    ):
        try:
            layout_document(text)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected invalid receipt Markdown: {text!r}")


def test_editor_document_keeps_supported_semantics_without_markers():
    document = editor_document(
        "::: center double-size\n++Big++ ==reverse==\n:::\n"
        "::: font-b\nsmall\n:::\n---"
    )

    large, small, rule = document["blocks"]
    assert large["kind"] == "paragraph"
    assert large["align"] == "center"
    assert (large["style"]["width"], large["style"]["height"]) == (2, 2)
    assert [run["text"] for run in large["runs"]] == ["Big", " ", "reverse"]
    assert large["runs"][0]["underline"] == 1
    assert large["runs"][2]["invert"] is True
    assert small["kind"] == "paragraph"
    assert small["style"]["font"] == "b"
    assert small["runs"][0]["text"] == "small"
    assert rule["kind"] == "rule"


def beautify_text_block(
    source_ids,
    text,
    *,
    size="medium",
    align="left",
    underline=False,
    reverse=False,
):
    return {
        "kind": "text",
        "source_ids": source_ids,
        "size": size,
        "align": align,
        "runs": [
            {
                "text": text,
                "underline": underline,
                "reverse": reverse,
            }
        ],
    }


def beautify_decoration(kind):
    return {
        "kind": kind,
        "source_ids": [],
        "size": "medium",
        "align": "left",
        "runs": [],
    }


def test_beautify_api_formats_unsaved_content_without_writing(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    model_response = {
        "blocks": [
            beautify_text_block(
                [0], "Raw Title", size="large", align="center", underline=True
            ),
            beautify_decoration("rule"),
            beautify_text_block([1], "- First item"),
            beautify_text_block(
                [2], "TOTAL: 2", size="small", align="right", reverse=True
            ),
        ]
    }
    monkeypatch.setattr(
        receipt_app,
        "request_ollama",
        lambda _blocks, _feedback="": json.dumps(model_response),
    )

    response = receipt_app.app.test_client().post(
        "/beautify",
        json={"content": "raw title\n- first item\nTotal 2"},
    )

    assert response.status_code == 200
    result = response.get_json()
    assert result["content"] == (
        "::: center double-size\n"
        "++Raw Title++\n"
        ":::\n"
        "---\n"
        "- First item\n"
        "::: right font-b\n"
        "==TOTAL: 2==\n"
        ":::"
    )
    large, rule, item, total = result["editor_state"]["blocks"]
    assert large["align"] == "center"
    assert (large["style"]["width"], large["style"]["height"]) == (2, 2)
    assert large["runs"][0]["underline"] == 1
    assert rule["kind"] == "rule"
    assert item["runs"][0]["text"] == "- First item"
    assert total["align"] == "right"
    assert total["style"]["font"] == "b"
    assert total["runs"][0]["invert"] is True
    assert list(tmp_path.iterdir()) == []


def test_beautify_retries_invalid_output_and_allows_reshape(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    responses = [
        {"blocks": [beautify_text_block([0], "One")]},
        {
            "blocks": [
                beautify_text_block([0, 1], "One and"),
                beautify_text_block([1], "two."),
            ]
        },
    ]
    feedback = []

    def fake_request(_blocks, retry_feedback=""):
        feedback.append(retry_feedback)
        return json.dumps(responses.pop(0))

    monkeypatch.setattr(receipt_app, "request_ollama", fake_request)
    response = receipt_app.app.test_client().post(
        "/beautify", json={"content": "one\ntwo"}
    )

    assert response.status_code == 200
    assert response.get_json()["content"] == "One and\ntwo."
    assert feedback[0] == ""
    assert "missing" in feedback[1].lower()


def test_beautify_preserves_checklist_state_and_rejects_bad_styles():
    input_blocks, source_texts = receipt_app.beautify_input_blocks("- [ ] open")
    changed_checklist = {
        "blocks": [beautify_text_block([0], "- [x] open")]
    }
    with pytest.raises(receipt_app.BeautifyOutputError, match="Checklist state"):
        receipt_app.validate_beautify_output(
            changed_checklist, source_texts, len(input_blocks)
        )

    bad_style = {"blocks": [beautify_text_block([0], "- [ ] open")]}
    bad_style["blocks"][0]["size"] = "enormous"
    with pytest.raises(receipt_app.BeautifyOutputError, match="unsupported style"):
        receipt_app.validate_beautify_output(
            bad_style, source_texts, len(input_blocks)
        )


def test_beautify_normalizes_model_empty_text_to_a_spacer():
    input_blocks, source_texts = receipt_app.beautify_input_blocks("Heading\nBody")
    model_document = {
        "blocks": [
            beautify_text_block([0], "Heading", size="large", align="center"),
            {
                "kind": "text",
                "source_ids": [],
                "size": "medium",
                "align": "left",
                "runs": [{"text": "", "underline": False, "reverse": False}],
            },
            beautify_text_block([1], "Body"),
        ]
    }

    source = receipt_app.validate_beautify_output(
        model_document, source_texts, len(input_blocks)
    )

    assert source == "::: center double-size\nHeading\n:::\n\nBody"


def test_beautify_errors_are_safe_and_concurrent_calls_are_rejected(
    monkeypatch, tmp_path
):
    configure_vault(monkeypatch, tmp_path)
    client = receipt_app.app.test_client()

    calls = []

    def malformed(_blocks, feedback=""):
        calls.append(feedback)
        return "not json"

    monkeypatch.setattr(receipt_app, "request_ollama", malformed)
    invalid = client.post("/beautify", json={"content": "hello"})
    assert invalid.status_code == 502
    assert len(calls) == 2
    assert list(tmp_path.iterdir()) == []

    def unavailable(_blocks, _feedback=""):
        raise receipt_app.BeautifyUnavailable("offline")

    monkeypatch.setattr(receipt_app, "request_ollama", unavailable)
    offline = client.post("/beautify", json={"content": "hello"})
    assert offline.status_code == 503
    assert "ollama serve" in offline.get_json()["error"]

    empty = client.post("/beautify", json={"content": "\n---\n"})
    assert empty.status_code == 400

    receipt_app.BEAUTIFY_LOCK.acquire()
    try:
        busy = client.post("/beautify", json={"content": "hello"})
    finally:
        receipt_app.BEAUTIFY_LOCK.release()
    assert busy.status_code == 409


def test_request_ollama_uses_local_structured_chat(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {"message": {"content": '{"blocks": []}'}}
            ).encode("utf-8")

    def fake_urlopen(api_request, timeout):
        captured["url"] = api_request.full_url
        captured["payload"] = json.loads(api_request.data)
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(receipt_app, "urlopen", fake_urlopen)

    content = receipt_app.request_ollama([{"kind": "text", "source_id": 0}])

    assert content == '{"blocks": []}'
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["timeout"] == 90
    assert captured["payload"]["model"] == "gemma4:latest"
    assert captured["payload"]["format"] == receipt_app.BEAUTIFY_SCHEMA
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["think"] is False
    assert captured["payload"]["keep_alive"] == "10m"
    assert captured["payload"]["options"]["temperature"] == 0


def test_save_preserves_raw_markdown_and_protects_paths(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    (tmp_path / "inbox").mkdir()
    client = receipt_app.app.test_client()
    unsaved_page = client.get("/")
    assert b'id="schedule-toggle"' in unsaved_page.data
    assert b"Save first" in unsaved_page.data
    source = "++Heading++\n\n" + "x" * 43

    response = client.post(
        "/save",
        data={"title": "test", "directory": "inbox", "content": source},
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/?path=inbox/test.md&saved=1"
    assert (tmp_path / "inbox/test.md").read_text(encoding="ascii") == source

    response = client.post(
        "/save", data={"title": "../outside", "directory": "", "content": "no"}
    )
    assert response.status_code == 400
    assert not (tmp_path.parent / "outside.md").exists()


def test_note_tree_includes_nested_and_empty_directories(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    (tmp_path / "Projects" / "Alpha").mkdir(parents=True)
    (tmp_path / "Projects" / "Alpha" / "todo.md").write_text("hello", encoding="ascii")
    (tmp_path / "Archive").mkdir()

    tree = receipt_app.build_note_tree()
    assert tree == [
        {"kind": "folder", "name": "Archive", "path": "Archive", "children": []},
        {
            "kind": "folder",
            "name": "Projects",
            "path": "Projects",
            "children": [
                {
                    "kind": "folder",
                    "name": "Alpha",
                    "path": "Projects/Alpha",
                    "children": [
                        {"kind": "file", "name": "todo", "path": "Projects/Alpha/todo.md"}
                    ],
                }
            ],
        },
    ]

    response = receipt_app.app.test_client().get("/")
    assert response.status_code == 200
    assert b'aria-label="New note"' in response.data
    assert b'aria-label="New directory"' in response.data
    assert b'id="mobile-files-button"' in response.data
    assert b'aria-controls="file-sidebar"' in response.data
    assert b'id="receipt-scale-frame"' in response.data
    assert b'id="beautify-button"' in response.data
    assert b'id="undo-beautify"' in response.data
    assert b'id="beautify-overlay"' in response.data
    assert response.data.index(b'id="receipt-editor"') < response.data.index(b'id="beautify-button"')
    assert b"Beautifying receipt" in response.data
    assert b'data-tree-path="Projects"' in response.data
    assert b'data-touch-drag-path="Projects"' in response.data
    assert b'data-touch-drag-path="Projects/Alpha/todo.md"' in response.data
    assert b'todo.md' in response.data


def test_create_directory_keeps_current_note_open(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "note.md").write_text("still **open**", encoding="ascii")
    client = receipt_app.app.test_client()

    response = client.post(
        "/directory",
        data={"name": "Ideas", "parent": "Projects", "current_path": "Projects/note.md"},
    )
    assert response.status_code == 200
    assert (tmp_path / "Projects" / "Ideas").is_dir()
    assert b'value="note"' in response.data
    assert b"still **open**" in response.data
    assert b"Directory path" not in response.data


def test_new_note_and_title_imply_md_extension(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    client = receipt_app.app.test_client()

    response = client.get("/")
    assert response.status_code == 200
    assert b'value="untitled"' in response.data
    assert b'class="tree-file proposed"' in response.data
    assert b"untitled.md" in response.data

    (tmp_path / "Inbox").mkdir()
    response = client.post(
        "/save",
        data={"title": "shopping", "directory": "Inbox", "content": "- milk"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert response.request.path == "/"
    assert response.request.args["path"] == "Inbox/shopping.md"
    assert response.request.args["saved"] == "1"
    assert (tmp_path / "Inbox" / "shopping.md").read_text(encoding="ascii") == "- milk"
    assert response.data.rindex(b"</form>") < response.data.index(b"Saved.")


def test_today_and_tomorrow_create_and_reopen_dated_notes(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    monkeypatch.setattr(receipt_app, "local_date", lambda: date(2026, 12, 31))
    client = receipt_app.app.test_client()

    today = client.post("/daily", data={"offset": "0"}, follow_redirects=True)
    assert today.status_code == 200
    assert today.request.args["path"] == "daily/2026/12 December/2026-12-31.md"
    today_path = tmp_path / "daily/2026/12 December/2026-12-31.md"
    assert today_path.read_text(encoding="ascii") == "Thursday, December 31, 2026\n\n"

    tomorrow = client.post("/daily", data={"offset": "1"}, follow_redirects=True)
    assert tomorrow.status_code == 200
    assert tomorrow.request.args["path"] == "daily/2027/01 January/2027-01-01.md"
    tomorrow_path = tmp_path / "daily/2027/01 January/2027-01-01.md"
    assert tomorrow_path.read_text(encoding="ascii") == "Friday, January 1, 2027\n\n"

    today_path.write_text("keep this", encoding="ascii")
    reopened = client.post("/daily", data={"offset": "0"}, follow_redirects=True)
    assert reopened.status_code == 200
    assert today_path.read_text(encoding="ascii") == "keep this"


def test_printer_connection_status(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    client = receipt_app.app.test_client()

    monkeypatch.delenv("PRINTER_HOST", raising=False)
    response = client.get("/printer-status")
    assert response.get_json() == {"connected": False, "label": "Printer disconnected"}

    calls = []

    class FakeConnection:
        def close(self):
            calls.append(("close",))

    def connect(address, timeout):
        calls.append(("connect", address, timeout))
        return FakeConnection()

    monkeypatch.setenv("PRINTER_HOST", "192.0.2.10")
    monkeypatch.setattr(receipt_app.socket, "create_connection", connect)
    response = client.get("/printer-status")
    assert response.get_json() == {"connected": True, "label": "Printer connected"}
    assert calls == [("connect", ("192.0.2.10", 9100), 0.4), ("close",)]


def test_changing_title_renames_markdown_note(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    (tmp_path / "Inbox").mkdir()
    original = tmp_path / "Inbox" / "old.md"
    original.write_text("old", encoding="ascii")

    response = receipt_app.app.test_client().post(
        "/save",
        data={
            "title": "new.md",
            "directory": "Inbox",
            "original_path": "Inbox/old.md",
            "content": "updated",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/?path=Inbox/new.md&saved=1"
    assert not original.exists()
    assert (tmp_path / "Inbox" / "new.md").read_text(encoding="ascii") == "updated"


def test_autosave_creates_updates_and_renames_note(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    (tmp_path / "Inbox").mkdir()
    client = receipt_app.app.test_client()

    created = client.post(
        "/autosave",
        data={
            "title": "named",
            "directory": "Inbox",
            "original_path": "",
            "content": "first",
        },
    )
    assert created.status_code == 200
    assert created.get_json() == {"saved": True, "path": "Inbox/named.md"}
    original = tmp_path / "Inbox" / "named.md"
    assert original.read_text(encoding="ascii") == "first"

    updated = client.post(
        "/autosave",
        data={
            "title": "named",
            "directory": "Inbox",
            "original_path": "Inbox/named.md",
            "content": "second",
        },
    )
    assert updated.status_code == 200
    assert original.read_text(encoding="ascii") == "second"

    scheduled_at = datetime(2026, 7, 20, 7, 0, tzinfo=ZoneInfo("US/Mountain"))
    receipt_app.write_schedule(original, scheduled_at)
    renamed = client.post(
        "/autosave",
        data={
            "title": "renamed",
            "directory": "Inbox",
            "original_path": "Inbox/named.md",
            "content": "third",
        },
    )
    destination = tmp_path / "Inbox" / "renamed.md"
    assert renamed.status_code == 200
    assert renamed.get_json() == {"saved": True, "path": "Inbox/renamed.md"}
    assert not original.exists()
    assert destination.read_text(encoding="ascii") == "third"
    assert receipt_app.schedule_path(destination).exists()


def test_autosave_returns_json_errors_without_changing_files(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    response = receipt_app.app.test_client().post(
        "/autosave",
        data={
            "title": "bad/name",
            "directory": "",
            "original_path": "",
            "content": "text",
        },
    )

    assert response.status_code == 400
    assert "error" in response.get_json()
    assert list(tmp_path.iterdir()) == []


def test_schedule_api_creates_updates_and_cancels_immediately(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    now = datetime(2026, 7, 18, 20, 0, tzinfo=ZoneInfo("US/Mountain"))
    monkeypatch.setattr(receipt_app, "server_now", lambda: now)
    note = tmp_path / "morning.md"
    note.write_text("todo", encoding="ascii")
    client = receipt_app.app.test_client()

    response = client.post(
        "/schedule",
        data={"path": "morning.md", "scheduled_at": "2026-07-19T07:00"},
    )
    assert response.status_code == 200
    assert response.get_json() == {
        "scheduled": True,
        "scheduled_at": "2026-07-19T07:00",
        "timezone": "US/Mountain",
    }
    sidecar = receipt_app.schedule_path(note)
    assert json.loads(sidecar.read_text(encoding="ascii")) == {
        "scheduled_at": "2026-07-19T07:00:00-06:00"
    }
    assert client.get("/schedule-status?path=morning.md").get_json() == response.get_json()
    scheduled_page = client.get("/?path=morning.md")
    assert b'aria-pressed="true"' in scheduled_page.data
    assert b'value="2026-07-19T07:00"' in scheduled_page.data
    assert b"Mountain time" in scheduled_page.data

    updated = client.post(
        "/schedule",
        data={"path": "morning.md", "scheduled_at": "2026-07-19T08:30"},
    )
    assert updated.get_json()["scheduled_at"] == "2026-07-19T08:30"

    cancelled = client.post(
        "/schedule", data={"path": "morning.md", "scheduled_at": ""}
    )
    assert cancelled.get_json() == {
        "scheduled": False,
        "scheduled_at": None,
        "timezone": "US/Mountain",
    }
    assert not sidecar.exists()

    past = client.post(
        "/schedule",
        data={"path": "morning.md", "scheduled_at": "2026-07-18T19:59"},
    )
    assert past.status_code == 400
    assert past.get_json() == {"error": "Choose a future date and time."}
    unsaved = client.post(
        "/schedule",
        data={"path": "missing.md", "scheduled_at": "2026-07-19T07:00"},
    )
    assert unsaved.status_code == 400
    assert unsaved.get_json() == {"error": "Save the note before scheduling it."}


def test_due_schedule_attempts_latest_saved_note_once(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    due = datetime(2026, 7, 19, 7, 0, tzinfo=ZoneInfo("US/Mountain"))
    note = tmp_path / "todo.md"
    note.write_text("old", encoding="ascii")
    receipt_app.write_schedule(note, due)
    note.write_text("latest saved contents", encoding="ascii")
    calls = []
    monkeypatch.setattr(receipt_app, "send_to_printer", calls.append)

    receipt_app.process_due_schedules(due)
    receipt_app.process_due_schedules(due + receipt_app.timedelta(minutes=1))

    assert calls == ["latest saved contents"]
    assert not receipt_app.schedule_path(note).exists()
    assert not receipt_app.schedule_path(note, claimed=True).exists()


def test_schedule_failure_and_startup_expiration_never_retry(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    started = datetime(2026, 7, 19, 7, 0, tzinfo=ZoneInfo("US/Mountain"))

    failed = tmp_path / "failed.md"
    failed.write_text("fail once", encoding="ascii")
    receipt_app.write_schedule(failed, started + receipt_app.timedelta(minutes=1))

    def fail_print(_source):
        raise OSError("offline")

    monkeypatch.setattr(receipt_app, "send_to_printer", fail_print)
    receipt_app.process_due_schedules(started + receipt_app.timedelta(minutes=1))
    receipt_app.process_due_schedules(started + receipt_app.timedelta(minutes=2))
    assert not receipt_app.schedule_path(failed).exists()
    assert not receipt_app.schedule_path(failed, claimed=True).exists()

    overdue = tmp_path / "overdue.md"
    overdue.write_text("too late", encoding="ascii")
    receipt_app.write_schedule(overdue, started - receipt_app.timedelta(minutes=1))
    future = tmp_path / "future.md"
    future.write_text("later", encoding="ascii")
    receipt_app.write_schedule(future, started + receipt_app.timedelta(hours=1))
    claimed_note = tmp_path / "claimed.md"
    claimed_note.write_text("uncertain", encoding="ascii")
    receipt_app.write_schedule(claimed_note, started - receipt_app.timedelta(minutes=2))
    receipt_app.schedule_path(claimed_note).rename(
        receipt_app.schedule_path(claimed_note, claimed=True)
    )

    receipt_app.expire_startup_schedules(started)
    assert not receipt_app.schedule_path(overdue).exists()
    assert receipt_app.schedule_path(future).exists()
    assert not receipt_app.schedule_path(claimed_note, claimed=True).exists()


def test_schedule_follows_note_lifecycle(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    scheduled_at = datetime(2026, 7, 20, 7, 0, tzinfo=ZoneInfo("US/Mountain"))
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    original = inbox / "old.md"
    original.write_text("scheduled", encoding="ascii")
    receipt_app.write_schedule(original, scheduled_at)
    client = receipt_app.app.test_client()

    renamed = client.post(
        "/save",
        data={
            "title": "new",
            "directory": "Inbox",
            "original_path": "Inbox/old.md",
            "content": "updated",
        },
    )
    renamed_note = inbox / "new.md"
    assert renamed.status_code == 302
    assert not receipt_app.schedule_path(original).exists()
    assert receipt_app.schedule_path(renamed_note).exists()

    archive = tmp_path / "Archive"
    archive.mkdir()
    moved = client.post(
        "/move", data={"source": "Inbox/new.md", "destination": "Archive"}
    )
    moved_note = archive / "new.md"
    assert moved.status_code == 200
    assert receipt_app.schedule_path(moved_note).exists()

    deleted = client.post(
        "/delete", data={"path": "Archive/new.md", "confirm": "delete"}
    )
    assert deleted.status_code == 200
    assert not receipt_app.schedule_path(moved_note).exists()

    folder = tmp_path / "Folder"
    folder.mkdir()
    nested = folder / "nested.md"
    nested.write_text("nested", encoding="ascii")
    receipt_app.write_schedule(nested, scheduled_at)
    moved_folder = client.post(
        "/move", data={"source": "Folder", "destination": "Archive"}
    )
    assert moved_folder.status_code == 200
    moved_nested = archive / "Folder" / "nested.md"
    assert receipt_app.schedule_path(moved_nested).exists()
    deleted_folder = client.post(
        "/delete", data={"path": "Archive/Folder", "confirm": "delete"}
    )
    assert deleted_folder.status_code == 200
    assert not receipt_app.schedule_path(moved_nested).exists()


def test_move_and_delete_markdown_notes(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    (tmp_path / "Inbox").mkdir()
    (tmp_path / "Inbox" / "note.md").write_text("hello", encoding="ascii")
    (tmp_path / "Archive").mkdir()
    client = receipt_app.app.test_client()

    response = client.post(
        "/move", data={"source": "Inbox/note.md", "destination": "Archive"}
    )
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "path": "Archive/note.md"}
    assert (tmp_path / "Archive" / "note.md").read_text(encoding="ascii") == "hello"

    response = client.post(
        "/delete", data={"path": "Archive/note.md", "confirm": "delete"}
    )
    assert response.status_code == 200
    assert not (tmp_path / "Archive" / "note.md").exists()

    (tmp_path / "Folder").mkdir()
    (tmp_path / "Folder" / "nested.md").write_text("nested", encoding="ascii")
    response = client.post("/delete", data={"path": "Folder", "confirm": "delete"})
    assert response.status_code == 200
    assert not (tmp_path / "Folder").exists()


def test_print_uses_mixed_native_escpos_styles_and_closes(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    monkeypatch.setenv("PRINTER_HOST", "192.0.2.10")
    calls = []

    class FakeNetwork:
        def __init__(self, host, profile):
            calls.append(("connect", host, profile))

        def set(self, **settings):
            calls.append(("set", settings))

        def text(self, value):
            calls.append(("text", value))

        def cut(self, feed=True):
            calls.append(("cut", feed))

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(receipt_app, "Network", FakeNetwork)
    source = "plain ++under++ ==reverse=="
    note = tmp_path / "test.md"
    note.write_text("old", encoding="ascii")
    receipt_app.write_schedule(
        note, datetime(2026, 7, 20, 7, 0, tzinfo=ZoneInfo("US/Mountain"))
    )
    response = receipt_app.app.test_client().post(
        "/print",
        data={
            "title": "test",
            "directory": "",
            "original_path": "test.md",
            "content": source,
        },
    )

    assert response.status_code == 200
    assert (tmp_path / "test.md").read_text(encoding="ascii") == source
    set_calls = [call[1] for call in calls if call[0] == "set"]
    assert [
        (
            call["font"],
            call["underline"],
            call["invert"],
            call["width"],
            call["height"],
        )
        for call in set_calls
    ] == [
        ("a", 0, False, 1, 1),
        ("a", 1, False, 1, 1),
        ("a", 0, False, 1, 1),
        ("a", 0, True, 1, 1),
    ]
    assert calls[1] == ("text", "\n")
    assert ("cut", False) in calls
    assert calls[-1] == ("close",)
    assert receipt_app.schedule_path(note).exists()
