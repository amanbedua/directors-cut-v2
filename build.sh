#!/bin/bash
set -e
echo "=== Directors Cut — Frontend Build ==="
cd frontend
echo "Installing dependencies..."
npm install
echo "Building..."
npm run build
echo "=== Build complete! ==="
