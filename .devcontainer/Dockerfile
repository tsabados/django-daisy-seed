FROM mcr.microsoft.com/devcontainers/python:2-3.14-trixie

# # Install Node.js features
# RUN bash /tmp/devcontainers-features-mnt/stu-bell---devcontainer-features---node/install.sh \
#     && bash /tmp/devcontainers-features-mnt/va-h---devcontainers-features---uv/install.sh

# Install PostgreSQL client tools
RUN rm -f /etc/apt/sources.list.d/yarn.list
RUN apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
