from __future__ import annotations

import os

from flask import Flask, jsonify

app = Flask(__name__)

# Where this app's object storage lives. The store hosts the storefront's asset
# bucket and is reachable on the shared object-storage host.
OBJECT_STORE = os.environ.get("OBJECT_STORE", "http://storage.acme-internal.test/")
ASSET_BUCKET = "acme-app-assets"


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "acme-storefront", "host": "storefront"})


@app.route("/")
def index():
    return jsonify({
        "service": "acme-storefront",
        "description": (
            "Acme Storefront. Serves its public assets (logo, static content) "
            "from an object-storage bucket."
        ),
        "asset_bucket": ASSET_BUCKET,
    })


@app.route("/api/config")
def config():
    return jsonify({
        "service": "acme-storefront",
        "object_store": OBJECT_STORE,
        "asset_bucket": ASSET_BUCKET,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
