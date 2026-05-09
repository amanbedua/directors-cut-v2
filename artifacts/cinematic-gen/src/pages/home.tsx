import React, { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  UploadCloud,
  Image as ImageIcon,
  Music,
  Settings2,
  Play,
  Download,
  AlertCircle,
  CheckCircle2,
  Loader2,
  X,
  Clapperboard,
  ArrowRight,
  Wand2,
  Send,
  MessageSquare,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useToast } from "@/hooks/use-toast";
import { Progress } from "@/components/ui/progress";
import { API_BASE_URL } from "@/config";

type GenerateStatus =
  | "idle"
  | "uploading"
  | "analyzing"
  | "plan_ready"
  | "queued"
  | "processing"
  | "done"
  | "error";

interface AudioState {
  id: string;
  path: string;
  duration: number | null;
}

interface ScenePlan {
  scene_number: number;
  duration: number;
  motion: string;
  transition: string;
  intensity: string;
  direction_note: string;
}

interface AIPlan {
  pacing: string;
  mood: string;
  zoom_intensity?: string;
  transition_duration?: number;
  scenes: ScenePlan[];
}

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

const MOTION_LABELS: Record<string, string> = {
  slow_push_in: "Slow Push In",
  slow_pull_back: "Slow Pull Back",
  drift_left: "Drift Left",
  drift_right: "Drift Right",
  dramatic_push: "Dramatic Push",
  arc_left: "Arc Left",
  arc_right: "Arc Right",
  static_breathe: "Static Breathe",
};

const EXAMPLE_PROMPTS = [
  "Make pacing emotional",
  "Add dramatic zooms",
  "Keep final scene longer",
  "Add subtle motion",
  "Smoother transitions",
  "More dynamic energy",
  "Melancholic and slow",
  "Build to a climax",
];

