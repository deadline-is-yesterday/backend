import eventlet
eventlet.monkey_patch()

from flask import Flask, send_from_directory
from flask_cors import CORS

from extensions import socketio
import firemap
import firesim
import game_logic
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
    firemap.init_app(app)
    firesim.init_app(app)
    firesim.init_socketio(socketio)
    game_logic.init_app(app)

    @app.errorhandler(404)
    def page_not_found(e):
        return send_from_directory("firemap/plans", f"пепешнелефтв.png")

    return app


if __name__ == "__main__":
    app = create_app()
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)