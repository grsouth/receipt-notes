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
- `APP_HOST`: listening address; defaults to this machine's Tailscale IP
- `APP_PORT`: listening port; defaults to `8000`
- `PRINTER_TYPE`: `usb` or `network`; defaults to `network`
- `PRINTER_USB_VENDOR_ID`: USB vendor ID; defaults to Epson's `0x04b8`
- `PRINTER_USB_PRODUCT_ID`: optional USB product ID for exact device matching
- `PRINTER_HOST`: printer IP address when using `network`; editing works without it
- `APP_TIMEZONE`: scheduled-print timezone; defaults to `US/Mountain`
- `OLLAMA_URL`: local Ollama address; defaults to `http://127.0.0.1:11434`
- `OLLAMA_MODEL`: Beautify model; defaults to `gemma4:12b`
- `OLLAMA_TIMEOUT_SECONDS`: Beautify timeout; defaults to `90`

The app listens on this machine's Tailscale address at `http://100.71.126.126:8000`.

## Boot service

The production service uses Waitress, starts the print scheduler in the same process, and waits for Tailscale before binding. It runs as a systemd user service; lingering must be enabled so the user manager starts at boot. This machine already has lingering enabled for `grs`.

Install or update the service:

```bash
.venv/bin/pip install -r requirements.txt
install -Dm600 systemd/receipt-notes.env.example ~/.config/receipt-notes/environment
install -Dm644 systemd/receipt-notes.service ~/.config/systemd/user/receipt-notes.service
systemctl --user daemon-reload
systemctl --user enable --now receipt-notes.service
```

Edit `~/.config/receipt-notes/environment` when the Tailscale or printer IP changes. Check the service and follow its logs with:

```bash
systemctl --user status receipt-notes.service
journalctl --user -u receipt-notes.service -f
```

After pulling application updates, reinstall dependencies if needed and run `systemctl --user restart receipt-notes.service`.

### USB printer

This installation uses the TM-T70II's UB-U05 USB interface (`04b8:0202`). The service
environment selects it with `PRINTER_TYPE=usb`, `PRINTER_USB_VENDOR_ID=0x04b8`, and
`PRINTER_USB_PRODUCT_ID=0x0202`. The service user must have read/write permission for
that USB device, normally through a device-specific udev rule.

## Receipt format

Notes use a small receipt markup, not full Markdown:

```text
Plain text
* round bullet
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

Type `* ` at the start of a line or use the Bullet toolbar button. The button toggles the current line or every selected line. Pressing Enter continues bullet lists; pressing Enter on an empty bullet ends the list.

Checklist markers can be clicked or toggled with `Ctrl+Enter`/`Cmd+Enter`. Pressing Enter continues with a new unchecked item; pressing Enter again on an empty item ends the checklist.

## Local AI

Beautify uses Gemma 4 through a separate, local Ollama process. Install the model once, then keep Ollama running:

~~~bash
ollama pull gemma4:12b
ollama serve
~~~

Ollama must listen on this machine's loopback interface. Beautify works on the current editor contents, applies only supported receipt formatting, and never prints automatically. Use Undo Beautify immediately if needed; otherwise the result follows the normal autosave behavior.

## Notes

- The vault is the source of truth. The file tree creates, moves, and deletes real directories and `.md` files.
- Today and Tomorrow create or open dated notes under `daily/YYYY/MM Month/`.
- Named notes autosave shortly after content, formatting, or title changes stop. Save remains available for an immediate manual save.
- Saving is atomic. Printing saves first, sends native text and styles over the configured USB or TCP `9100` transport, then cuts.
- Saved notes can be scheduled to print once. Schedules are hidden sidecar files, require the app to remain running, and are consumed after one attempt.
- Schedules missed while the app is stopped expire when it starts again.
- Printer status refreshes every 60 seconds and can also be refreshed manually.

## Tests

```bash
pytest -q
```
