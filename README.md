# Receipt Notes

A minimal, single-user Flask app for saving 42-column text notes and printing them to a network Epson TM-T70II receipt printer.

## Setup

```bash
cd /home/grs/Projects/receipt-notes
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set the printer address and choose the directory that acts as the vault:

```bash
export PRINTER_HOST=192.168.1.100
export VAULT_ROOT=/home/grs/Projects/receipt-notes/vault
python app.py
```

The app listens only on `127.0.0.1:8000`. In another terminal, expose it privately to your tailnet:

```bash
tailscale serve --bg localhost:8000
```

Use `tailscale serve status` to see its tailnet URL.

## Notes

- `VAULT_ROOT` is the source of truth. The sidebar is rebuilt from its real directories and `.txt` files on every page load.
- New notes default to the title `untitled` at the vault root. The tree carries the directory location and the app adds the `.txt` extension internally.
- Changing a saved note's title renames its source file without changing its tree directory.
- Dragging files or directories in the tree moves the underlying filesystem item.
- Delete controls in the tree remove notes or entire folders after an explicit confirmation.
- Notes accept printable ASCII and newlines and are permanently wrapped at 42 columns, matching the TM-T70II's standard Font A on 80 mm paper.
- **Save & Print** saves first, then sends the exact saved text through `python-escpos` and cuts the receipt.
- Printing explicitly selects the default ESC/POS capability profile and standard-size, left-aligned Font A.
- The printer is expected to accept ESC/POS over its default network port, 9100.

Run the tests with:

```bash
pytest -q
```
