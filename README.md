# Directors Cut v2 — AI Cinematic Video Generator

## Project Structure

```
directors-cut-v2/
├── backend/          ← Python Flask API (deploys to HF Space)
│   ├── app.py        ← Main server — all video generation logic
│   ├── Dockerfile    ← Container config for HF Space
│   └── requirements.txt
│
├── frontend/         ← React App (deploys to Render)
│   ├── src/
│   │   ├── pages/home.tsx   ← Main UI
│   │   ├── config.ts        ← API URL config
│   │   └── ...
│   ├── index.html
│   └── package.json
│
├── .github/workflows/
│   └── hf-sync.yml   ← Auto-deploys backend/ to HF Space on push
│
└── render.yaml       ← Render build config for frontend/
```

## Deployments

| Part | Platform | URL |
|------|----------|-----|
| Frontend | Render (Static) | https://directors-cut-v2-2.onrender.com |
| Backend | HF Space (Docker) | https://amanbedua-directors-cut-backend.hf.space |

## How Deployment Works

### Backend (HF Space)
- Push any change to `backend/` on `main` branch
- GitHub Actions (`hf-sync.yml`) automatically syncs to HF Space
- HF Space rebuilds Docker container

### Frontend (Render)
- Push any change to `frontend/` on `main` branch  
- Render auto-detects via `render.yaml`
- Build: `npm install && npm run build` inside `frontend/`
- Publish: `frontend/dist/`

## API Routes

| Method | Route | Description |
|--------|-------|-------------|
| POST | `/generate-video` | Start video job → returns `{ job_id }` |
| GET | `/status/<job_id>` | Poll progress → `{ status, progress, message }` |
| GET | `/download/<job_id>` | Download finished video |
| GET | `/health` | Health check |

## Local Dev

### Backend
```bash
cd backend
pip install -r requirements.txt
python app.py
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```
