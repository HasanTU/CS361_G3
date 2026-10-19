"""Create and run the Flask application."""

# Import Flask to create the web application and redirect the root page.
from flask import Flask, redirect, url_for
from flask_cors import CORS

from backend import config
from backend.routes import auth_bp, documents_bp
from backend.routes.search import search_bp
from backend.services.mvp_seed_service import seed_mvp_documents
from scripts.seed_mock_users import seed_users
from scripts.seed_mock_documents import seed_documents


# Create and configure the Flask application.
def create_app():
    # Create the Flask application instance.
    app = Flask(
        __name__,
        static_folder="../frontend",
        static_url_path="/frontend",
    )

    # Load settings such as the MongoDB URI and upload folder.
    app.config.from_object(config)
    app.secret_key = config.SECRET_KEY
    CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)
    app.json.ensure_ascii = False

    # Register document API routes under /api/v1/documents.
    app.register_blueprint(documents_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(auth_bp)

    # Add three stakeholder demo documents only to a fresh database.
    seed_mvp_documents()
    if config.SEED_MOCK_USERS:
        seed_users()
    if config.SEED_MOCK_DOCUMENTS:
        seed_documents()
    return app


# Create the application instance used by Flask and Docker.
app = create_app()


# Redirect the root URL to the staff document registration page.
@app.get("/")
def home():
    return redirect(url_for("static", filename="html/staff_upload.html"))


# Start the development server when this file is executed directly.
if __name__ == "__main__":
    # Listen on all container interfaces at port 5000.
    app.run(host="0.0.0.0", port=5000)
