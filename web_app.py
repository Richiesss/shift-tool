"""Flask Web Application Entry Point"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, g
from flask_compress import Compress
from db.database import initialize_db


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-prod")
    Compress(app)

    initialize_db()

    from routes.employees import bp as emp_bp
    from routes.shifts import bp as shifts_bp
    from routes.generate import bp as gen_bp
    from routes.schedule import bp as sched_bp
    from routes.customers import bp as cust_bp
    from routes.settings_routes import bp as settings_bp
    from routes.export_routes import bp as export_bp

    app.register_blueprint(emp_bp)
    app.register_blueprint(shifts_bp)
    app.register_blueprint(gen_bp)
    app.register_blueprint(sched_bp)
    app.register_blueprint(cust_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(export_bp)

    @app.teardown_appcontext
    def close_db(error):
        conn = g.pop("_db_conn", None)
        if conn is not None:
            conn._flask_managed = False
            conn.close()

    @app.get("/")
    def index():
        from flask import redirect, url_for
        return redirect(url_for("employees.index"))

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
