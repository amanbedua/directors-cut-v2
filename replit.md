# Director's Cut — AI Cinematic Director System

A web app that turns uploaded images and voiceover audio into cinematic videos using an AI brain (Gemini) for cinematic direction and FFmpeg as the rendering engine.

## Run & Operate

- `pnpm --filter @workspace/cinematic-gen run dev` — run the React frontend (port 21037)
- `PORT=5000 python3 /home/runner/workspace/artifacts/video-backend/app.py` — run the Python video backend (port 5000)
- `pnpm --filter @workspace/api-server run dev` — run the Node.js API server (port 8080)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- Frontend: React + Vite, Tailwind CSS, Framer Motion, shadcn/ui
- Video Backend: Python 3.12 + Flask + FFmpeg + Gemini AI (via Replit AI Integrations)
- API: Express 5 (Node.js)
- DB: PostgreSQL + Drizzle ORM (not currently used by video features)
- Build: esbuild (CJS bundle for Node API)

## Where things live

- `artifacts/cinematic-gen/src/pages/home.tsx` — main UI page with AI Director workflow
- `artifacts/cinematic-gen/src/index.css` — dark cinematic theme (amber accent, deep navy bg)
- `artifacts/video-backend/app.py` — Flask app: upload, AI director analysis, video generation, status, download
- `artifacts/video-backend/uploads/` — temporary uploaded files (per-session subdirectories)
- `artifacts/video-backend/outputs/` — generated MP4 files

## Architecture: AI Brain + FFmpeg Body

### AI Director Flow (Gemini)
1. User uploads images (auto-sorted by scene number: scene1.png, scene2.png…)
2. User uploads voiceover audio
3. Frontend auto-triggers `/video-api/analyze` — sends audio as base64 to Gemini
4. Gemini analyzes emotional pacing, rhythm, mood → returns per-scene cinematic plan
5. AI Director's Brief is shown: pacing, mood, per-scene motion/transition/timing
6. User clicks EXECUTE RENDER → plan is sent to `/video-api/generate`
7. Python FFmpeg engine executes the plan: per-scene motion, transitions, timing, audio sync
8. Download the finished MP4

### Scene Ordering
- Images are auto-sorted by extracting numeric scene number from filename
- scene1.png, scene2.png, scene3.png → correct order automatically
- Falls back to alphabetical for unlabeled files

### Motion System (8 cinematic styles)
- `slow_push_in` — dramatic slow zoom in 1.0→1.45 (opening shots)
- `slow_pull_back` — reveal zoom out 1.45→1.0 (reflective moments)
- `drift_left` / `drift_right` — pan with gentle 1.25× zoom
- `dramatic_push` — aggressive zoom to 1.6×, lower-third focus (climax)
- `arc_left` / `arc_right` — combined pan + push-in (dynamic movement)
- `static_breathe` — imperceptible 1.12× breathe (contemplative moments)

### Transition System (5 types, AI-selected per scene)
- `fade` — standard crossfade (emotional moments)
- `dissolve` — slow dissolve (reflective moments)
- `wipeleft` / `wiperight` — directional wipe (energetic cuts)
- `circleopen` — circle expand (opening/dramatic reveals)

### Key Endpoints
- `POST /video-api/upload/images` — upload images, auto-sorts by scene number
- `POST /video-api/upload/audio` — upload audio, extracts duration
- `POST /video-api/analyze` — Gemini AI direction analysis (returns per-scene plan)
- `POST /video-api/generate` — start render job (accepts optional ai_plan)
- `GET /video-api/status/:job_id` — poll render progress
- `GET /video-api/download/:job_id` — download finished MP4

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

- Python packages live in `.pythonlibs/` (managed by uv). If Flask/requests is missing, re-run `installLanguagePackages`.
- The Google Fonts `@import url(...)` in `index.css` MUST be line 0 (before all other imports) or PostCSS fails silently.
- Gemini audio input: max 8MB inline (files API not supported via AI Integrations). Large audio files fall back to the intelligent cinematic plan.
- FFmpeg zoompan: images are scaled 2× before zoompan to avoid edge artifacts on strong motion.
- xfade offsets are calculated from cumulative hold_times (not fixed 3s intervals) — critical for variable scene durations.
- The `artifacts/cinematic-gen: video-api` workflow runs the Python backend on port 5000 with absolute path.
- AI_INTEGRATIONS_GEMINI_BASE_URL and AI_INTEGRATIONS_GEMINI_API_KEY are auto-provisioned by Replit AI Integrations.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
