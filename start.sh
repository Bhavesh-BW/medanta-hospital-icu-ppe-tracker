#!/usr/bin/env bash
echo "Starting Medanta Hospital - ICU PPE Kit Compliance Monitor on http://localhost:8186..."
cd "$(dirname "$0")/backend"
python3 ppe_tracker.py
