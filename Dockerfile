FROM python:3.12-slim

# Install system dependencies required for PyQt6 wheel installation
RUN apt-get update && apt-get install -y --no-install-recommends \
    libegl1 \
    libgl1 \
    libxkbcommon0 \
    libdbus-1-3 \
    libfontconfig1 \
    libxcb-cursor0 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-randr0 \
    libxcb-render-util0 \
    libxcb-shape0 \
    libxcb-sync1 \
    libxcb-xfixes0 \
    libxcb-xkb1 \
    libxkbcommon-x11-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY automation ./automation
COPY shared ./shared
COPY examples ./examples
COPY docs ./docs
COPY AGENTS.md LICENSE ./

RUN pip install --no-cache-dir -e .

ENTRYPOINT ["mosdat"]
CMD ["--help"]
