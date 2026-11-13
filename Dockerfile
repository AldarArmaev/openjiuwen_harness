# Harness image for the openJiuwen hackathon.
# Pinned base tag and pinned openJiuwen version per the regulation.
FROM python:3.12-slim-bookworm

# No apt packages are needed at runtime; keeping the layer empty avoids build-time downloads.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install openJiuwen from PyPI at a pinned version (sanctioned by the regulation).
# All build-time downloads land in the image; nothing is fetched at run time.
RUN pip install --no-cache-dir openjiuwen==0.1.19

# Third-party libraries the harness itself uses for data_analysis / coding tools.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Harness code and entry point.
COPY harness/ /app/harness/
COPY run_agent.py /app/run_agent.py

# The container runs as a non-root user; HOME is set to the writable tmpfs by the runner.
RUN useradd --create-home --shell /usr/sbin/nologin agent
USER agent

# Entrypoint is overridden by the runner (--entrypoint python /app/run_agent.py --tasks-dir /data/tasks).
CMD ["python", "/app/run_agent.py"]
