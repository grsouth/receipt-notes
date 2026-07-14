import os
import re
import shutil
import socket
import tempfile
from pathlib import Path

from escpos.printer import Network
from flask import Flask, jsonify, render_template, request


FONT_METRICS = {
    "a": {"columns": 42, "cell_width": 12, "cell_height": 24},
    "b": {"columns": 56, "cell_width": 9, "cell_height": 17},
}
SIZE_PRESETS = {
    "normal": (1, 1, "Normal"),
    "double-width": (2, 1, "Double width"),
    "double-height": (1, 2, "Double height"),
    "double": (2, 2, "Double size"),
}
RECEIPT_HEADER = re.compile(
    r"#!receipt-notes-v1 font=([ab]) width=([12]) height=([12])"
)
DEFAULT_FONT = "a"
DEFAULT_SIZE = "normal"
RECEIPT_COLUMNS = FONT_METRICS[DEFAULT_FONT]["columns"]
PRINTER_PROFILE = "default"
PRINTER_PORT = 9100
DEFAULT_NOTE_PATH = "untitled.txt"
BASE_DIR = Path(__file__).resolve().parent
VAULT_ROOT = Path(os.environ.get("VAULT_ROOT", BASE_DIR / "vault")).expanduser().resolve()

app = Flask(__name__)


def printer_connection_status() -> dict:
    """Report whether the configured network receipt printer is reachable."""
    host = os.environ.get("PRINTER_HOST", "").strip()
    if not host:
        return {"connected": False, "label": "Printer disconnected"}
    try:
        connection = socket.create_connection((host, PRINTER_PORT), timeout=0.4)
        connection.close()
    except OSError:
        return {"connected": False, "label": "Printer disconnected"}
    return {"connected": True, "label": "Printer connected"}


def receipt_style(font: str = DEFAULT_FONT, size: str = DEFAULT_SIZE) -> dict:
    """Validate whole-note formatting and return its print and preview metrics."""
    font = font.strip().lower()
    size = size.strip().lower()
    if font not in FONT_METRICS:
        raise ValueError("Choose Font A or Font B.")
    if size not in SIZE_PRESETS:
        raise ValueError("Choose a supported receipt text size.")

    width, height, size_label = SIZE_PRESETS[size]
    metrics = FONT_METRICS[font]
    return {
        "font": font,
        "size": size,
        "size_label": size_label,
        "width": width,
        "height": height,
        "columns": metrics["columns"] // width,
        "cell_width": metrics["cell_width"],
        "cell_height": metrics["cell_height"],
    }


def receipt_style_from_dimensions(font: str, width: int, height: int) -> dict:
    """Convert persisted ESC/POS dimensions back to a supported preset."""
    for size, (candidate_width, candidate_height, _) in SIZE_PRESETS.items():
        if (width, height) == (candidate_width, candidate_height):
            return receipt_style(font, size)
    raise ValueError("The note contains an unsupported receipt text size.")


def submitted_receipt_style() -> dict:
    return receipt_style(
        request.form.get("font", DEFAULT_FONT),
        request.form.get("size", DEFAULT_SIZE),
    )


def parse_note_document(document: str) -> tuple[dict, str]:
    """Read an optional whole-note receipt header without exposing it as content."""
    first_line, separator, body = document.partition("\n")
    match = RECEIPT_HEADER.fullmatch(first_line)
    if not match:
        return receipt_style(), document
    style = receipt_style_from_dimensions(
        match.group(1), int(match.group(2)), int(match.group(3))
    )
    return style, body if separator else ""


def format_note_document(text: str, style: dict) -> str:
    """Persist non-default formatting in the note itself, avoiding sidecar state."""
    if style["font"] == DEFAULT_FONT and style["size"] == DEFAULT_SIZE:
        return text
    header = (
        f"#!receipt-notes-v1 font={style['font']} "
        f"width={style['width']} height={style['height']}"
    )
    return f"{header}\n{text}"


