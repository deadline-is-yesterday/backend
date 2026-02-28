from flask import request
from flask_socketio import Namespace, emit
import logging

from extensions import socketio

logger = logging.getLogger(__name__)


class RadioNamespace(Namespace):
    """PTT radio stack: only the first speaker in the stack is heard by all."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stack: list[str] = []
        self._clients: set[str] = set()

    def _log_state(self) -> None:
        logger.info(
            "clients=%d %s | stack=%s",
            len(self._clients),
            list(self._clients),
            self._stack,
        )

    def _broadcast_stack_update(self) -> None:
        active = bool(self._stack)
        logger.info(
            "stack_update → active=%s, speaker=%s | clients=%d %s",
            active,
            self._stack[0] if self._stack else None,
            len(self._clients),
            list(self._clients),
        )
        emit("stack_update", {"active": active}, broadcast=True)

    def on_connect(self) -> None:
        sid = request.sid
        self._clients.add(sid)
        logger.info("connect: sid=%s", sid)
        self._log_state()

    def on_disconnect(self) -> None:
        sid = request.sid
        self._clients.discard(sid)
        logger.info("disconnect: sid=%s", sid)
        if sid in self._stack:
            self._stack.remove(sid)
        self._log_state()
        if not self._stack:
            self._broadcast_stack_update()

    def on_ptt_start(self) -> None:
        sid = request.sid
        logger.info("ptt_start: sid=%s", sid)
        if sid not in self._stack:
            self._stack.append(sid)
            self._broadcast_stack_update()

    def on_ptt_stop(self) -> None:
        sid = request.sid
        logger.info("ptt_stop: sid=%s", sid)
        if sid in self._stack:
            self._stack.remove(sid)
            self._broadcast_stack_update()

    def on_audio_chunk(self, data: bytes) -> None:
        sid = request.sid
        if self._stack and self._stack[0] == sid:
            logger.debug("audio_chunk: sid=%s bytes=%d → relay to %d clients", sid, len(data), len(self._clients) - 1)
            socketio.emit("audio_chunk", data, namespace="/", skip_sid=sid)
