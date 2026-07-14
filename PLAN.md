# Minimal Receipt Notes App

Build a tiny Flask app with no database, accounts, API, print queue, or separate preview. Notes are hard-wrapped `.txt` files, so the editor content is exactly what reaches the printer.

## Implementation

- Store notes and folders directly beneath `VAULT_ROOT`, which is the sole source of truth.
- Show the live vault as a draggable directory tree beside a monospace 42-column editor calibrated to the TM-T70II's Font A.
- Show saved notes at their real location and unsaved notes at their proposed location.
- Let users edit only a note title; infer its directory from the tree and its `.txt` extension from the note type.
- Create directories and move notes or directories directly in the vault.
- Validate printable ASCII, hard-wrap at 42 characters, and protect the vault from unsafe paths.
- Print synchronously with `escpos.printer.Network`, explicitly select Font A, then call `cut()` and `close()`.
- Bind Flask to localhost and expose it only through Tailscale Serve.

## Assumptions

- Tailscale is the only access-control layer.
- The printer supports ESC/POS on TCP port 9100 and supports cutting.
- Search, history, rich formatting, and multiple printers are deferred.
