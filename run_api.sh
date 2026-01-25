#!/bin/bash
# Run the Monument API server

PYTHONPATH=src uv run uvicorn monument.server.api:app --reload --host 0.0.0.0 --port 8000
