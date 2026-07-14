from pathlib import Path

import app as receipt_app


def configure_vault(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(receipt_app, "VAULT_ROOT", tmp_path.resolve())
    receipt_app.app.config.update(TESTING=True)


def test_wrap_note_at_42_columns():
    text = "a" * 43 + "\n\n" + "b" * 84
    assert receipt_app.wrap_note(text).splitlines() == [
        "a" * 42,
        "a",
        "",
        "b" * 42,
        "b" * 42,
    ]


def test_wrap_note_rejects_non_ascii_and_tabs():
    for text in ("café", "one\ttwo", "hello\x00"):
        try:
            receipt_app.wrap_note(text)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected invalid note text: {text!r}")


def test_receipt_styles_control_columns_and_document_header():
    font_b = receipt_app.receipt_style("b", "normal")
    double_width = receipt_app.receipt_style("a", "double-width")

    assert font_b["columns"] == 56
    assert double_width["columns"] == 21

    document = receipt_app.format_note_document("hello", font_b)
    assert document == "#!receipt-notes-v1 font=b width=1 height=1\nhello"
    parsed_style, content = receipt_app.parse_note_document(document)
    assert parsed_style["font"] == "b"
    assert parsed_style["size"] == "normal"
    assert content == "hello"

    default_style, plain_content = receipt_app.parse_note_document("plain text")
    assert default_style["font"] == "a"
    assert default_style["size"] == "normal"
    assert plain_content == "plain text"


def test_save_and_path_protection(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    client = receipt_app.app.test_client()

    response = client.post("/save", data={"path": "inbox/test.txt", "content": "x" * 43})
    assert response.status_code == 200
    assert (tmp_path / "inbox/test.txt").read_text(encoding="ascii") == "x" * 42 + "\nx"

    response = client.post("/save", data={"path": "../outside.txt", "content": "no"})
    assert response.status_code == 400
    assert not (tmp_path.parent / "outside.txt").exists()


def test_note_tree_includes_nested_and_empty_directories(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    (tmp_path / "Projects" / "Alpha").mkdir(parents=True)
    (tmp_path / "Projects" / "Alpha" / "todo.txt").write_text("hello", encoding="ascii")
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
                        {
                            "kind": "file",
                            "name": "todo",
                            "path": "Projects/Alpha/todo.txt",
                        }
                    ],
                }
            ],
        },
    ]

    response = receipt_app.app.test_client().get("/")
    assert response.status_code == 200
    assert b'aria-label="New note"' in response.data
    assert b'aria-label="New directory"' in response.data
    assert b">New note<" not in response.data
    assert b'type="button">New directory</button>' not in response.data
    assert b'data-tree-path="Projects"' in response.data
    assert b'todo.txt' in response.data


def test_create_directory(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "note.txt").write_text("still open", encoding="ascii")
    client = receipt_app.app.test_client()

    response = client.post(
        "/directory",
        data={
            "name": "Ideas",
            "parent": "Projects",
            "current_path": "Projects/note.txt",
        },
    )
    assert response.status_code == 200
    assert (tmp_path / "Projects" / "Ideas").is_dir()
    assert b'value="note"' in response.data
    assert b"still open" in response.data
    assert b'for="directory-name"' in response.data
    assert b"Directory path" not in response.data
    assert b'placeholder="projects/ideas"' not in response.data

    response = client.post(
        "/directory", data={"name": "../outside", "parent": ""}
    )
    assert response.status_code == 400
    assert not (tmp_path.parent / "outside").exists()


def test_new_note_shows_proposed_vault_location(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)

    response = receipt_app.app.test_client().get("/")

    assert response.status_code == 200
    assert b'value="untitled"' in response.data
    assert b'class="tree-file proposed"' in response.data
    assert b"untitled.txt" in response.data


