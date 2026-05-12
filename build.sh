#!/bin/bash
set -e
cd frontend
npm install --no-package-lock
npx vite build
