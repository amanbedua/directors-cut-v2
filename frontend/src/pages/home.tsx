import React, { useState, useRef, useCallback } from "react";
import { API_BASE_URL } from "@/config";

type Quality = "480p" | "720p" | "1080p";
type Status = "idle" | "processing" | "done" | "error";

// Compress image to max 1200px wide, 80% JPEG quality — keeps payload small
function compressImage(file: File, maxW = 1200): Promise<string> {
  return new Promise((res, rej) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, maxW / img.width);
      const w = Math.round(img.width * scale);
      const h = Math.round(img.height * scale);
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      canvas.getContext("2d")!.drawImage(img, 0, 0, w, h);
      URL.revokeObjectURL(url);
      res(canvas.toDataURL("image/jpeg", 0.82));
    };
    img.onerror = rej;
    img.src = url;
  });
}

function fileToB64(file: File): Promise<string> {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result as string);
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}

export default function Home() {
  const imageInputRef = useRef<HTMLInputElement>(null);
  const audioInputRef = useRef<HTMLInputElement>(null);

  const [images, setImages] = useState<File[]>([]);
  const [imagePreviews, setImagePreviews] = useState<string[]>([]);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [quality, setQuality] = useState<Quality>("480p");
  const [duration, setDuration] = useState(4);
  const [prompt, setPrompt] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [progress, setProgress] = useState(0);
  const [progressMsg, setProgressMsg] = useState("");
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [videoBlob, setVideoBlob] = useState<Blob | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [isDragOver, setIsDragOver] = useState(false);
  const [meta, setMeta] = useState<{ scenes: number; size: string; time: string; quality: string } | null>(null);
  const progTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [backendOk, setBackendOk] = useState<boolean | null>(null);

  React.useEffect(() => {
    fetch(API_BASE_URL + "/health", { signal: AbortSignal.timeout(8000) })
      .then(r => setBackendOk(r.ok))
      .catch(() => setBackendOk(false));
  }, []);

  const addImages = useCallback(async (files: File[]) => {
    const imgs = files.filter(f => f.type.startsWith("image/")).slice(0, 20 - images.length);
    if (!imgs.length) return;
    // Generate previews
    const previews = imgs.map(f => URL.createObjectURL(f));
    setImages(prev => [...prev, ...imgs].slice(0, 20));
    setImagePreviews(prev => [...prev, ...previews].slice(0, 20));
  }, [images.length]);

  const removeImage = (i: number) => {
    setImages(prev => prev.filter((_, idx) => idx !== i));
    setImagePreviews(prev => prev.filter((_, idx) => idx !== i));
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    addImages([...e.dataTransfer.files]);
  };

  const simProgress = (from: number, to: number, msg: string, ms: number) => {
    if (progTimerRef.current) clearInterval(progTimerRef.current);
    let cur = from;
    const step = (to - from) / (ms / 400);
    setProgressMsg(msg);
    progTimerRef.current = setInterval(() => {
      cur = Math.min(cur + step + Math.random() * 0.4, to);
      setProgress(Math.round(cur));
      if (cur >= to && progTimerRef.current) clearInterval(progTimerRef.current);
    }, 400);
  };

  const generate = async () => {
    if (!images.length) return;
    setStatus("processing");
    setProgress(0);
    setErrorMsg("");
    setVideoUrl(null);
    setMeta(null);
    const t0 = Date.now();

    try {
      // Step 1: Compress images
      setProgressMsg("Compressing images…");
      setProgress(5);
      const compressed = await Promise.all(images.map(f => compressImage(f)));

      setProgress(12);
      setProgressMsg("Reading audio…");
      let b64Audio: string | null = null;
      if (audioFile) b64Audio = await fileToB64(audioFile);

      // Step 2: Start job (quick response — just returns job_id)
      setProgress(15);
      const totalKB = Math.round(compressed.reduce((s, b) => s + b.length * 0.75 / 1024, 0));
      setProgressMsg(`Uploading ${images.length} scenes (${totalKB} KB)…`);

      const startResp = await fetch(API_BASE_URL + "/generate-video", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          images: compressed,
          audio: b64Audio,
          quality,
          prompt: prompt.trim(),
          scene_duration: duration,
        }),
      });

      if (!startResp.ok) {
        const err = await startResp.json().catch(() => ({ error: "Server error " + startResp.status }));
        throw new Error(err.error || "Server error " + startResp.status);
      }

      const { job_id } = await startResp.json();
      if (!job_id) throw new Error("No job_id returned from server");

      // Step 3: Poll /status/:job_id until done
      setProgress(20);
      setProgressMsg("Rendering cinematic video…");

      await new Promise<void>((resolve, reject) => {
        const poll = setInterval(async () => {
          try {
            const statusResp = await fetch(API_BASE_URL + "/status/" + job_id);
            if (!statusResp.ok) { clearInterval(poll); reject(new Error("Status check failed")); return; }
            const s = await statusResp.json();
            if (s.status === "error") { clearInterval(poll); reject(new Error(s.message || "Render failed")); return; }
            if (s.status === "done") { clearInterval(poll); resolve(); return; }
            // Map backend progress (0-100) to frontend range (20-88)
            const mapped = 20 + Math.round((s.progress / 100) * 68);
            setProgress(Math.max(mapped, progress));
            setProgressMsg(s.message || "Rendering…");
          } catch (pollErr) {
            // Network hiccup — keep polling
            console.warn("Poll error:", pollErr);
          }
        }, 2000); // poll every 2s
      });

      // Step 4: Download finished video
      setProgress(92);
      setProgressMsg("Downloading video…");
      const dlResp = await fetch(API_BASE_URL + "/download/" + job_id);
      if (!dlResp.ok) throw new Error("Download failed: " + dlResp.status);

      const blob = await dlResp.blob();
      const url = URL.createObjectURL(blob);
      const elapsed = ((Date.now() - t0) / 1000).toFixed(0);
      const sizeMB = (blob.size / 1024 / 1024).toFixed(1);

      setVideoBlob(blob);
      setVideoUrl(url);
      setMeta({ scenes: images.length, size: sizeMB + " MB", time: elapsed + "s", quality });
      setProgress(100);
      setProgressMsg("Done!");
      setStatus("done");

    } catch (e: unknown) {
      if (progTimerRef.current) clearInterval(progTimerRef.current);
      const msg = (e as Error).message || "Unknown error";
      setErrorMsg(msg);
      setStatus("error");
    }
  };

  const download = () => {
    if (!videoBlob || !videoUrl) return;
    const a = document.createElement("a");
    a.href = videoUrl;
    a.download = "directors_cut_" + Date.now() + ".mp4";
    a.click();
  };

  const reset = () => {
    setImages([]); setImagePreviews([]); setAudioFile(null);
    setVideoUrl(null); setVideoBlob(null); setStatus("idle");
    setProgress(0); setProgressMsg(""); setErrorMsg(""); setMeta(null); setPrompt("");
    if (imageInputRef.current) imageInputRef.current.value = "";
    if (audioInputRef.current) audioInputRef.current.value = "";
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const isProcessing = status === "processing";

  return (
    <div style={S.page}>
      <header style={S.header}>
        <div style={S.brand}>
          <span style={S.brandName}>Director's Cut</span>
          <span style={S.brandTag}>AI</span>
        </div>
        <div style={S.statusRow}>
          <span style={{ ...S.dot, background: backendOk === null ? "#555" : backendOk ? "#3dff9a" : "#ff5555", boxShadow: backendOk ? "0 0 6px rgba(61,255,154,0.5)" : "none" }} />
          <span style={S.statusText}>{backendOk === null ? "Connecting…" : backendOk ? "Backend online" : "Backend offline"}</span>
        </div>
      </header>

      <main style={S.main}>

        {/* Images */}
        <div style={S.card}>
          <div style={S.cardLabel}>Scenes — Images</div>
          <div
            style={{ ...S.uploadZone, ...(isDragOver ? S.uploadZoneHover : {}) }}
            onDragOver={e => { e.preventDefault(); setIsDragOver(true); }}
            onDragLeave={() => setIsDragOver(false)}
            onDrop={onDrop}
            onClick={() => imageInputRef.current?.click()}
          >
            <input ref={imageInputRef} type="file" accept="image/*" multiple style={S.hiddenInput}
              onChange={e => { addImages([...(e.target.files || [])]); e.target.value = ""; }} />
            <div style={S.uploadIcon}>⬆</div>
            <div style={S.uploadTitle}>Drop images here or tap to upload</div>
            <div style={S.uploadSub}>JPG, PNG, WEBP · Max 20 images · Auto-compressed</div>
          </div>
          {images.length > 0 && (
            <div style={S.imgGrid}>
              {imagePreviews.map((src, i) => (
                <div key={i} style={S.thumb}>
                  <img src={src} alt="" style={S.thumbImg} />
                  <span style={S.sceneNum}>{i + 1}</span>
                  <button style={S.delBtn} onClick={e => { e.stopPropagation(); removeImage(i); }}>×</button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Audio */}
        <div style={S.card}>
          <div style={S.cardLabel}>Soundtrack — Audio</div>
          <div style={S.audioRow} onClick={() => !audioFile && audioInputRef.current?.click()}>
            <input ref={audioInputRef} type="file" accept="audio/*" style={S.hiddenInput}
              onChange={e => setAudioFile(e.target.files?.[0] || null)} />
            <span style={{ fontSize: 22, color: audioFile ? "#d4ff3d" : "#555" }}>♪</span>
            <div style={{ flex: 1 }}>
              {audioFile
                ? <span style={S.audioName}>{audioFile.name}</span>
                : <><div style={{ fontSize: 13, fontWeight: 500, color: "#888" }}>Add background music</div><div style={{ fontSize: 11, color: "#555" }}>MP3, WAV, M4A (optional)</div></>}
            </div>
            {audioFile
              ? <button style={S.clearBtn} onClick={e => { e.stopPropagation(); setAudioFile(null); if (audioInputRef.current) audioInputRef.current.value = ""; }}>×</button>
              : <span style={{ color: "#555", fontSize: 13 }}>Browse →</span>}
          </div>
        </div>

        {/* Settings */}
        <div style={S.card}>
          <div style={S.cardLabel}>Settings</div>
          <div style={S.settingsGrid}>
            <div>
              <div style={S.settingLabel}>Quality</div>
              <select value={quality} onChange={e => setQuality(e.target.value as Quality)} style={S.select}>
                <option value="480p">480p — Fast</option>
                <option value="720p">720p — HD</option>
                <option value="1080p">1080p — Full HD</option>
              </select>
            </div>
            <div>
              <div style={S.settingLabel}>Scene Duration: <span style={{ color: "#d4ff3d" }}>{duration}s</span></div>
              <input type="range" min={2} max={10} step={1} value={duration}
                onChange={e => setDuration(+e.target.value)}
                style={{ width: "100%", accentColor: "#d4ff3d", marginTop: 10 }} />
            </div>
          </div>
          <div style={{ marginTop: 14 }}>
            <div style={S.settingLabel}>AI Director Prompt (optional)</div>
            <textarea value={prompt} onChange={e => setPrompt(e.target.value)}
              placeholder="e.g. cinematic thriller, emotional wedding, travel vlog…"
              style={S.textarea} />
          </div>
        </div>

        {status === "error" && <div style={S.errorBox}>⚠ {errorMsg}</div>}

        <button
          style={{ ...S.btnGenerate, opacity: (!images.length || isProcessing) ? 0.35 : 1, cursor: (!images.length || isProcessing) ? "not-allowed" : "pointer" }}
          disabled={!images.length || isProcessing}
          onClick={generate}
        >
          {isProcessing ? "Generating…" : images.length ? `Generate Video — ${images.length} scene${images.length > 1 ? "s" : ""}` : "Select images to generate video"}
        </button>

        {/* Progress */}
        {isProcessing && (
          <div style={S.card}>
            <div style={S.stepsRow}>
              {(["Compress", "Upload", "Render", "Done"] as const).map((label, i) => {
                const pcts = [0, 15, 20, 100];
                const done = progress > pcts[i + 1 < 4 ? i + 1 : 3] || (i === 3 && progress === 100);
                const active = progress >= pcts[i] && !done;
                return (
                  <div key={i} style={S.step}>
                    <div style={{ ...S.stepDot, ...(done ? S.stepDone : active ? S.stepActive : {}) }}>
                      {done ? "✓" : i + 1}
                    </div>
                    <div style={{ ...S.stepLabel, ...(done ? { color: "#d4ff3d" } : active ? { color: "#aaa" } : {}) }}>{label}</div>
                  </div>
                );
              })}
            </div>
            <div style={S.progressWrap}>
              <div style={{ ...S.progressFill, width: progress + "%" }} />
            </div>
            <div style={S.progressText}>{progressMsg}</div>
          </div>
        )}

        {/* Result */}
        {status === "done" && videoUrl && (
          <div style={S.card}>
            <div style={S.cardLabel}>Preview</div>
            {meta && (
              <div style={S.metaRow}>
                {[meta.quality, meta.scenes + " scenes", meta.size, meta.time].map(m => (
                  <span key={m} style={S.metaPill}>{m}</span>
                ))}
              </div>
            )}
            <video src={videoUrl} controls playsInline style={S.video} />
            <div style={S.resultActions}>
              <button style={S.btnDl} onClick={download}>⬇ Download Video</button>
              <button style={S.btnNew} onClick={reset}>New Video</button>
            </div>
          </div>
        )}

      </main>
    </div>
  );
}

const S: Record<string, React.CSSProperties> = {
  page: { background: "#0b0b0b", color: "#f0f0f0", minHeight: "100vh", fontFamily: "'DM Sans','Inter',sans-serif" },
  header: { padding: "24px 20px 18px", borderBottom: "1px solid #1e1e1e", display: "flex", alignItems: "center", justifyContent: "space-between" },
  brand: { display: "flex", alignItems: "baseline", gap: 10 },
  brandName: { fontFamily: "Georgia,serif", fontSize: 22, fontWeight: 600, letterSpacing: "0.02em" },
  brandTag: { fontSize: 11, fontWeight: 500, letterSpacing: "0.12em", textTransform: "uppercase", color: "#d4ff3d", background: "rgba(212,255,61,0.1)", padding: "3px 8px", borderRadius: 4 },
  statusRow: { display: "flex", alignItems: "center", gap: 6 },
  dot: { width: 7, height: 7, borderRadius: "50%", display: "inline-block", flexShrink: 0 },
  statusText: { fontSize: 12, color: "#555" },
  main: { maxWidth: 560, margin: "0 auto", padding: "24px 16px 80px", display: "flex", flexDirection: "column", gap: 16 },
  card: { background: "#161616", border: "1px solid #222", borderRadius: 12, padding: 20 },
  cardLabel: { fontSize: 11, fontWeight: 500, letterSpacing: "0.1em", textTransform: "uppercase", color: "#444", marginBottom: 14 },
  uploadZone: { border: "1.5px dashed #2a2a2a", borderRadius: 8, padding: "28px 20px", textAlign: "center", cursor: "pointer", position: "relative" },
  uploadZoneHover: { borderColor: "#d4ff3d", background: "rgba(212,255,61,0.05)" },
  hiddenInput: { position: "absolute", inset: 0, opacity: 0, cursor: "pointer", width: "100%", height: "100%" },
  uploadIcon: { fontSize: 26, color: "#444", marginBottom: 8 },
  uploadTitle: { fontSize: 14, fontWeight: 500, color: "#666", marginBottom: 4 },
  uploadSub: { fontSize: 12, color: "#444" },
  imgGrid: { display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8, marginTop: 14 },
  thumb: { position: "relative", aspectRatio: "1", borderRadius: 8, overflow: "hidden", background: "#1e1e1e", border: "1px solid #2a2a2a" },
  thumbImg: { width: "100%", height: "100%", objectFit: "cover", display: "block" },
  sceneNum: { position: "absolute", top: 5, left: 5, background: "rgba(0,0,0,0.75)", color: "#d4ff3d", fontSize: 10, fontWeight: 500, padding: "2px 6px", borderRadius: 4 },
  delBtn: { position: "absolute", top: 5, right: 5, width: 22, height: 22, borderRadius: "50%", background: "rgba(0,0,0,0.8)", border: "none", color: "#fff", fontSize: 14, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center" },
  audioRow: { display: "flex", alignItems: "center", gap: 12, padding: "14px 16px", border: "1.5px dashed #2a2a2a", borderRadius: 8, cursor: "pointer", position: "relative" },
  audioName: { fontSize: 13, fontWeight: 500, color: "#f0f0f0", maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block" },
  clearBtn: { background: "none", border: "none", color: "#555", fontSize: 20, cursor: "pointer", padding: 4, lineHeight: 1, flexShrink: 0 },
  settingsGrid: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 },
  settingLabel: { fontSize: 11, color: "#555", letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 8 },
  select: { width: "100%", background: "#1e1e1e", border: "1px solid #2a2a2a", borderRadius: 8, color: "#f0f0f0", fontFamily: "inherit", fontSize: 14, padding: "10px 12px", outline: "none" },
  textarea: { width: "100%", background: "#1e1e1e", border: "1px solid #2a2a2a", borderRadius: 8, color: "#f0f0f0", fontFamily: "inherit", fontSize: 14, padding: "10px 12px", outline: "none", resize: "none", height: 68, lineHeight: 1.5, boxSizing: "border-box" },
  errorBox: { background: "rgba(255,85,85,0.08)", border: "1px solid rgba(255,85,85,0.25)", borderRadius: 8, padding: "14px 16px", fontSize: 13, color: "#ff9090" },
  btnGenerate: { width: "100%", padding: 16, background: "#d4ff3d", color: "#0b0b0b", border: "none", borderRadius: 12, fontFamily: "inherit", fontSize: 15, fontWeight: 500, cursor: "pointer" },
  stepsRow: { display: "flex", justifyContent: "space-between", marginBottom: 20 },
  step: { display: "flex", flexDirection: "column", alignItems: "center", flex: 1, gap: 6 },
  stepDot: { width: 28, height: 28, borderRadius: "50%", border: "1.5px solid #2a2a2a", background: "#161616", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, color: "#444" },
  stepActive: { borderColor: "#d4ff3d", color: "#d4ff3d", background: "rgba(212,255,61,0.08)" },
  stepDone: { borderColor: "#d4ff3d", background: "#d4ff3d", color: "#0b0b0b" },
  stepLabel: { fontSize: 10, color: "#444", letterSpacing: "0.05em", textTransform: "uppercase" },
  progressWrap: { background: "#1e1e1e", borderRadius: 99, height: 4, overflow: "hidden", marginBottom: 10 },
  progressFill: { height: "100%", background: "#d4ff3d", borderRadius: 99, transition: "width 0.5s ease" },
  progressText: { fontSize: 13, color: "#666", textAlign: "center" },
  metaRow: { display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 },
  metaPill: { fontSize: 11, color: "#555", background: "#1e1e1e", border: "1px solid #2a2a2a", borderRadius: 99, padding: "4px 10px" },
  video: { width: "100%", borderRadius: 8, background: "#000", display: "block", marginBottom: 14, maxHeight: 320, objectFit: "contain" },
  resultActions: { display: "flex", gap: 10 },
  btnDl: { flex: 1, padding: 13, background: "#d4ff3d", color: "#0b0b0b", border: "none", borderRadius: 8, fontFamily: "inherit", fontSize: 14, fontWeight: 500, cursor: "pointer" },
  btnNew: { padding: "13px 18px", background: "none", color: "#888", border: "1px solid #2a2a2a", borderRadius: 8, fontFamily: "inherit", fontSize: 14, cursor: "pointer", whiteSpace: "nowrap" },
};
