# botburrow-runner image
#
# Build (tag with a pinned semver from a VERSION reference — never :latest):
#   docker build -t ronaldraygun/botburrow-runner:0.1.0 -f runner/Dockerfile .
#
# Runtime configuration is via environment variables — see runner/README.md.
FROM python:3.12-slim

# git is needed by scripts/config_loader.py to clone/pull definitions repos.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY runner/requirements.txt /app/runner/requirements.txt
RUN pip install --no-cache-dir -r /app/runner/requirements.txt

COPY runner/ /app/runner/
COPY scripts/config_loader.py /app/scripts/config_loader.py

# Run as the non-root user.
RUN useradd --system --uid 10001 runner
USER runner

# STATE_DIR must point somewhere writable by uid 10001 (mount an emptyDir
# at /var/lib/botburrow-runner in the Deployment).
ENV STATE_DIR=/var/lib/botburrow-runner \
    PYTHONUNBUFFERED=1

CMD ["python", "-m", "runner"]
