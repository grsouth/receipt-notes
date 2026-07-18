# Receipt Notes

A single-user Flask editor for receipt-sized notes. Notes are plain ASCII `.md` files in a filesystem vault and print to an Epson TM-T70II with native ESC/POS text. There is no database or authentication; access is intended to stay within a private tailnet.

## Run

```bash
cd /home/grs/Projects/receipt-notes
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Optional environment variables:

- `VAULT_ROOT`: notes directory; defaults to `vault/`
- `PRINTER_HOST`: printer IP address; editing works without it
- `APP_TIMEZONE`: scheduled-print timezone; defaults to `US/Mountain`
- `OLLAMA_URL`: local Ollama address; defaults to `http://127.0.0.1:11434`
- `OLLAMA_MODEL`: Beautify model; defaults to `gemma4:latest`
- `OLLAMA_TIMEOUT_SECONDS`: Beautify timeout; defaults to `90`

The app listens on this machine's Tailscale address at `http://100.71.126.126:8000`.

## Receipt format

Notes use a small receipt markup, not full Markdown:

```text
Plain text
- [ ] unfinished task
- [x] finished task
++underlined++
==reverse text==
---

::: center double-size
Large centered text
:::
```

Block directives may combine `font-b`, `center`, `right`, and `double-size`, but cannot nest. Defaults are Font A, normal size, and left alignment. In the toolbar, Large is Font A at 2× size, Medium is Font A, and Small is Font B.

Checklist markers can be clicked or toggled with `Ctrl+Enter`/`Cmd+Enter`. Pressing Enter continues with a new unchecked item; pressing Enter again on an empty item ends the checklist.

## Local AI

Beautify uses Gemma 4 through a separate, local Ollama process. Install the model once, then keep Ollama running:

~~~bash
ollama pull gemma4
ollama serve
~~~

Ollama must listen on this machine's loopback interface. Beautify works on the current editor contents, applies only supported receipt formatting, and never prints automatically. Use Undo Beautify immediately if needed; otherwise the result follows the normal autosave behavior.

## Notes

- The vault is the source of truth. The file tree creates, moves, and deletes real directories and `.md` files.
- Today and Tomorrow create or open dated notes under `daily/YYYY/MM Month/`.
- Named notes autosave shortly after content, formatting, or title changes stop. Save remains available for an immediate manual save.
- Saving is atomic. Printing saves first, sends native text and styles over TCP `9100`, then cuts.
- Saved notes can be scheduled to print once. Schedules are hidden sidecar files, require the app to remain running, and are consumed after one attempt.
- Schedules missed while the app is stopped expire when it starts again.
- Printer status refreshes every 60 seconds and can also be refreshed manually.

## Tests

```bash
pytest -q
```