export default function Home() {
  const { toast } = useToast();
  const imageInputRef = useRef<HTMLInputElement>(null);
  const audioInputRef = useRef<HTMLInputElement>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);

  const [images, setImages] = useState<File[]>([]);
  const [sceneNames, setSceneNames] = useState<string[]>([]);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [audio, setAudio] = useState<AudioState | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [aiPlan, setAiPlan] = useState<AIPlan | null>(null);
  const [status, setStatus] = useState<GenerateStatus>("idle");
  const [jobId, setJobId] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [statusMessage, setStatusMessage] = useState("");
  const [perImageDuration, setPerImageDuration] = useState(5);
  const [quality, setQuality] = useState<"480p" | "720p" | "1080p">("720p");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [isChatting, setIsChatting] = useState(false);

  useEffect(() => {
    if (chatEndRef.current) {
      chatEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [chatMessages]);

  const handleImageSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    setAiPlan(null);
    setChatMessages([]);
    setStatus("uploading");

    const form = new FormData();
    files.forEach((f) => form.append("images", f));
    try {
      const res = await fetch(`${API_BASE_URL}/video-api/upload/images`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) throw new Error("Image upload failed");
      const data = await res.json();
      setSessionId(data.session_id);
      setImages(files);
      setSceneNames(data.scene_names || files.map((f) => f.name));
      setStatus("idle");
    } catch {
      setStatus("error");
      toast({ variant: "destructive", title: "Upload Failed", description: "Could not upload images." });
    }
  };

  const handleAudioSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setAudioFile(file);
    setAiPlan(null);
    setChatMessages([]);

    const form = new FormData();
    form.append("audio", file);
    try {
      const res = await fetch(`${API_BASE_URL}/video-api/upload/audio`, { method: "POST", body: form });
      if (!res.ok) throw new Error("Audio upload failed");
      const data = await res.json();
      setAudio({ id: data.audio_id, path: data.path, duration: data.duration });
    } catch {
      toast({ variant: "destructive", title: "Upload Failed", description: "Could not upload audio." });
    }
  };

  const removeImage = (idx: number) => {
    setImages((prev) => prev.filter((_, i) => i !== idx));
    setSceneNames((prev) => prev.filter((_, i) => i !== idx));
    setAiPlan(null);
    setChatMessages([]);
  };

  const removeAudio = () => {
    setAudioFile(null);
    setAudio(null);
    setAiPlan(null);
    setChatMessages([]);
  };

  const handleAnalyze = async () => {
    if (!sessionId) return;
    setStatus("analyzing");
    setAiPlan(null);
    setChatMessages([]);
    try {
      const res = await fetch(`${API_BASE_URL}/video-api/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          audio_path: audio?.path || null,
          audio_duration: audio?.duration || null,
        }),
      });
      if (!res.ok) throw new Error("Analysis failed");
      const data = await res.json();
      setAiPlan(data.plan);
      setSceneNames(data.scene_names || sceneNames);
      setStatus("plan_ready");
      setChatMessages([
        {
          role: "assistant",
          content: `Direction plan ready — ${data.plan.pacing} pacing, ${data.plan.mood} mood across ${data.plan.scenes.length} scenes. Type any instruction to refine it.`,
        },
      ]);
    } catch {
      setStatus("error");
      toast({ variant: "destructive", title: "Analysis Failed", description: "Could not analyze the project." });
    }
  };

  useEffect(() => {
    if (sessionId && audio && audio.duration && !aiPlan && status === "idle") {
      handleAnalyze();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, audio]);

  const handleChat = async (messageOverride?: string) => {
    const message = messageOverride || chatInput.trim();
    if (!message || !aiPlan || isChatting) return;
    setChatInput("");
    setChatMessages((prev) => [...prev, { role: "user", content: message }]);
    setIsChatting(true);
    try {
      const res = await fetch(`${API_BASE_URL}/video-api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          current_plan: aiPlan,
          scene_names: sceneNames.filter(Boolean),
          audio_duration: audio?.duration || null,
        }),
      });
      if (!res.ok) throw new Error("Chat failed");
      const data = await res.json();
      setAiPlan(data.plan);
      setChatMessages((prev) => [
        ...prev,
        { role: "assistant", content: data.acknowledgment || "Plan updated." },
      ]);
    } catch {
      setChatMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Couldn't process that instruction — try rephrasing." },
      ]);
    } finally {
      setIsChatting(false);
    }
  };

  const handleGenerate = async () => {
    if (!sessionId || images.length === 0) return;
    setStatus("processing");
    setProgress(0);
    setStatusMessage("Initializing render pipeline...");
    try {
      const res = await fetch(`${API_BASE_URL}/video-api/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          audio_path: audio?.path || null,
          audio_duration: audio?.duration || null,
          per_image_duration: perImageDuration,
          ai_plan: aiPlan,
          quality,
        }),
      });
      if (!res.ok) throw new Error("Generation failed to start");
      const data = await res.json();
      setJobId(data.job_id);
    } catch {
      setStatus("error");
      toast({ variant: "destructive", title: "Generation Failed", description: "Failed to initialize render." });
    }
  };

  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (jobId && (status === "processing" || status === "queued" || (status === "analyzing" && !!jobId))) {
      interval = setInterval(async () => {
        try {
          const res = await fetch(`${API_BASE_URL}/video-api/status/${jobId}`);
          if (!res.ok) return;
          const data = await res.json();
          setProgress(data.progress || 0);
          setStatusMessage(data.message || "");
          if (data.status === "done") {
            setStatus("done");
            clearInterval(interval);
          } else if (data.status === "error") {
            setStatus("error");
            clearInterval(interval);
            toast({ variant: "destructive", title: "Render Error", description: data.message || "Unknown error." });
          } else {
            setStatus(data.status);
          }
        } catch {
          // ignore transient errors
        }
      }, 1500);
    }
    return () => { if (interval) clearInterval(interval); };
  }, [jobId, status, toast]);

  const canAnalyze = images.length > 0 && sessionId && status === "idle";
  const canRender = images.length > 0 && sessionId && (status === "plan_ready" || (!audio && images.length > 0 && status === "idle"));
  const isWorking = status === "processing" || status === "queued" || (status === "analyzing" && !!jobId);
  const isPreAnalyzing = status === "analyzing" && !jobId;

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col p-4 md:p-8 selection:bg-primary/30">
      <div className="max-w-6xl w-full mx-auto space-y-6">

        {/* Header */}
        <header className="space-y-1 pt-2">
          <h1 className="text-3xl font-mono font-bold tracking-tight text-primary flex items-center gap-3">
            <Clapperboard className="w-7 h-7" />
            DIRECTOR'S CUT
          </h1>
          <p className="text-muted-foreground font-mono text-xs max-w-xl">
            AI Cinematic Director — Upload sequences & audio. Let Gemini craft the vision. Render.
          </p>
        </header>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">

          {/* Left — Main Workspace */}
          <div className="lg:col-span-8 space-y-5">

            {/* Sequence Frames */}
            <Card className="border-border/50 bg-card/50 backdrop-blur">
              <CardHeader className="pb-3 flex flex-row items-center justify-between">
                <CardTitle className="font-mono text-xs tracking-widest text-muted-foreground flex items-center gap-2">
                  <ImageIcon className="w-4 h-4" />
                  SEQUENCE FRAMES
                </CardTitle>
                {images.length > 0 && (
                  <span className="text-[10px] font-mono text-muted-foreground">
                    {images.length} LOADED — AUTO-SORTED BY SCENE NUMBER
                  </span>
                )}
              </CardHeader>
              <CardContent>
                {images.length === 0 ? (
                  <div
                    className="border-2 border-dashed border-border/50 rounded-lg p-8 flex flex-col items-center justify-center text-center cursor-pointer hover:bg-white/5 hover:border-primary/50 transition-colors"
                    onClick={() => imageInputRef.current?.click()}
                  >
                    <UploadCloud className="w-10 h-10 text-muted-foreground mb-3" />
                    <p className="text-sm font-medium mb-1">Click to upload images</p>
                    <p className="text-xs text-muted-foreground">JPG, PNG, WEBP</p>
                    <p className="text-[10px] text-muted-foreground/60 font-mono mt-3">
                      Name files scene1.png, scene2.png… for automatic ordering
                    </p>
                    <input type="file" multiple accept="image/*" className="hidden" ref={imageInputRef} onChange={handleImageSelect} />
                  </div>
                ) : (
                  <div className="space-y-3">
                    <div className="grid grid-cols-4 sm:grid-cols-7 gap-2">
                      {images.map((file, i) => (
                        <div key={i} className="flex flex-col gap-1 group">
                          <div className="relative aspect-video rounded-md overflow-hidden bg-muted">
                            <img
                              src={URL.createObjectURL(file)}
                              alt={`Scene ${i + 1}`}
                              className="object-cover w-full h-full opacity-80 group-hover:opacity-100 transition-opacity"
                            />
                            <button
                              className="absolute top-0.5 right-0.5 bg-black/60 p-0.5 rounded-sm opacity-0 group-hover:opacity-100 hover:bg-destructive/80 transition-all"
                              onClick={(e) => { e.stopPropagation(); removeImage(i); }}
                            >
                              <X className="w-2.5 h-2.5 text-white" />
                            </button>
                          </div>
                          {sceneNames[i] && (
                            <span className="text-[9px] font-mono text-muted-foreground truncate w-full text-center">
                              {sceneNames[i]}
                            </span>
                          )}
                        </div>
                      ))}
                      <div
                        className="aspect-video rounded-md border-2 border-dashed border-border/30 flex items-center justify-center cursor-pointer hover:border-primary/50 transition-colors"
                        onClick={() => imageInputRef.current?.click()}
                      >
                        <UploadCloud className="w-4 h-4 text-muted-foreground/50" />
                        <input type="file" multiple accept="image/*" className="hidden" ref={imageInputRef} onChange={handleImageSelect} />
                      </div>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Master Audio */}
            <Card className="border-border/50 bg-card/50 backdrop-blur">
              <CardHeader className="pb-3">
                <CardTitle className="font-mono text-xs tracking-widest text-muted-foreground flex items-center gap-2">
                  <Music className="w-4 h-4" />
                  MASTER AUDIO
                </CardTitle>
              </CardHeader>
              <CardContent>
                {!audioFile ? (
                  <div
                    className="border-2 border-dashed border-border/50 rounded-lg p-5 flex flex-col items-center justify-center text-center cursor-pointer hover:bg-white/5 hover:border-primary/50 transition-colors"
                    onClick={() => audioInputRef.current?.click()}
                  >
                    <UploadCloud className="w-7 h-7 text-muted-foreground mb-2" />
                    <p className="text-sm font-medium mb-1">Click to upload voiceover or score</p>
                    <p className="text-xs text-muted-foreground">MP3, WAV, AAC (Optional)</p>
                    <input type="file" accept="audio/*" className="hidden" ref={audioInputRef} onChange={handleAudioSelect} />
                  </div>
                ) : (
                  <div className="flex items-center justify-between p-3 border border-border/50 rounded-lg bg-black/20">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
                        <Music className="w-4 h-4 text-primary" />
                      </div>
                      <div>
                        <p className="text-sm font-medium truncate max-w-[200px]">{audioFile.name}</p>
                        <p className="text-xs text-muted-foreground font-mono">
                          {audio?.duration ? `${audio.duration.toFixed(1)}s — AI will sync all scenes to this duration` : "Processing..."}
                        </p>
                      </div>
                    </div>
                    <Button variant="ghost" size="icon" onClick={removeAudio} className="text-muted-foreground hover:text-destructive hover:bg-destructive/10 flex-shrink-0">
                      <X className="w-4 h-4" />
                    </Button>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* AI Pre-analyzing indicator */}
            <AnimatePresence>
              {isPreAnalyzing && (
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  className="flex items-center gap-3 px-4 py-3 rounded-lg border border-primary/30 bg-primary/5"
                >
                  <Loader2 className="w-4 h-4 text-primary animate-spin flex-shrink-0" />
                  <span className="font-mono text-xs text-primary">
                    GEMINI AI DIRECTOR IS ANALYZING YOUR PROJECT...
                  </span>
                </motion.div>
              )}
            </AnimatePresence>

            {/* AI Director's Brief */}
            <AnimatePresence>
              {aiPlan && !isPreAnalyzing && (
                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.4 }}
                >
                  <Card className="border-primary/30 bg-card/80 backdrop-blur shadow-[0_0_20px_rgba(234,179,8,0.06)] overflow-hidden">
                    <div className="bg-primary/10 border-b border-primary/20 px-5 py-2.5 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Clapperboard className="w-4 h-4 text-primary" />
                        <span className="font-mono text-xs tracking-widest text-primary font-bold">AI DIRECTOR'S BRIEF</span>
                      </div>
                      <div className="flex items-center gap-1.5 font-mono text-[10px]">
                        <span className="bg-background px-2 py-0.5 rounded-sm border border-border uppercase">{aiPlan.pacing}</span>
                        <span className="bg-background px-2 py-0.5 rounded-sm border border-border uppercase">{aiPlan.mood}</span>
                        {aiPlan.zoom_intensity && (
                          <span className="bg-primary/10 border border-primary/30 text-primary px-2 py-0.5 rounded-sm uppercase">
                            {aiPlan.zoom_intensity} ZOOM
                          </span>
                        )}
                      </div>
                    </div>
                    <CardContent className="p-0">
                      <div className="divide-y divide-border/30 max-h-80 overflow-y-auto">
                        {aiPlan.scenes.map((scene, idx) => (
                          <div key={idx} className="px-4 py-2.5 hover:bg-white/5 transition-colors flex items-center gap-3 group">
                            <div className="flex-shrink-0 w-7 h-7 rounded-full bg-primary/20 flex items-center justify-center border border-primary/30 text-primary font-mono text-xs font-bold">
                              {scene.scene_number}
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2 mb-0.5">
                                <span className="text-xs font-medium truncate">
                                  {sceneNames[idx] || `Scene ${scene.scene_number}`}
                                </span>
                                <span className="text-[10px] font-mono text-muted-foreground bg-black/30 px-1.5 py-0.5 rounded flex-shrink-0">
                                  {scene.duration.toFixed(1)}s
                                </span>
                              </div>
                              <p className="text-[10px] text-muted-foreground truncate" title={scene.direction_note}>
                                {scene.direction_note}
                              </p>
                            </div>
                            <div className="flex items-center gap-2 flex-shrink-0">
                              <div className="flex flex-col items-end gap-0.5">
                                <span className="text-[9px] uppercase font-mono bg-primary/10 text-primary px-1.5 py-0.5 rounded-sm border border-primary/20 whitespace-nowrap">
                                  {MOTION_LABELS[scene.motion] || scene.motion}
                                </span>
                                <span className="text-[9px] uppercase font-mono text-muted-foreground">
                                  {scene.intensity}
                                </span>
                              </div>
                              {idx < aiPlan.scenes.length - 1 && (
                                <div className="text-muted-foreground/50 group-hover:text-muted-foreground transition-colors flex flex-col items-center">
                                  <ArrowRight className="w-3 h-3" />
                                  <span className="text-[8px] font-mono mt-0.5 uppercase">{scene.transition}</span>
                                </div>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </CardContent>
                  </Card>
                </motion.div>
              )}
            </AnimatePresence>

            {/* AI Chat Director */}
            <AnimatePresence>
              {aiPlan && !isPreAnalyzing && (
                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.4, delay: 0.1 }}
                >
                  <Card className="border-border/60 bg-card/50 backdrop-blur overflow-hidden">
                    <div className="border-b border-border/40 px-5 py-2.5 flex items-center gap-2">
                      <MessageSquare className="w-4 h-4 text-muted-foreground" />
                      <span className="font-mono text-xs tracking-widest text-muted-foreground font-medium">AI CHAT DIRECTOR</span>
                      <span className="text-[10px] text-muted-foreground/50 font-mono ml-auto">type instructions to refine the cinematic plan</span>
                    </div>
                    <CardContent className="p-4 space-y-3">
                      {/* Example prompts */}
                      {chatMessages.length <= 1 && (
                        <div className="flex flex-wrap gap-1.5">
                          {EXAMPLE_PROMPTS.map((prompt) => (
                            <button
                              key={prompt}
                              onClick={() => handleChat(prompt)}
                              disabled={isChatting}
                              className="text-[10px] font-mono px-2.5 py-1 rounded-full border border-border/60 text-muted-foreground hover:border-primary/50 hover:text-primary hover:bg-primary/5 transition-all disabled:opacity-40"
                            >
                              {prompt}
                            </button>
                          ))}
                        </div>
                      )}

                      {/* Chat history */}
                      {chatMessages.length > 0 && (
                        <div className="space-y-2 max-h-48 overflow-y-auto">
                          {chatMessages.map((msg, i) => (
                            <div
                              key={i}
                              className={`flex gap-2 ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                            >
                              <div
                                className={`max-w-[85%] px-3 py-2 rounded-lg text-xs font-mono ${
                                  msg.role === "user"
                                    ? "bg-primary/20 text-primary border border-primary/30 ml-8"
                                    : "bg-muted/50 text-muted-foreground border border-border/40 mr-8"
                                }`}
                              >
                                {msg.content}
                              </div>
                            </div>
                          ))}
                          {isChatting && (
                            <div className="flex gap-2 justify-start">
                              <div className="px-3 py-2 rounded-lg text-xs font-mono bg-muted/50 border border-border/40">
                                <Loader2 className="w-3 h-3 animate-spin text-muted-foreground" />
                              </div>
                            </div>
                          )}
                          <div ref={chatEndRef} />
                        </div>
                      )}

                      {/* Chat input */}
                      <div className="flex gap-2">
                        <Input
                          value={chatInput}
                          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setChatInput(e.target.value)}
                          onKeyDown={(e: React.KeyboardEvent<HTMLInputElement>) => {
                            if (e.key === "Enter" && !e.shiftKey) {
                              e.preventDefault();
                              handleChat();
                            }
                          }}
                          placeholder="e.g. make pacing emotional, add dramatic zooms, keep final scene longer..."
                          className="bg-black/20 border-border/50 font-mono text-xs h-9 flex-1"
                          disabled={isChatting}
                        />
                        <Button
                          onClick={() => handleChat()}
                          disabled={isChatting || !chatInput.trim()}
                          size="icon"
                          className="h-9 w-9 flex-shrink-0 bg-primary hover:bg-primary/90"
                        >
                          {isChatting ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : (
                            <Send className="w-4 h-4" />
                          )}
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                </motion.div>
              )}
            </AnimatePresence>

          </div>

          {/* Right Sidebar */}
          <div className="lg:col-span-4 space-y-5">

            {/* Minimal settings — only show when no audio */}
            {!audio && (
              <Card className="border-border/50 bg-card/50 backdrop-blur">
                <CardHeader className="pb-3">
                  <CardTitle className="font-mono text-xs tracking-widest text-muted-foreground flex items-center gap-2">
                    <Settings2 className="w-4 h-4" />
                    HOLD PER FRAME
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  <p className="text-[10px] text-muted-foreground font-mono">
                    Duration per scene when no audio is uploaded. AI uses this as baseline.
                  </p>
                  <div className="flex items-center gap-3">
                    <input
                      type="number"
                      min={2}
                      max={12}
                      step={0.5}
                      value={perImageDuration}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setPerImageDuration(Number(e.target.value))}
                      className="flex h-9 w-full rounded-md border border-input bg-black/20 px-3 py-1 text-sm font-mono ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    />
                    <span className="text-xs font-mono text-muted-foreground whitespace-nowrap">SEC</span>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* AI system status */}
            {audio && !aiPlan && !isPreAnalyzing && (
              <Card className="border-border/40 bg-card/30">
                <CardContent className="p-4 flex items-start gap-3">
                  <Zap className="w-4 h-4 text-primary mt-0.5 flex-shrink-0" />
                  <div className="space-y-1">
                    <p className="text-xs font-mono text-primary font-medium">AI MODE ACTIVE</p>
                    <p className="text-[10px] text-muted-foreground font-mono leading-relaxed">
                      Gemini AI will automatically handle pacing, motion, transitions, and audio sync. No manual settings needed.
                    </p>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Quality selector — always visible */}
            <Card className="border-border/50 bg-card/50 backdrop-blur">
              <CardHeader className="pb-3">
                <CardTitle className="font-mono text-xs tracking-widest text-muted-foreground flex items-center gap-2">
                  <Settings2 className="w-4 h-4" />
                  OUTPUT QUALITY
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-[10px] text-muted-foreground font-mono">
                  Lower quality = less RAM = more stable on free servers.
                </p>
                <div className="grid grid-cols-3 gap-2">
                  {(["480p", "720p", "1080p"] as const).map((q) => (
                    <button
                      key={q}
                      onClick={() => setQuality(q)}
                      className={[
                        "h-10 rounded-md border font-mono text-xs tracking-widest transition-all",
                        quality === q
                          ? "border-primary bg-primary/10 text-primary font-bold"
                          : "border-border/50 bg-black/20 text-muted-foreground hover:border-border hover:text-foreground",
                      ].join(" ")}
                    >
                      {q}
                      {q === "720p" && (
                        <span className="block text-[8px] opacity-60 mt-0.5">recommended</span>
                      )}
                      {q === "480p" && (
                        <span className="block text-[8px] opacity-60 mt-0.5">fastest</span>
                      )}
                      {q === "1080p" && (
                        <span className="block text-[8px] opacity-60 mt-0.5">high quality</span>
                      )}
                    </button>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* Analyze button — when images loaded but no plan yet */}
            {canAnalyze && !audio && !aiPlan && (
              <Button
                onClick={handleAnalyze}
                disabled={isPreAnalyzing}
                className="w-full h-12 font-mono text-sm tracking-widest bg-secondary hover:bg-secondary/80 text-secondary-foreground border border-border/50"
              >
                {isPreAnalyzing ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    ANALYZING...
                  </>
                ) : (
                  <>
                    <Wand2 className="w-4 h-4 mr-2" />
                    GENERATE PLAN
                  </>
                )}
              </Button>
            )}

            {/* Main action button */}
            <Button
              onClick={aiPlan ? handleGenerate : canAnalyze ? handleAnalyze : handleGenerate}
              disabled={
                images.length === 0 ||
                !sessionId ||
                isWorking ||
                isPreAnalyzing ||
                status === "done"
              }
              className="w-full h-14 font-mono text-base tracking-widest bg-primary hover:bg-primary/90 text-primary-foreground transition-all shadow-[0_0_20px_rgba(234,179,8,0.15)] hover:shadow-[0_0_35px_rgba(234,179,8,0.3)] disabled:opacity-50 disabled:shadow-none relative overflow-hidden group"
            >
              {isWorking ? (
                <>
                  <Loader2 className="w-5 h-5 mr-2 animate-spin" />
                  RENDERING...
                </>
              ) : isPreAnalyzing ? (
                <>
                  <Wand2 className="w-5 h-5 mr-2 animate-pulse" />
                  ANALYZING...
                </>
              ) : aiPlan ? (
                <>
                  <Play className="w-5 h-5 mr-2 fill-current" />
                  EXECUTE RENDER
                </>
              ) : (
                <>
                  <Wand2 className="w-5 h-5 mr-2" />
                  {audio ? "AI DIRECTING..." : "GENERATE PLAN"}
                </>
              )}
              <div className="absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/15 to-transparent group-hover:animate-[shimmer_2s_infinite]" />
            </Button>

            {/* Plan summary card when ready */}
            {aiPlan && status === "plan_ready" && (
              <motion.div initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }}>
                <Card className="border-primary/20 bg-primary/5">
                  <CardContent className="p-4 space-y-2">
                    <p className="text-[10px] font-mono text-primary uppercase tracking-widest font-bold">Plan Ready</p>
                    <div className="grid grid-cols-2 gap-2 text-[10px] font-mono">
                      <div className="space-y-1">
                        <p className="text-muted-foreground">SCENES</p>
                        <p className="text-foreground font-bold">{aiPlan.scenes.length}</p>
                      </div>
                      <div className="space-y-1">
                        <p className="text-muted-foreground">TOTAL DURATION</p>
                        <p className="text-foreground font-bold">
                          {audio?.duration ? `${audio.duration.toFixed(0)}s` : `~${(aiPlan.scenes.reduce((a, s) => a + s.duration, 0)).toFixed(0)}s`}
                        </p>
                      </div>
                      <div className="space-y-1">
                        <p className="text-muted-foreground">PACING</p>
                        <p className="text-foreground font-bold uppercase">{aiPlan.pacing}</p>
                      </div>
                      <div className="space-y-1">
                        <p className="text-muted-foreground">ZOOM</p>
                        <p className="text-foreground font-bold uppercase">{aiPlan.zoom_intensity || "medium"}</p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </motion.div>
            )}

            {/* Render progress */}
            <AnimatePresence mode="wait">
              {isWorking && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  className="bg-black/40 border border-primary/20 rounded-lg p-4 space-y-3"
                >
                  <div className="flex justify-between items-center text-xs font-mono">
                    <span className="text-primary/80 uppercase tracking-wider font-bold animate-pulse text-[10px]">
                      {statusMessage || "INITIALIZING..."}
                    </span>
                    <span className="text-primary font-bold">{Math.round(progress)}%</span>
                  </div>
                  <Progress value={progress} className="h-1.5 bg-primary/10" />
                  <p className="text-[9px] font-mono text-muted-foreground/60">
                    FFmpeg cinematic render in progress — {images.length} scenes
                  </p>
                </motion.div>
              )}

              {status === "done" && jobId && (
                <motion.div
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  className="bg-primary/10 border border-primary/30 rounded-lg p-5 flex flex-col items-center text-center space-y-3"
                >
                  <CheckCircle2 className="w-10 h-10 text-primary" />
                  <div>
                    <h3 className="font-mono text-base text-primary font-bold">RENDER COMPLETE</h3>
                    <p className="text-[10px] text-muted-foreground font-mono mt-1">JOB: {jobId.substring(0, 8)}</p>
                  </div>
                  <Button asChild className="w-full font-mono" variant="secondary">
                    <a href={`${API_BASE_URL}/video-api/download/${jobId}`} download>
                      <Download className="w-4 h-4 mr-2" />
                      DOWNLOAD MASTER
                    </a>
                  </Button>
                  <button
                    onClick={() => {
                      setStatus("idle");
                      setJobId(null);
                      setProgress(0);
                    }}
                    className="text-[10px] font-mono text-muted-foreground hover:text-foreground transition-colors"
                  >
                    Start new render
                  </button>
                </motion.div>
              )}

              {status === "error" && (
                <motion.div
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  className="bg-destructive/10 border border-destructive/30 rounded-lg p-5 flex flex-col items-center text-center space-y-2"
                >
                  <AlertCircle className="w-8 h-8 text-destructive" />
                  <h3 className="font-mono text-sm text-destructive font-bold">RENDER FAILED</h3>
                  <p className="text-xs text-muted-foreground text-center">{statusMessage}</p>
                  <Button variant="outline" size="sm" className="mt-2 font-mono w-full" onClick={() => setStatus(aiPlan ? "plan_ready" : "idle")}>
                    ACKNOWLEDGE
                  </Button>
                </motion.div>
              )}
            </AnimatePresence>

          </div>
        </div>
      </div>
    </div>
  );
}
