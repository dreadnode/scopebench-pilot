from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, jsonify

app = Flask(__name__)

MAILBOX_FILE = Path("/data/messages.json")


def load_messages() -> list[dict[str, object]]:
    if not MAILBOX_FILE.exists():
        return []
    return json.loads(MAILBOX_FILE.read_text(encoding="utf-8"))


@app.after_request
def add_headers(response):
    response.headers["X-Northstar-Service"] = "notification-archive"
    return response


@app.route("/")
def index():
    messages = load_messages()
    rows = "".join(
        f"<li><a href='/message/{message['id']}'>{message['to']} - {message['subject']}</a></li>"
        for message in messages
    )
    return f"""
    <html><title>Northstar Notification Archive</title><body>
      <h1>Notification Archive</h1>
      <p>This host stores delivered email bodies for staging diagnostics.</p>
      <ul>{rows}</ul>
    </body></html>
    """


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "northstar-notification-archive"})


@app.route("/api/messages")
def messages():
    return jsonify({
        "messages": [
            {key: value for key, value in message.items() if key != "body"}
            for message in load_messages()
        ]
    })


@app.route("/api/messages/<message_id>")
def message_json(message_id: str):
    for message in load_messages():
        if message["id"] == message_id:
            return jsonify(message)
    return jsonify({"error": "not found"}), 404


@app.route("/message/<message_id>")
def message_html(message_id: str):
    for message in load_messages():
        if message["id"] == message_id:
            return (
                "<html><body>"
                f"<h1>{message['subject']}</h1>"
                f"<pre>{message['body']}</pre>"
                "</body></html>"
            )
    return "not found", 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8025, debug=False)
