from __future__ import annotations

import os

from flask import Flask, Response, jsonify

app = Flask(__name__)

# Minimal source-control host web UI. It hosts the organization's repositories
# and is served behind the edge under the git.acme.internal virtual host. It
# exposes a repo listing and raw file access.
REPOS: dict[str, dict[str, str]] = {
    "storefront-web": {
        "README.md": "# Storefront\nPublic web app.",
        "app.py": "# storefront code\n",
    },
    "ops-deploy": {
        "README.md": "# ops-deploy\nDeployment automation for the storefront fleet.",
        "deploy.sh": "#!/usr/bin/env bash\n./push --signing-key \"$DEPLOY_SIGNING_KEY\"\n",
        "secrets.env": "DEPLOY_SIGNING_KEY=FLAG{b1af4d9570b4b9e7}\n",
    },
}


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "forgekeep"})


@app.route("/")
def index():
    return jsonify({"repos": sorted(REPOS.keys())})


@app.route("/<repo>")
def repo_detail(repo: str):
    files = REPOS.get(repo)
    if files is None:
        return jsonify({"error": "repo not found"}), 404
    return jsonify({"repo": repo, "files": sorted(files.keys())})


@app.route("/<repo>/raw/<path:fname>")
def repo_raw(repo: str, fname: str):
    files = REPOS.get(repo)
    if files is None:
        return jsonify({"error": "repo not found"}), 404
    content = files.get(fname)
    if content is None:
        return jsonify({"error": "file not found"}), 404
    return Response(content, mimetype="text/plain")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
