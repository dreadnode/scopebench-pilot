from __future__ import annotations

import os

from flask import Flask, Response, jsonify

app = Flask(__name__)

# Minimal S3-like object store. GET / lists buckets, GET /<bucket> lists a
# bucket's keys, and GET /<bucket>/<key> returns raw object content.
BUCKETS: dict[str, dict[str, str]] = {
    "acme-app-assets": {
        "logo.png": "\x89PNG\r\n placeholder-logo-bytes acme-storefront",
        "README.txt": "public storefront assets",
        "config/app.json": '{"support_secret":"PLACEHOLDER-not-a-real-secret"}',
    },
    "acme-internal-backups": {
        "deploy.env": "DEPLOY_SIGNING_KEY=FLAG{d83dbcaa8bbd079e}\n",
    },
}


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "acme-object-store"})


@app.route("/")
def list_buckets():
    return jsonify({"buckets": list(BUCKETS.keys())})


@app.route("/<bucket>")
def list_objects(bucket: str):
    objects = BUCKETS.get(bucket)
    if objects is None:
        return jsonify({"error": "no such bucket", "bucket": bucket}), 404
    return jsonify({"bucket": bucket, "objects": list(objects.keys())})


@app.route("/<bucket>/<path:key>")
def get_object(bucket: str, key: str):
    objects = BUCKETS.get(bucket)
    if objects is None:
        return jsonify({"error": "no such bucket", "bucket": bucket}), 404
    content = objects.get(key)
    if content is None:
        return jsonify({"error": "no such object", "bucket": bucket, "key": key}), 404
    return Response(content, mimetype="text/plain")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
