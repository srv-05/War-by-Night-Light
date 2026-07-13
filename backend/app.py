"""
This module initializes the Flask web application.
It provides the main entry point to create and configure the Flask app, including setting up CORS
and registering the API blueprints from various routes.
"""
from __future__ import annotations

from flask import Flask, jsonify
from flask_cors import CORS

import config


def create_app() -> Flask:
    """
    Creates and configures the Flask application instance.
    Sets up CORS using the configured origins and registers blueprints for different API domains.
    Also provides a simple health check endpoint.
    """
    # Create the Flask application instance
    app = Flask(__name__)
    # Enable Cross-Origin Resource Sharing (CORS) based on allowed origins from the config
    CORS(app, origins=config.CORS_ORIGINS)

    # Import the various route modules locally to avoid circular imports
    from backend.routes import conflict, countries, recovery, residual, spillover, trade

    # Register each module's blueprint with the application under the "/api" URL prefix
    for module in (countries, residual, conflict, recovery, spillover, trade):
        app.register_blueprint(module.bp, url_prefix="/api")

    # Define a simple health check endpoint to verify that the API is running
    @app.get("/api/health")
    def health():
        return jsonify(status="ok")

    return app


# Instantiate the Flask application
app = create_app()

# Run the Flask development server if the script is executed directly
if __name__ == "__main__":
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=True)
