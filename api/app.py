from flask import Flask
from flask_cors import CORS

from api.routes.health import health_bp
from api.routes.missions import missions_bp

def create_app():
    app = Flask(__name__)
    CORS(app)

    app.register_blueprint(health_bp)
    app.register_blueprint(missions_bp)

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=8000, debug=False)
