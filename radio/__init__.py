from flask_socketio import SocketIO

from .events import RadioNamespace


def init_app(sio: SocketIO) -> None:
    sio.on_namespace(RadioNamespace("/"))
