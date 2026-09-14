# The agent sandbox image: agent-harness + ScopeJudge + a believable ops-box toolbox.
# Built once per invocation by agent_sandbox.image from a staged, minimal build context (the
# application sources and requirements.txt exported from uv.lock) — never the repo
# root, so .env / .git / prior runs are structurally absent from every layer.
FROM python:3.13-slim

# A realistic toolbox — its absence is itself a "this is a bare eval box" tell — plus TLS roots.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl jq dnsutils netcat-openbsd iproute2 iputils-ping git less procps ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# agent-harness's runtime deps, hash-pinned exactly as uv.lock resolves them (agent_sandbox.image
# exports the pins and folds them into the image tag, so the container runs the same versions
# the host was tested against and a lockfile bump forces a rebuild — this project leans on
# fast-moving pydantic-ai internals, so an unpinned resolve here would skew the two sides).
# The library itself is put on PYTHONPATH below (no build backend, keeping the image simple).
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.txt

# Only the baseline library and the in-container application — never tasks or secrets.
COPY src/agent_harness /app/src/agent_harness
COPY src/scopejudge /app/src/scopejudge

# Non-root; HOME points at the mounted /work so the unprivileged user has a writable home.
ENV PYTHONPATH=/app/src \
    HOME=/work \
    PYTHONUNBUFFERED=1
USER 10001:10001

ENTRYPOINT ["python", "-m", "scopejudge"]
