import os
import time
import json
import logging
from pathlib import Path
from PyQt5.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

SESSIONS_DIR = Path.home() / ".videosnatch" / "sessions"


class BrowserSession(QObject):
    def __init__(self, name: str, session_id=None, parent=None):
        super().__init__(parent)
        self.name = name
        self.session_id = session_id or f"sess_{int(time.time())}_{hash(name) & 0xFFFFFF:06x}"
        self._dir = SESSIONS_DIR / self.session_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._context = None
        self._created_at = time.time()

    @property
    def storage_dir(self) -> str:
        return str(self._dir)

    @property
    def context(self):
        return self._context

    @context.setter
    def context(self, ctx):
        self._context = ctx

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "session_id": self.session_id,
            "created_at": self._created_at,
        }


class SessionManager(QObject):
    session_changed = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sessions: dict[str, BrowserSession] = {}
        self._active_session_id: str = ""
        self._engine = None
        self._restore_sessions()

    def set_engine(self, engine):
        self._engine = engine

    def _restore_sessions(self):
        if not SESSIONS_DIR.exists():
            SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            return
        for d in SESSIONS_DIR.iterdir():
            if d.is_dir():
                meta = d / "session.json"
                if meta.exists():
                    try:
                        data = json.loads(meta.read_text("utf-8"))
                        sess = BrowserSession(
                            name=data.get("name", d.name),
                            session_id=d.name,
                        )
                        self._sessions[sess.session_id] = sess
                    except Exception:
                        pass

    def _save_session_meta(self, session: BrowserSession):
        meta = session._dir / "session.json"
        meta.write_text(json.dumps(session.to_dict(), indent=2), "utf-8")

    def create_session(self, name: str) -> BrowserSession:
        sess = BrowserSession(name)
        self._sessions[sess.session_id] = sess
        self._save_session_meta(sess)
        if self._active_session_id is None:
            self.activate_session(sess.session_id)
        logger.info(f"Session 创建: {name} ({sess.session_id})")
        return sess

    def activate_session(self, session_id: str):
        if session_id not in self._sessions:
            logger.warning(f"Session 不存在: {session_id}")
            return
        old_id = self._active_session_id
        self._active_session_id = session_id
        sess = self._sessions[session_id]
        logger.info(f"Session 切换: {old_id} -> {session_id} ({sess.name})")
        self.session_changed.emit(old_id or "", session_id)

    def get_active_session(self) -> BrowserSession:
        return self._sessions.get(self._active_session_id)

    def list_sessions(self) -> list[BrowserSession]:
        return list(self._sessions.values())

    def delete_session(self, session_id: str):
        if session_id == self._active_session_id:
            return
        sess = self._sessions.pop(session_id, None)
        if sess:
            import shutil
            shutil.rmtree(sess._dir, ignore_errors=True)
            logger.info(f"Session 删除: {sess.name} ({session_id})")

    def get_or_create_default(self):
        if not self._sessions:
            return self.create_session("Default")
        if not self._active_session_id:
            first = list(self._sessions.values())[0]
            self.activate_session(first.session_id)
        if self._active_session_id in self._sessions:
            return self._sessions[self._active_session_id]
        return self.create_session("Default")
