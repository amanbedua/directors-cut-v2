---
title: "Directors Cut Backend"
emoji: "🎬"
colorFrom: amber
colorTo: indigo
sdk: docker
sdk_version: "1.0.0"
python_version: "3.11"
app_file: app.py
app_port: 7860
pinned: false
---

# Directors Cut Backend

Docker-based Hugging Face Space for the Directors Cut v2 Flask API.

## Routes

- `GET /health`
- `POST /generate-video`
- `GET /status/<job_id>`
- `GET /download/<job_id>`
