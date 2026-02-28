from flask import Flask
from flask_socketio import SocketIO

from .routes import bp
from .events import FireSimNamespace


def init_app(app: Flask) -> None:
    app.register_blueprint(bp)


def init_socketio(sio: SocketIO) -> None:
    sio.on_namespace(FireSimNamespace("/firesim"))
