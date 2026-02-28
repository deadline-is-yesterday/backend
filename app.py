from flask import Flask
from flask_cors import CORS

from extensions import socketio
import radio
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app, origins="*")

    socketio.init_app(app, cors_allowed_origins="*", async_mode="eventlet")
    radio.init_app(socketio)

    return app


if __name__ == "__main__":
    app = create_app()
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