def test_title_implies_directory_and_txt_extension(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    (tmp_path / "Inbox").mkdir()
    client = receipt_app.app.test_client()

    response = client.post(
        "/save",
        data={"title": "shopping", "directory": "Inbox", "content": "milk"},
    )

    assert response.status_code == 200
    assert (tmp_path / "Inbox" / "shopping.txt").read_text(encoding="ascii") == "milk"
    assert b'value="shopping"' in response.data
    assert response.data.rindex(b"</form>") < response.data.index(b"Saved.")


def test_nondefault_format_persists_in_note_and_is_hidden_in_editor(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    client = receipt_app.app.test_client()

    response = client.post(
        "/save",
        data={
            "path": "formatted.txt",
            "content": "formatted note",
            "font": "b",
            "size": "double-height",
        },
    )
    assert response.status_code == 200
    assert (tmp_path / "formatted.txt").read_text(encoding="ascii") == (
        "#!receipt-notes-v1 font=b width=1 height=2\nformatted note"
    )

    response = client.get("/?path=formatted.txt")
    assert response.status_code == 200
    assert b"#!receipt-notes-v1" not in response.data
    assert b"EPSON TM-T70II / FONT" not in response.data
    assert b"Printer disconnected" in response.data
    assert b'name="font" value="b" checked' in response.data
    assert b'name="size" value="double-height" checked' in response.data


def test_printer_connection_status(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    client = receipt_app.app.test_client()

    monkeypatch.delenv("PRINTER_HOST", raising=False)
    response = client.get("/printer-status")
    assert response.get_json() == {
        "connected": False,
        "label": "Printer disconnected",
    }

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
    assert response.get_json() == {
        "connected": True,
        "label": "Printer connected",
    }
    assert calls == [("connect", ("192.0.2.10", 9100), 0.4), ("close",)]


def test_changing_title_renames_note_without_duplicate(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    (tmp_path / "Inbox").mkdir()
    original = tmp_path / "Inbox" / "old.txt"
    original.write_text("old", encoding="ascii")

    response = receipt_app.app.test_client().post(
        "/save",
        data={
            "title": "new.txt",
            "directory": "Inbox",
            "original_path": "Inbox/old.txt",
            "content": "updated",
        },
    )

    assert response.status_code == 200
    assert not original.exists()
    assert (tmp_path / "Inbox" / "new.txt").read_text(encoding="ascii") == "updated"


def test_move_files_and_directories_in_vault(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    (tmp_path / "Inbox").mkdir()
    (tmp_path / "Inbox" / "note.txt").write_text("hello", encoding="ascii")
    (tmp_path / "Archive").mkdir()
    client = receipt_app.app.test_client()

    response = client.post(
        "/move", data={"source": "Inbox/note.txt", "destination": "Archive"}
    )
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "path": "Archive/note.txt"}
    assert (tmp_path / "Archive" / "note.txt").read_text(encoding="ascii") == "hello"

    response = client.post("/move", data={"source": "Inbox", "destination": "Archive"})
    assert response.status_code == 200
    assert (tmp_path / "Archive" / "Inbox").is_dir()

    response = client.post("/move", data={"source": "Archive", "destination": "Archive/Inbox"})
    assert response.status_code == 400
    assert response.get_json()["ok"] is False


def test_delete_files_and_nonempty_directories(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    (tmp_path / "note.txt").write_text("hello", encoding="ascii")
    (tmp_path / "Folder").mkdir()
    (tmp_path / "Folder" / "nested.txt").write_text("nested", encoding="ascii")
    client = receipt_app.app.test_client()

    response = client.post(
        "/delete", data={"path": "note.txt", "confirm": "delete"}
    )
    assert response.status_code == 200
    assert not (tmp_path / "note.txt").exists()

    response = client.post(
        "/delete", data={"path": "Folder", "confirm": "delete"}
    )
    assert response.status_code == 200
    assert not (tmp_path / "Folder").exists()

    response = client.post("/delete", data={"path": "../outside", "confirm": "delete"})
    assert response.status_code == 400


def test_print_uses_python_escpos_and_closes(monkeypatch, tmp_path):
    configure_vault(monkeypatch, tmp_path)
    monkeypatch.setenv("PRINTER_HOST", "192.0.2.10")
    calls = []

    class FakeNetwork:
        def __init__(self, host, profile):
            calls.append(("connect", host, profile))

        def set(self, align, font, custom_size, width, height):
            calls.append(("set", align, font, custom_size, width, height))

        def text(self, value):
            calls.append(("text", value))

        def cut(self):
            calls.append(("cut",))

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(receipt_app, "Network", FakeNetwork)
    response = receipt_app.app.test_client().post(
        "/print",
        data={
            "path": "test.txt",
            "content": "x" * 29,
            "font": "b",
            "size": "double-width",
        },
    )

    assert response.status_code == 200
    assert (tmp_path / "test.txt").read_text(encoding="ascii") == (
        "#!receipt-notes-v1 font=b width=2 height=1\n" + "x" * 28 + "\nx"
    )
    assert calls == [
        ("connect", "192.0.2.10", "default"),
        ("set", "left", "b", True, 2, 1),
        ("text", "x" * 28 + "\nx\n\n\n"),
        ("cut",),
        ("close",),
    ]
