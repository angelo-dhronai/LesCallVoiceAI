#!/bin/bash
# Run the Lescall live dashboard on port 8080
cd "$(dirname "$0")"
VENV=/home/angelo/lescall/calling-agent/venv
exec "$VENV/bin/python" server.py
