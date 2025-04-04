# Generated by https://smithery.ai. See: https://smithery.ai/docs/config#dockerfile
FROM python:3.10-alpine

# Install build dependencies
RUN apk add --no-cache build-base

WORKDIR /app

# Copy the project files
COPY . /app

# Upgrade pip and install the package
RUN pip install --upgrade pip \
    && pip install .

# Expose port if needed (optional)
# EXPOSE 8080

# Set default command
CMD ["freshdesk-mcp"]
