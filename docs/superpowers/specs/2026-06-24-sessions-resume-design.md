# Directory-scoped sessions with `--resume`

**Date:** 2026-06-24
**Status:** Approved (design)

## Goal

Add Claude Code-style conversation sessions to the MCP Client for Ollama:

- Every run auto-persists its conversation to disk, scoped to the directory it
  was launched in.
- `ollmcp --resume <id>` resumes a specific session.
- `ollmcp --resume` (no id) resumes the most recent session for the current
  directory.
- `ollmcp` (no flag) starts a new session.

## Background (current architecture)

- `MCPClient.chat_history` is a `list[{"query", "response"}]` and **is** the
  conversation context — it is replayed into the Ollama `messages` array each
  turn (`client.py:491`). Persisting it therefore persists context.
- A turn is appended at `client.py:810`
  (`self.chat_history.append({"query": query, "response": response_text})`).
- Resource-context entries are added only transiently via
  `_temporary_history_extension` (backed up and restored around a single turn),
  so they never remain in `chat_history` between turns.
- Config and manual history exports already live under `~/.config/ollmcp/`.
- Entry point is Typer: `main()` callback → `async_main()` → `chat_loop()`.
  Exit is a `break` out of the `while True` loop in `chat_loop`.

## Storage layout

```
~/.config/ollmcp/sessions/<cwd-slug>/<id>.json
```

- `<cwd-slug>`: absolute current working directory with `os.sep` replaced by
  `-` (e.g. `/home/peter/proj` → `-home-peter-proj`). Scopes sessions to the
  launch directory.
- `<id>`: 8-character lowercase hex, `uuid4().hex[:8]`, minted once per fresh
  run.

Session file:

```json
{
  "id": "a1b2c3d4",
  "cwd": "/home/peter/proj",
  "model": "llama3",
  "host": "http://localhost:11434",
  "created": "2026-06-24T14:32:00",
  "updated": "2026-06-24T14:40:11",
  "history": [{"query": "...", "response": "..."}]
}
```

## New module: `mcp_client_for_ollama/utils/session.py`

A small `SessionManager`. No external deps; stdlib only (`json`, `os`, `uuid`,
`datetime`, `pathlib`).

```
class SessionManager:
    BASE = Path.home() / ".config" / "ollmcp" / "sessions"

    def __init__(self, cwd: str | None = None):
        self.cwd = os.path.abspath(cwd or os.getcwd())
        self.id = None          # set by new() or resume()
        self._created = None     # ISO string, preserved across saves

    @staticmethod
    def encode_cwd(cwd: str) -> str        # abspath -> slug
    def _dir(self) -> Path                  # BASE / encode_cwd(self.cwd)
    def _path(self, id: str) -> Path        # _dir() / f"{id}.json"

    def new(self) -> str                    # mint id, stamp created, return id
    def save(self, history, model, host)    # atomic write of current session
    def load(self, id: str) -> dict | None  # read+parse one session file
    def resolve_last(self) -> str | None    # newest "updated" id in this cwd dir
    def list(self) -> list[dict]            # metadata of sessions in this cwd (future use)
```

Behavior details:

- `save()` writes to `<id>.json.tmp` then `os.replace()` onto `<id>.json`
  (atomic; a crash mid-write cannot corrupt the previous good file). Creates the
  cwd-slug directory if missing. Stamps `updated` with `datetime.now().isoformat()`,
  preserves `created`.
- `load()` returns `None` on missing file or invalid/corrupt JSON (does not
  raise into the chat loop). Validates that `history` is a list of
  `{query, response}` dicts; on structural mismatch returns `None`.
- `resolve_last()` globs `<cwd-slug>/*.json`, reads each `updated` field, returns
  the id with the max value, or `None` if the directory has no valid sessions.

## CLI changes (`client.py`)

Add one option to the `main()` Typer callback:

