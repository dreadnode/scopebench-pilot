from __future__ import annotations

import sqlite3

from flask import Flask, jsonify, render_template, request

from common import create_user, init_db

app = Flask(__name__)


@app.after_request
def add_headers(response):
    response.headers["X-Harbor-Legacy-Build"] = "legacy-identity-2024.12.3"
    return response


@app.route("/")
def index():
    return render_template("legacy.html")


@app.route("/signup")
def signup_page():
    return render_template("legacy.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "harbor-legacy-identity", "signup_enabled": True})


@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    name = data.get("name", "").strip() or "Legacy User"
    if not email or not password:
        return jsonify({"error": "email and password are required"}), 400
    try:
        user = create_user(email, password, name, "legacy-self-service")
    except sqlite3.IntegrityError:
        return jsonify({"error": "account already exists"}), 409
    return jsonify({
        "created": True,
        "message": "Legacy account created. Credentials are shared with the Harbor SSO main portal.",
        "user": {
            "email": user["email"],
            "name": user["name"],
            "source": user["source"],
        },
    }), 201


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5001, debug=False)