def wrap_note(text: str, columns: int = RECEIPT_COLUMNS) -> str:
    """Normalize newlines, validate printable ASCII, and hard-wrap each line."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    invalid = [char for char in text if char != "\n" and not 32 <= ord(char) <= 126]
    if invalid:
        raise ValueError("Notes may contain printable ASCII characters and newlines only.")

    wrapped_lines: list[str] = []
    for line in text.split("\n"):
        if not line:
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(
            line[start : start + columns] for start in range(0, len(line), columns)
        )
    return "\n".join(wrapped_lines)


def note_path(relative_path: str) -> Path:
    """Resolve a .txt path and guarantee that it stays inside VAULT_ROOT."""
    relative_path = relative_path.strip()
    if not relative_path:
        raise ValueError("Enter a note path, such as today.txt or projects/today.txt.")
    if "\\" in relative_path:
        raise ValueError("Use forward slashes in note paths.")

    relative = Path(relative_path)
    if relative.is_absolute() or relative.suffix.lower() != ".txt":
        raise ValueError("Note paths must be relative and end in .txt.")

    resolved = (VAULT_ROOT / relative).resolve()
    try:
        resolved.relative_to(VAULT_ROOT)
    except ValueError as exc:
        raise ValueError("The note path must stay inside the vault.") from exc
    return resolved


def title_path(title: str, directory: str = "") -> str:
    """Build an internal .txt path from a visible title and tree directory."""
    title = title.strip()
    if title.lower().endswith(".txt"):
        title = title[:-4].rstrip()
    if not title:
        raise ValueError("Enter a title.")
    if title in {".", ".."} or "/" in title or "\\" in title:
        raise ValueError("Titles cannot contain slashes.")
    if any(not 32 <= ord(char) <= 126 for char in title):
        raise ValueError("Titles may contain printable ASCII characters only.")

    directory = directory.strip().strip("/")
    if not directory:
        return f"{title}.txt"
    parent = directory_path(directory)
    if not parent.is_dir():
        raise ValueError("The note's directory no longer exists.")
    return (Path(directory) / f"{title}.txt").as_posix()


def submitted_note_path() -> str:
    """Read the current title form, with legacy path support for simple clients."""
    if "title" in request.form:
        return title_path(
            request.form.get("title", ""), request.form.get("directory", "")
        )
    return request.form.get("path", "")


def directory_path(relative_path: str) -> Path:
    """Resolve a directory path and guarantee that it stays inside VAULT_ROOT."""
    relative_path = relative_path.strip().strip("/")
    if not relative_path:
        raise ValueError("Enter a directory path, such as projects/ideas.")
    if "\\" in relative_path:
        raise ValueError("Use forward slashes in directory paths.")

    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Directory paths must stay inside the vault.")

    resolved = (VAULT_ROOT / relative).resolve()
    try:
        resolved.relative_to(VAULT_ROOT)
    except ValueError as exc:
        raise ValueError("Directory paths must stay inside the vault.") from exc
    return resolved


def directory_name_path(name: str, parent: str = "") -> tuple[str, Path]:
    """Build a directory location from its visible name and tree parent."""
    name = name.strip()
    if not name:
        raise ValueError("Enter a directory name.")
    if name in {".", ".."} or name.startswith(".") or "/" in name or "\\" in name:
        raise ValueError("Directory names cannot contain slashes or begin with a dot.")
    if any(not 32 <= ord(char) <= 126 for char in name):
        raise ValueError("Directory names may contain printable ASCII characters only.")

    parent = parent.strip().strip("/")
    if parent:
        parent_path = directory_path(parent)
        if not parent_path.is_dir():
            raise ValueError("The parent directory no longer exists.")
        relative_path = (Path(parent) / name).as_posix()
    else:
        parent_path = VAULT_ROOT
        relative_path = name
    return relative_path, parent_path / name


def page_for_current_note(
    current_path: str = "", message: str = "", error: str = ""
):
    """Keep the active note open after a separate tree action."""
    if current_path:
        try:
            current = note_path(current_path)
            if current.is_file():
                style, content = parse_note_document(current.read_text(encoding="ascii"))
                return page(current_path, content, message, error, style)
        except (OSError, UnicodeError, ValueError):
            pass
    return page(message=message, error=error)


def existing_item_path(relative_path: str) -> Path:
    """Resolve an existing note or directory inside VAULT_ROOT."""
    relative_path = relative_path.strip().strip("/")
    if not relative_path or "\\" in relative_path:
        raise ValueError("Choose a valid file or directory.")

    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("The item must stay inside the vault.")

    resolved = (VAULT_ROOT / relative).resolve()
    try:
        resolved.relative_to(VAULT_ROOT)
    except ValueError as exc:
        raise ValueError("The item must stay inside the vault.") from exc
    if not resolved.exists() or resolved.is_symlink():
        raise ValueError("That item no longer exists.")
    if resolved.is_file() and resolved.suffix.lower() != ".txt":
        raise ValueError("Only .txt notes can be moved.")
    return resolved


def save_note(
    relative_path: str,
    text: str,
    original_path: str = "",
    style: dict | None = None,
) -> str:
    style = style or receipt_style()
    wrapped = wrap_note(text, style["columns"])
    document = format_note_document(wrapped, style)
    destination = note_path(relative_path)
    source = note_path(original_path) if original_path else None

    if source is not None and source != destination:
        if not source.is_file():
            raise ValueError("The original note no longer exists.")
        if destination.exists():
            raise ValueError("A note with that title already exists in this directory.")
    elif source is None and destination.exists():
        raise ValueError("A note with that title already exists in this directory.")

    destination.parent.mkdir(parents=True, exist_ok=True)

    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="ascii", newline="\n", dir=destination.parent, delete=False
        ) as temporary:
            temporary.write(document)
            temporary_name = temporary.name
        os.replace(temporary_name, destination)
        if source is not None and source != destination:
            source.unlink()
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return wrapped


def build_note_tree(directory: Path | None = None, relative: Path | None = None) -> list[dict]:
    """Return the live vault directory as nested folders and .txt files."""
    VAULT_ROOT.mkdir(parents=True, exist_ok=True)
    directory = directory or VAULT_ROOT
    relative = relative or Path()

    try:
        entries = sorted(
            directory.iterdir(),
            key=lambda path: (not path.is_dir(), path.name.casefold()),
        )
    except OSError:
        return []

    tree: list[dict] = []
    for entry in entries:
        if entry.name.startswith(".") or entry.is_symlink():
            continue
        try:
            entry.resolve().relative_to(VAULT_ROOT)
        except (OSError, ValueError):
            continue

        item_path = relative / entry.name
        if entry.is_dir():
            tree.append(
                {
                    "kind": "folder",
                    "name": entry.name,
                    "path": item_path.as_posix(),
                    "children": build_note_tree(entry, item_path),
                }
            )
        elif entry.is_file() and entry.suffix.lower() == ".txt":
            tree.append(
                {
                    "kind": "file",
                    "name": entry.stem,
                    "path": item_path.as_posix(),
                }
            )
    return tree


def default_note_path() -> str:
    """Return an unused default path for a new note."""
    candidate = DEFAULT_NOTE_PATH
    counter = 2
    while note_path(candidate).exists():
        candidate = f"untitled-{counter}.txt"
        counter += 1
    return candidate


def add_proposed_note(tree: list[dict], relative_path: str) -> None:
    """Add an unsaved note and any missing parent folders to a rendered tree."""
    try:
        resolved = note_path(relative_path)
    except ValueError:
        return
    if resolved.exists():
        return

    normalized = Path(relative_path.strip()).as_posix()
    parts = Path(normalized).parts
    cursor = tree
    current_parts: list[str] = []
    for name in parts[:-1]:
        current_parts.append(name)
        current_path = "/".join(current_parts)
        folder = next(
            (item for item in cursor if item["kind"] == "folder" and item["name"] == name),
            None,
        )
        if folder is None:
            folder = {
                "kind": "folder",
                "name": name,
                "path": current_path,
                "children": [],
                "proposed": True,
            }
            cursor.append(folder)
            cursor.sort(key=lambda item: (item["kind"] != "folder", item["name"].casefold()))
        cursor = folder["children"]

    if not any(item["kind"] == "file" and item["name"] == parts[-1] for item in cursor):
        cursor.append(
            {
                "kind": "file",
                "name": Path(parts[-1]).stem,
                "path": normalized,
                "proposed": True,
            }
        )
        cursor.sort(key=lambda item: (item["kind"] != "folder", item["name"].casefold()))


def page(
    selected_path: str = "",
    content: str = "",
    message: str = "",
    error: str = "",
    style: dict | None = None,
):
    style = style or receipt_style()
    display_path = selected_path or default_note_path()
    try:
        is_saved = bool(selected_path) and note_path(selected_path).is_file()
    except ValueError:
        is_saved = False
    tree = build_note_tree()
    if not is_saved:
        add_proposed_note(tree, display_path)
    display = Path(display_path)
    note_directory = "" if display.parent == Path(".") else display.parent.as_posix()
    return render_template(
        "index.html",
        note_tree=tree,
        selected_path=display_path,
        is_saved=is_saved,
        default_note_path=display_path,
        note_title=display.stem,
        note_directory=note_directory,
        original_path=selected_path if is_saved else "",
        content=content,
        message=message,
        error=error,
        style=style,
        columns=style["columns"],
    )


@app.get("/")
def index():
    selected_path = request.args.get("path", "")
    if not selected_path:
        return page()
    try:
        path = note_path(selected_path)
        if not path.is_file():
            raise ValueError("That note does not exist.")
        style, content = parse_note_document(path.read_text(encoding="ascii"))
        return page(selected_path, content, style=style)
    except (OSError, UnicodeError, ValueError) as exc:
        return page(selected_path=selected_path, error=str(exc)), 400


@app.get("/printer-status")
def printer_status():
    return jsonify(printer_connection_status())


@app.post("/save")
def save():
    original_path = request.form.get("original_path", "")
    selected_path = original_path or request.form.get("path", "")
    content = request.form.get("content", "")
    style = receipt_style()
    try:
        style = submitted_receipt_style()
        selected_path = submitted_note_path()
        wrapped = save_note(selected_path, content, original_path, style)
        return page(selected_path, wrapped, "Saved.", style=style)
    except (OSError, ValueError) as exc:
        return page(selected_path, content, error=str(exc), style=style), 400


@app.post("/directory")
def create_directory():
    name = request.form.get("name", "")
    parent = request.form.get("parent", "")
    current_path = request.form.get("current_path", "")
    try:
        _, destination = directory_name_path(name, parent)
        destination.mkdir(exist_ok=False)
        return page_for_current_note(current_path, message=f"Created {name.strip()}.")
    except FileExistsError:
        return page_for_current_note(
            current_path, error="That directory already exists."
        ), 400
    except (OSError, ValueError) as exc:
        return page_for_current_note(current_path, error=str(exc)), 400


@app.post("/move")
def move_item():
    source_value = request.form.get("source", "")
    destination_value = request.form.get("destination", "").strip().strip("/")
    try:
        source = existing_item_path(source_value)
        destination = VAULT_ROOT if not destination_value else directory_path(destination_value)
        if not destination.is_dir():
            raise ValueError("The destination directory no longer exists.")
        if source.is_dir() and (destination == source or destination.is_relative_to(source)):
            raise ValueError("A directory cannot be moved inside itself.")

        target = destination / source.name
        if target == source:
            raise ValueError("The item is already in that directory.")
        if target.exists():
            raise ValueError(f"{target.name} already exists in that directory.")

        source.rename(target)
        moved_path = target.relative_to(VAULT_ROOT).as_posix()
        return jsonify({"ok": True, "path": moved_path})
    except (OSError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/delete")
def delete_item():
    relative_path = request.form.get("path", "")
    current_path = request.form.get("current_path", "")
    try:
        if request.form.get("confirm") != "delete":
            raise ValueError("Deletion was not confirmed.")
        target = existing_item_path(relative_path)
        is_directory = target.is_dir()
        display_name = target.name if is_directory else target.stem
        if is_directory:
            shutil.rmtree(target)
        else:
            target.unlink()
        message = f"Deleted {display_name}."
    except (OSError, ValueError) as exc:
        return page(error=str(exc)), 400

    current_was_deleted = current_path == relative_path or current_path.startswith(
        f"{relative_path}/"
    )
    if current_path and not current_was_deleted:
        try:
            current = note_path(current_path)
            if current.is_file():
                style, content = parse_note_document(current.read_text(encoding="ascii"))
                return page(current_path, content, message, style=style)
        except (OSError, UnicodeError, ValueError):
            pass
    return page(message=message)


@app.post("/print")
def print_note():
    original_path = request.form.get("original_path", "")
    selected_path = original_path or request.form.get("path", "")
    content = request.form.get("content", "")
    style = receipt_style()
    try:
        style = submitted_receipt_style()
        selected_path = submitted_note_path()
        wrapped = save_note(selected_path, content, original_path, style)
    except (OSError, ValueError) as exc:
        return page(selected_path, content, error=str(exc), style=style), 400

    printer_host = os.environ.get("PRINTER_HOST", "").strip()
    if not printer_host:
        return page(
            selected_path,
            wrapped,
            error="PRINTER_HOST is not configured.",
            style=style,
        ), 503

    printer = None
    try:
        printer = Network(printer_host, profile=PRINTER_PROFILE)
        printer.set(
            align="left",
            font=style["font"],
            custom_size=True,
            width=style["width"],
            height=style["height"],
        )
        printer.text(wrapped + "\n\n\n")
        printer.cut()
    except Exception as exc:  # python-escpos wraps several socket/device exceptions
        return page(
            selected_path,
            wrapped,
            error=f"Saved, but printing failed: {exc}",
            style=style,
        ), 503
    finally:
        if printer is not None:
            try:
                printer.close()
            except Exception:
                pass
    return page(selected_path, wrapped, "Saved and printed.", style=style)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