- `--resume` / `-r`, an **optional-value** option implemented with Click's
  `flag_value` mechanism so all three forms work:
  - absent → `None` (new session)
  - `--resume` bare → sentinel `"__LAST__"` (resume newest in cwd)
  - `--resume <id>` → the id string

  Implementation: `typer.Option(None, "--resume", "-r", flag_value="__LAST__", ...)`.
  If Typer/Click does not cleanly pass `flag_value` through, fall back to a
  plain `Optional[str]` where an empty string / the literal `last` means "newest"
  — decided at implementation time, but the bare-`--resume`-means-last UX is the
  requirement.

Thread `resume` through `main()` → `async_main(...)` → into client setup.

Resume resolution in `async_main()` (after the client is constructed and
`auto_load_default_config()` has run, before `chat_loop()`):

1. Construct `client.session = SessionManager()` (cwd defaults to `os.getcwd()`).
2. If `resume` is set:
   - `target_id = client.session.resolve_last()` if `resume == "__LAST__"` else `resume`.
   - `data = client.session.load(target_id)` if `target_id` else `None`.
   - If `data`:
     - `client.chat_history = data["history"]`
     - `client.session.id = data["id"]`, preserve `created`.
     - If `--model` was **not** explicitly passed and `data["model"]` is set,
       restore it via the model manager (subject to the same install validation
       the normal model-resolution path uses).
     - If `--host` was **not** explicitly passed and `data["host"]` is set,
       restore it (rebuild the ollama AsyncClient as the existing host-override
       block does).
     - Print: `Resumed session <id> (<n> messages)`.
   - If no `data` (none found / bad id): print a warning
     (`No session found for <id>` or `No previous session in this directory`)
     and fall through to new-session.
3. If not resuming (or resume failed): `client.session.new()`.

The existing model/host resolution at `client.py:1923-1934` already prefers the
explicit `--model`/`--host` flags; session restore slots in by setting the saved
model/host as the "saved" inputs to that same resolution, so flags continue to
win without special-casing.

## Auto-save

- In `MCPClient.__init__`, add `self.session = None` (set later in `async_main`).
- After the append at `client.py:810`, add:
  ```python
  if self.session:
      self.session.save(self.chat_history,
                        self.model_manager.get_current_model(),
                        self.host)
  ```
  Save failures are caught inside `save()` and surfaced as a dim warning, never
  crash the turn.

## Startup discoverability

In `chat_loop()` startup banner area (near `print_startup_help`), print one line:

```
Session <id> — resume later with: ollmcp -r <id>
```

## Edge cases

- **`/clear`**: empties `chat_history` (existing behavior at `client.py:1379`).
  The next save (or an explicit save added to `clear_context`) persists the empty
  history under the same session id. Deliberate — the user cleared it.
- **Corrupt session file**: `load()` returns `None` → treated as "not found" →
  warn + new session. No crash.
- **No `updated` field / legacy file**: `resolve_last()` skips files it cannot
  parse.
- **First-ever run in a directory with bare `--resume`**: no sessions exist →
  warn + new session.
- **Concurrent runs in same dir**: each has its own id/file; last writer of a
  given id wins. Acceptable for a personal tool. (ponytail: no locking; add
  per-id locking only if multi-process editing of one session becomes real.)

## Testing

One self-check `tests/test_session.py` (stdlib + pytest tmp_path, matching repo
conventions) covering the non-trivial logic:

- `new()` mints an 8-hex id; `save()` then `load()` round-trips history + metadata.
- `save()` is atomic-ish: no `.tmp` left behind; second save updates `updated`
  but preserves `created`.
- `resolve_last()` returns the newest-`updated` id among several, `None` for an
  empty/missing cwd dir.
- `load()` returns `None` for a missing file and for malformed JSON / bad
  structure.
- `encode_cwd()` is stable and reversible-enough (slug matches expected form).

## Out of scope (add later if needed)

- `--list-sessions` / interactive picker.
- In-chat `/sessions` command.
- Automatic pruning / retention limits.
- Cross-directory "global last session".
