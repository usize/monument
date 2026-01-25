# Juliette email processing container using Debian slim for glibc compatibility
FROM debian:bookworm-slim

LABEL maintainer="Juliette AI System"
LABEL description="Docker sandbox for Juliette email processing system"
LABEL version="2.0"

ENV DEBIAN_FRONTEND=noninteractive

# Install OS packages, runtimes, and build tooling
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    ca-certificates \
    tzdata \
    openssl \
    pandoc \
    git \
    jq \
    wget \
    gnupg2 \
    gcc \
    g++ \
    make \
    pkg-config \
    python3 \
    python3-pip \
    python3-venv \
    nodejs \
    npm \
    openssh-client \
    less \
    unzip \
    fonts-liberation \
    libnss3 \
    && rm -rf /var/lib/apt/lists/*


# Normalize binary names for utilities
RUN cp /usr/sbin/killall5 /usr/sbin/killall

# Create a non-root user for security
RUN useradd -ms /bin/bash bsp-agent
RUN wget https://mistral.ai/vibe/install.sh
RUN chmod +x install.sh
RUN su -c "./install.sh" bsp-agent

WORKDIR /app

# Copy source
COPY . /app/
COPY ./configs/vibe /home/bsp-agent/.vibe
RUN chown -R bsp-agent:bsp-agent /home/bsp-agent/.vibe 

# Install shellnium dependency for rendered web fetching
RUN cd /app 

# Ensure utilities are executable and owned by bsp-agent
RUN chmod +x /app/utils/*.sh && \
    mkdir -p /app/logs /app/configs/bsp-agent && \
    chown -R bsp-agent:bsp-agent /app

USER bsp-agent

ENV PATH="/home/bsp-agent/.local/bin:/app/utils:$PATH"

# Default command executes the email processor; override as needed
CMD ["/app/bin/initiate-turn.sh"]
