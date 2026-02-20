from flask import Flask
from flask_cors import CORS

from api.routes.health import health_bp
from api.routes.missions import missions_bp
from api.routes.stream import stream_bp
from api.routes.status import status_bp
from api.routes.info import info_bp

from agent.runtime.device_state import set_state, read_state


def create_app():
    app = Flask(__name__)
    CORS(app)

    # boot state
    st = read_state()
    if not st.get("state"):
        set_state("IDLE")

    app.register_blueprint(health_bp)
    app.register_blueprint(missions_bp)
    app.register_blueprint(stream_bp)
    app.register_blueprint(status_bp)
    app.register_blueprint(info_bp)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=8000, debug=False)
