from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Flask, jsonify, request

app = Flask(__name__)

DB_PATH = Path("/data/storefront.sqlite3")
DBA_PASSWORD = "staging-adminer-rotate"


@app.after_request
def add_headers(response):
    response.headers["X-Ardent-Service"] = "database-admin"
    return response


@app.route("/")
def index():
    return """
    <html><title>Ardent DB Admin</title><body>
      <h1>Ardent Database Admin</h1>
      <p>Operations console for direct SQLite query diagnostics.</p>
      <p>Use <code>POST /api/query</code> with JSON fields <code>username</code>,
      <code>password</code>, and <code>sql</code>.</p>
    </body></html>
    """


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "ardent-dbadmin"})


@app.route("/api/query", methods=["POST"])
def query():
    data = request.get_json(silent=True) or {}
    if data.get("username") != "dba" or data.get("password") != DBA_PASSWORD:
        return jsonify({"error": "invalid database admin credentials"}), 403

    sql = (data.get("sql") or "").strip()
    if not sql.lower().startswith("select"):
        return jsonify({"error": "read-only console accepts SELECT statements only"}), 400

    try:
        with sqlite3.connect(DB_PATH) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute(sql).fetchall()]
    except sqlite3.Error as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"rows": rows})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
