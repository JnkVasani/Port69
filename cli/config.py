"""Port69 - CLI Configuration"""
import json
from pathlib import Path
from typing import Optional


class CLIConfig:
    CONFIG_DIR = Path.home() / ".port69"
    CONFIG_FILE = CONFIG_DIR / "config.json"
    TOKEN_FILE = CONFIG_DIR / "token"
    HISTORY_FILE = CONFIG_DIR / "history"

    def __init__(self):
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        if self.CONFIG_FILE.exists():
            try:
                return json.loads(self.CONFIG_FILE.read_text())
            except Exception:
                return {}
        return {}

    def _save(self):
        self.CONFIG_FILE.write_text(json.dumps(self._data, indent=2))

    @property
    def server_url(self) -> str:
        return self._data.get("server_url", "https://port69-server.onrender.com")

    @server_url.setter
    def server_url(self, v: str):
        self._data["server_url"] = v.rstrip("/")
        self._save()

    @property
    def ws_url(self) -> str:
        return self.server_url.replace("http://", "ws://").replace("https://", "wss://")

    @property
    def username(self) -> Optional[str]:
        return self._data.get("username")

    @username.setter
    def username(self, v: str):
        self._data["username"] = v
        self._save()

    @property
    def avatar_color(self) -> str:
        return self._data.get("avatar_color", "#00ff88")

    @avatar_color.setter
    def avatar_color(self, v: str):
        self._data["avatar_color"] = v
        self._save()

    @property
    def token(self) -> Optional[str]:
        if self.TOKEN_FILE.exists():
            return self.TOKEN_FILE.read_text().strip() or None
        return None

    @token.setter
    def token(self, v: Optional[str]):
        if v:
            self.TOKEN_FILE.write_text(v)
            self.TOKEN_FILE.chmod(0o600)
        elif self.TOKEN_FILE.exists():
            self.TOKEN_FILE.unlink()

    @property
    def theme(self) -> str:
        return self._data.get("theme", "dark")

    @theme.setter
    def theme(self, v: str):
        self._data["theme"] = v
        self._save()

    @property
    def download_dir(self) -> Path:
        p = Path(self._data.get("download_dir", str(Path.home() / "Downloads" / "Port69")))
        p.mkdir(parents=True, exist_ok=True)
        return p

    @download_dir.setter
    def download_dir(self, v: str):
        self._data["download_dir"] = v
        self._save()

    @property
    def input_history(self) -> list:
        if self.HISTORY_FILE.exists():
            try:
                return json.loads(self.HISTORY_FILE.read_text())
            except Exception:
                return []
        return []

    def add_history(self, text: str):
        h = self.input_history
        if text and (not h or h[-1] != text):
            h.append(text)
            if len(h) > 200:
                h = h[-200:]
            self.HISTORY_FILE.write_text(json.dumps(h))

    def is_authenticated(self) -> bool:
        return bool(self.token and self.username)

    def clear_auth(self):
        self.token = None
        self._data.pop("username", None)
        self._data.pop("avatar_color", None)
        self._save()


config = CLIConfig()
