# Universal Container Scaffold for SolidWorks CAD MCP Server
FROM python:3.11-slim

# Install Xvfb, Mesa software rasterizer, and core tools for headless operation
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    libgl1-mesa-glx \
    libgl1-mesa-dri \
    libglu1-mesa \
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY . .

# Wrap entrypoint in xvfb-run for virtual framebuffer offscreen rendering
ENTRYPOINT ["xvfb-run", "-a", "--server-args=-screen 0 1920x1080x24", "python", "-m", "src.server"]
