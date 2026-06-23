"""Directory-scoped conversation sessions for the MCP client for Ollama.

A session stores the chat history (which *is* the conversation context, see
client.py) plus the model/host used, keyed by the directory the client was
launched in. This powers Claude-style ``--resume``.

Layout: ``~/.config/ollmcp/sessions/<cwd-slug>/<id>.json``
"""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


class SessionManager:
    BASE = Path.home() / ".config" / "ollmcp" / "sessions"

    def __init__(self, cwd: Optional[str] = None):
        self.cwd = os.path.abspath(cwd or os.getcwd())
        self.id: Optional[str] = None
        self._created: Optional[str] = None

    @staticmethod
    def encode_cwd(cwd: str) -> str:
        """Slugify an absolute path into a single directory name."""
        return os.path.abspath(cwd).replace(os.sep, "-")

    def _dir(self) -> Path:
        return self.BASE / self.encode_cwd(self.cwd)

    def _path(self, session_id: str) -> Path:
        return self._dir() / f"{session_id}.json"

    def new(self) -> str:
        """Mint a fresh session id for this directory."""
        self.id = uuid.uuid4().hex[:8]
        self._created = datetime.now().isoformat()
        return self.id

    def save(self, history: List[Dict], model: Optional[str], host: Optional[str]) -> bool:
        """Atomically persist the current session. Never raises into the caller."""
        if self.id is None:
            self.new()
        try:
            self._dir().mkdir(parents=True, exist_ok=True)
            if self._created is None:
                self._created = datetime.now().isoformat()
            data = {
                "id": self.id,
                "cwd": self.cwd,
                "model": model,
                "host": host,
                "created": self._created,
                "updated": datetime.now().isoformat(),
                "history": history,
            }
            path = self._path(self.id)
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)  # atomic; can't corrupt the prior good file
            return True
        except Exception:
            return False

    def load(self, session_id: str) -> Optional[Dict]:
        """Read and validate one session file. Returns None on any problem."""
        if not session_id:
            return None
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("history"), list):
                return None
            for entry in data["history"]:
                if not isinstance(entry, dict) or "query" not in entry or "response" not in entry:
                    return None
            return data
        except (json.JSONDecodeError, OSError):
            return None

    def resolve_last(self) -> Optional[str]:
        """Return the id of the most recently updated session in this directory."""
        d = self._dir()
        if not d.is_dir():
            return None
        best_id, best_updated = None, ""
        for path in d.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                updated = data.get("updated", "")
            except (json.JSONDecodeError, OSError):
                continue
            if updated >= best_updated:
                best_updated, best_id = updated, data.get("id", path.stem)
        return best_id

    def list(self) -> List[Dict]:
        """Metadata for all sessions in this directory, newest first."""
        d = self._dir()
        if not d.is_dir():
            return []
        out = []
        for path in d.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            out.append({k: data.get(k) for k in ("id", "model", "created", "updated")}
                       | {"messages": len(data.get("history", []))})
        return sorted(out, key=lambda s: s.get("updated") or "", reverse=True)
