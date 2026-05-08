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
  Wand2
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/hooks/use-toast";
import { Progress } from "@/components/ui/progress";

type GenerateStatus = "idle" | "uploading" | "analyzing" | "plan_ready" | "queued" | "processing" | "done" | "error";

interface AudioState {
  id: string;
  path: string;
  duration: number | null;
  name: string;
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
  scenes: ScenePlan[];
}

const MOTION_LABELS: Record<string, string> = {
  slow_push_in: "Slow Push In",
  slow_pull_back: "Slow Pull Back",
  drift_left: "Drift Left",
  drift_right: "Drift Right",
  dramatic_push: "Dramatic Push",
  arc_left: "Arc Left",
  arc_right: "Arc Right",
  static_breathe: "Static Breathe"
};

export default function Home() {
  const { toast } = useToast();
  
  // State
  const [images, setImages] = useState<File[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [uploadedPaths, setUploadedPaths] = useState<string[]>([]);
  const [sceneNames, setSceneNames] = useState<string[]>([]);
  
  const [audio, setAudio] = useState<AudioState | null>(null);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  
  const [zoomStyle, setZoomStyle] = useState<string>("mixed");
  const [transitionDuration, setTransitionDuration] = useState<number>(1.0);
  const [perImageDuration, setPerImageDuration] = useState<number>(4.0);
  
  const [status, setStatus] = useState<GenerateStatus>("idle");
  const [progress, setProgress] = useState(0);
  const [statusMessage, setStatusMessage] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  
  const [aiPlan, setAiPlan] = useState<AIPlan | null>(null);

  const imageInputRef = useRef<HTMLInputElement>(null);
  const audioInputRef = useRef<HTMLInputElement>(null);

  const handleImageSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files?.length) return;
    const files = Array.from(e.target.files);
    
    const validFiles = files.filter(f => f.type.startsWith('image/'));
    if (validFiles.length === 0) return;

    setImages(prev => [...prev, ...validFiles]);
    
    const formData = new FormData();
    validFiles.forEach(f => formData.append("images", f));
    
    try {
      const res = await fetch("/video-api/upload/images", {
        method: "POST",
        body: formData
      });
      if (!res.ok) throw new Error("Upload failed");
      const data = await res.json();
      setSessionId(data.session_id);
      setUploadedPaths(data.paths);
      if (data.scene_names) {
        setSceneNames(data.scene_names);
      }
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Image Upload Failed",
        description: "Could not upload images to server."
      });
    }
  };

  const handleAudioSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files?.length) return;
    const file = e.target.files[0];
    if (!file.type.startsWith('audio/')) return;
    
    setAudioFile(file);
    
    const formData = new FormData();
    formData.append("audio", file);
    
    try {
      const res = await fetch("/video-api/upload/audio", {
        method: "POST",
        body: formData
      });
      if (!res.ok) throw new Error("Audio upload failed");
      const data = await res.json();
      setAudio({
        id: data.audio_id,
        path: data.path,
        duration: data.duration,
        name: file.name
      });
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Audio Upload Failed",
        description: "Could not upload audio to server."
      });
      setAudioFile(null);
    }
  };

  const removeImage = (index: number) => {
    setImages(prev => prev.filter((_, i) => i !== index));
    setSceneNames(prev => prev.filter((_, i) => i !== index));
    // Need to clear plan if images change
    setAiPlan(null);
    if (status === "plan_ready") setStatus("idle");
  };

  const removeAudio = () => {
    setAudioFile(null);
    setAudio(null);
    setAiPlan(null);
    if (status === "plan_ready") setStatus("idle");
  };

  const handleAnalyze = async () => {
    if (!sessionId || !audio) return;
    
    setStatus("analyzing");
    setAiPlan(null);
    
    try {
      const res = await fetch("/video-api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          audio_path: audio.path,
          audio_duration: audio.duration
        })
      });
      
      if (!res.ok) throw new Error("Analysis failed");
      const data = await res.json();
      setAiPlan(data.plan);
      if (data.scene_names) setSceneNames(data.scene_names);
      setStatus("plan_ready");
    } catch (err) {
      setStatus("idle");
      toast({
        variant: "destructive",
        title: "Analysis Failed",
        description: "AI Director could not process the voiceover."
      });
    }
  };

  // Auto-analyze when both are ready
  useEffect(() => {
    if (sessionId && audio && audio.duration && !aiPlan && status === "idle") {
      handleAnalyze();
    }
  }, [sessionId, audio, aiPlan, status]);

  const handleGenerate = async () => {
    if (!sessionId || images.length === 0) return;
    
    setStatus("processing");
    setProgress(0);
    setStatusMessage("Initializing render pipeline...");

    try {
      const res = await fetch("/video-api/generate", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          session_id: sessionId,
          audio_path: audio?.path || null,
          audio_duration: audio?.duration || null,
          zoom_style: zoomStyle,
          transition_duration: transitionDuration,
          per_image_duration: perImageDuration,
          ai_plan: aiPlan
        })
      });

      if (!res.ok) throw new Error("Generation failed to start");
      const data = await res.json();
      setJobId(data.job_id);
    } catch (err) {
      setStatus("error");
      toast({
        variant: "destructive",
        title: "Generation Failed",
        description: "Failed to initialize video generation."
      });
    }
  };

  useEffect(() => {
    let interval: NodeJS.Timeout;
    
    if (jobId && (status === "processing" || status === "queued" || status === "analyzing")) {
      interval = setInterval(async () => {
        try {
          const res = await fetch(`/video-api/status/${jobId}`);
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
            toast({
              variant: "destructive",
              title: "Render Error",
              description: data.message || "An unknown error occurred during rendering."
            });
          } else {
            setStatus(data.status);
          }
        } catch (err) {
          // Ignore transient network errors during polling
        }
      }, 1500);
    }

    return () => {
      if (interval) clearInterval(interval);
    };
  }, [jobId, status, toast]);

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col p-6 md:p-12 selection:bg-primary/30">
      <div className="max-w-5xl w-full mx-auto space-y-8">
        
        {/* Header */}
        <header className="space-y-2">
          <h1 className="text-4xl font-mono font-bold tracking-tight text-primary flex items-center gap-3">
            <Settings2 className="w-8 h-8" />
            DIRECTOR'S CUT
          </h1>
          <p className="text-muted-foreground font-mono text-sm max-w-xl">
            Cinematic AI Video Generator. Load sequences. Set cadence. Render.
          </p>
        </header>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
          
          {/* Main Workspace */}
          <div className="lg:col-span-8 space-y-6">
            
            {/* Images */}
            <Card className="border-border/50 bg-card/50 backdrop-blur">
              <CardHeader className="pb-4 flex flex-row items-center justify-between">
                <CardTitle className="font-mono text-sm tracking-widest text-muted-foreground flex items-center gap-2">
                  <ImageIcon className="w-4 h-4" />
                  SEQUENCE FRAMES
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div 
                  className="border-2 border-dashed border-border/50 rounded-lg p-8 flex flex-col items-center justify-center text-center cursor-pointer hover:bg-white/5 hover:border-primary/50 transition-colors"
                  onClick={() => imageInputRef.current?.click()}
                  data-testid="dropzone-images"
                >
                  <UploadCloud className="w-10 h-10 text-muted-foreground mb-4" />
                  <p className="text-sm font-medium mb-1">Click to upload images</p>
                  <p className="text-xs text-muted-foreground">JPG, PNG, WEBP</p>
                  <p className="text-[10px] text-muted-foreground/70 font-mono mt-4">
                    Name files scene1.png, scene2.png… for automatic ordering
                  </p>
                  <input 
                    type="file" 
                    multiple 
                    accept="image/*" 
                    className="hidden" 
                    ref={imageInputRef} 
                    onChange={handleImageSelect}
                    data-testid="input-images"
                  />
                </div>

                {images.length > 0 && (
                  <div className="mt-6 space-y-3">
                    <div className="flex justify-between items-center text-xs font-mono text-muted-foreground">
                      <span>{images.length} FRAMES LOADED</span>
                    </div>
                    <div className="grid grid-cols-4 sm:grid-cols-6 gap-3">
                      {images.map((file, i) => (
                        <div key={i} className="flex flex-col gap-1 group">
                          <div className="relative aspect-video rounded-md overflow-hidden bg-muted">
                            <img 
                              src={URL.createObjectURL(file)} 
                              alt={`Frame ${i}`} 
                              className="object-cover w-full h-full opacity-80 group-hover:opacity-100 transition-opacity"
                            />
                            <button 
                              className="absolute top-1 right-1 bg-black/50 p-1 rounded-sm opacity-0 group-hover:opacity-100 hover:bg-destructive/80 transition-all"
                              onClick={(e) => { e.stopPropagation(); removeImage(i); }}
                              data-testid={`button-remove-image-${i}`}
                            >
                              <X className="w-3 h-3 text-white" />
                            </button>
                          </div>
                          {sceneNames[i] && (
                            <span className="text-[10px] font-mono text-muted-foreground truncate w-full text-center">
                              {sceneNames[i]}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Audio */}
            <Card className="border-border/50 bg-card/50 backdrop-blur">
              <CardHeader className="pb-4">
                <CardTitle className="font-mono text-sm tracking-widest text-muted-foreground flex items-center gap-2">
                  <Music className="w-4 h-4" />
                  MASTER AUDIO
                </CardTitle>
              </CardHeader>
              <CardContent>
                {!audioFile ? (
                  <div 
                    className="border-2 border-dashed border-border/50 rounded-lg p-6 flex flex-col items-center justify-center text-center cursor-pointer hover:bg-white/5 hover:border-primary/50 transition-colors"
                    onClick={() => audioInputRef.current?.click()}
                    data-testid="dropzone-audio"
                  >
                    <UploadCloud className="w-8 h-8 text-muted-foreground mb-3" />
                    <p className="text-sm font-medium mb-1">Click to upload voiceover or score</p>
                    <p className="text-xs text-muted-foreground">MP3, WAV, AAC (Optional)</p>
                    <input 
                      type="file" 
                      accept="audio/*" 
                      className="hidden" 
                      ref={audioInputRef} 
                      onChange={handleAudioSelect}
                      data-testid="input-audio"
                    />
                  </div>
                ) : (
                  <div className="flex items-center justify-between p-4 border border-border/50 rounded-lg bg-black/20">
                    <div className="flex items-center gap-4">
                      <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center">
                        <Music className="w-5 h-5 text-primary" />
                      </div>
                      <div>
                        <p className="text-sm font-medium">{audioFile.name}</p>
                        <p className="text-xs text-muted-foreground font-mono">
                          {audio?.duration ? `${audio.duration.toFixed(1)}s` : 'Processing...'}
                        </p>
                      </div>
                    </div>
                    <Button 
                      variant="ghost" 
                      size="icon" 
                      onClick={removeAudio}
                      className="text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                      data-testid="button-remove-audio"
                    >
                      <X className="w-4 h-4" />
                    </Button>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* AI Director Panel */}
            <AnimatePresence>
              {aiPlan && status !== "analyzing" && (
                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -20 }}
                  transition={{ duration: 0.4 }}
                >
                  <Card className="border-primary/30 bg-card/80 backdrop-blur shadow-[0_0_15px_rgba(234,179,8,0.05)] overflow-hidden">
                    <div className="bg-primary/10 border-b border-primary/20 px-6 py-3 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Clapperboard className="w-4 h-4 text-primary" />
                        <span className="font-mono text-sm tracking-widest text-primary font-bold">AI DIRECTOR'S BRIEF</span>
                      </div>
                      <div className="flex items-center gap-2 font-mono text-[10px]">
                        <span className="bg-background px-2 py-1 rounded-sm border border-border uppercase">
                          {aiPlan.pacing} PACING
                        </span>
                        <span className="bg-background px-2 py-1 rounded-sm border border-border uppercase">
                          {aiPlan.mood} MOOD
                        </span>
                      </div>
                    </div>
                    <CardContent className="p-0">
                      <div className="divide-y divide-border/30">
                        {aiPlan.scenes.map((scene, idx) => (
                          <div key={idx} className="p-4 hover:bg-white/5 transition-colors flex items-center gap-4 group">
                            <div className="flex-shrink-0 w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center border border-primary/30 text-primary font-mono text-xs font-bold">
                              {scene.scene_number}
                            </div>
                            
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2 mb-1">
                                <span className="text-sm font-medium truncate">
                                  {sceneNames[idx] || `Scene ${scene.scene_number}`}
                                </span>
                                <span className="text-[10px] font-mono text-muted-foreground bg-black/30 px-1.5 py-0.5 rounded">
                                  {scene.duration.toFixed(1)}s
                                </span>
                              </div>
                              <p className="text-xs text-muted-foreground truncate" title={scene.direction_note}>
                                {scene.direction_note}
                              </p>
                            </div>

                            <div className="flex items-center gap-3">
                              <div className="flex flex-col items-end gap-1">
                                <span className="text-[10px] uppercase font-mono bg-primary/10 text-primary px-2 py-1 rounded-sm border border-primary/20 whitespace-nowrap">
                                  {MOTION_LABELS[scene.motion] || scene.motion}
                                </span>
                                <span className="text-[10px] uppercase font-mono text-muted-foreground px-1">
                                  {scene.intensity} INTENSITY
                                </span>
                              </div>
                              {idx < aiPlan.scenes.length - 1 && (
                                <div className="text-muted-foreground flex flex-col items-center justify-center opacity-50 group-hover:opacity-100 transition-opacity">
                                  <ArrowRight className="w-4 h-4" />
                                  <span className="text-[9px] font-mono mt-0.5 uppercase">{scene.transition}</span>
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

          </div>

          {/* Right Sidebar - Settings & Render */}
          <div className="lg:col-span-4 space-y-6">
            
            <Card className="border-border/50 bg-card/50 backdrop-blur">
              <CardHeader className="pb-4">
                <CardTitle className="font-mono text-sm tracking-widest text-muted-foreground flex items-center gap-2">
                  <Settings2 className="w-4 h-4" />
                  RENDER SETTINGS
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-6">
                
                <div className="space-y-3">
                  <Label className="text-xs font-mono text-muted-foreground">CAMERA MOVEMENT</Label>
                  <Select value={zoomStyle} onValueChange={setZoomStyle}>
                    <SelectTrigger className="bg-black/20 border-border/50 font-mono text-sm" data-testid="select-zoom">
                      <SelectValue placeholder="Select style" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="mixed">MIXED AUTOMATIC</SelectItem>
                      <SelectItem value="zoom_in_center">ZOOM IN (CENTER)</SelectItem>
                      <SelectItem value="zoom_out_center">ZOOM OUT (CENTER)</SelectItem>
                      <SelectItem value="pan_left">PAN LEFT</SelectItem>
                      <SelectItem value="pan_right">PAN RIGHT</SelectItem>
                    </SelectContent>
                  </Select>
                  {aiPlan && (
                    <p className="text-[10px] text-primary/80 font-mono italic">
                      Overridden by AI Director Plan
                    </p>
                  )}
                </div>

                <div className="space-y-4">
                  <div className="flex justify-between">
                    <Label className="text-xs font-mono text-muted-foreground">TRANSITION CROSSFADE</Label>
                    <span className="text-xs font-mono text-primary">{transitionDuration}s</span>
                  </div>
                  <Slider 
                    value={[transitionDuration]} 
                    min={0.5} 
                    max={2.0} 
                    step={0.1} 
                    onValueChange={(v) => setTransitionDuration(v[0])} 
                    className="py-2"
                    data-testid="slider-transition"
                  />
                  {aiPlan && (
                    <p className="text-[10px] text-primary/80 font-mono italic mt-1">
                      Defaults applied unless AI specifies
                    </p>
                  )}
                </div>

                <div className="space-y-3">
                  <Label className="text-xs font-mono text-muted-foreground">HOLD PER FRAME (NO AUDIO)</Label>
                  <div className="flex items-center gap-3">
                    <Input 
                      type="number" 
                      min={2} 
                      max={10} 
                      step={0.5}
                      value={perImageDuration}
                      onChange={(e) => setPerImageDuration(Number(e.target.value))}
                      className="bg-black/20 border-border/50 font-mono text-sm"
                      data-testid="input-duration"
                    />
                    <span className="text-xs font-mono text-muted-foreground">SECONDS</span>
                  </div>
                </div>

              </CardContent>
            </Card>

            {/* Action Block */}
            <div className="space-y-4">
              <Button 
                onClick={status === "idle" && audio ? handleAnalyze : handleGenerate} 
                disabled={images.length === 0 || !sessionId || status === "processing" || status === "queued" || status === "analyzing"}
                className="w-full h-14 font-mono text-base tracking-widest bg-primary hover:bg-primary/90 text-primary-foreground transition-all shadow-[0_0_20px_rgba(234,179,8,0.15)] hover:shadow-[0_0_30px_rgba(234,179,8,0.3)] disabled:opacity-50 disabled:shadow-none relative overflow-hidden group"
                data-testid="button-generate"
              >
                {status === "analyzing" && !jobId ? (
                  <>
                    <Wand2 className="w-5 h-5 mr-2 animate-pulse" />
                    AI DIRECTOR IS THINKING...
                  </>
                ) : status === "processing" || status === "queued" || (status === "analyzing" && !!jobId) ? (
                  <>
                    <Loader2 className="w-5 h-5 mr-2 animate-spin" />
                    RENDERING...
                  </>
                ) : status === "plan_ready" || (!audio && status === "idle") ? (
                  <>
                    <Play className="w-5 h-5 mr-2 fill-current" />
                    EXECUTE RENDER
                  </>
                ) : (
                  <>
                    <Wand2 className="w-5 h-5 mr-2" />
                    ANALYZE AUDIO
                  </>
                )}
                
                {/* Shine effect */}
                <div className="absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/20 to-transparent group-hover:animate-[shimmer_1.5s_infinite]" />
              </Button>

              <AnimatePresence mode="wait">
                {(status === "processing" || status === "queued" || (status === "analyzing" && !!jobId)) && (
                  <motion.div 
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    exit={{ opacity: 0, height: 0 }}
                    className="bg-black/40 border border-primary/20 rounded-lg p-5 space-y-4"
                  >
                    <div className="flex justify-between items-center text-xs font-mono">
                      <span className="text-primary/80 uppercase tracking-wider font-bold animate-pulse">{statusMessage || "INITIALIZING..."}</span>
                      <span className="text-primary">{Math.round(progress)}%</span>
                    </div>
                    <Progress value={progress} className="h-1 bg-primary/10" />
                  </motion.div>
                )}

                {status === "done" && jobId && (
                  <motion.div 
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    className="bg-primary/10 border border-primary/30 rounded-lg p-6 flex flex-col items-center text-center space-y-4"
                  >
                    <CheckCircle2 className="w-10 h-10 text-primary mb-2" />
                    <div>
                      <h3 className="font-mono text-lg text-primary font-bold">RENDER COMPLETE</h3>
                      <p className="text-xs text-muted-foreground font-mono mt-1">JOB ID: {jobId.substring(0,8)}</p>
                    </div>
                    <Button asChild className="w-full mt-4 font-mono" variant="secondary" data-testid="button-download">
                      <a href={`/video-api/download/${jobId}`} download>
                        <Download className="w-4 h-4 mr-2" />
                        DOWNLOAD MASTER
                      </a>
                    </Button>
                  </motion.div>
                )}

                {status === "error" && (
                  <motion.div 
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    className="bg-destructive/10 border border-destructive/30 rounded-lg p-6 flex flex-col items-center text-center space-y-2"
                  >
                    <AlertCircle className="w-8 h-8 text-destructive mb-2" />
                    <h3 className="font-mono text-sm text-destructive font-bold">RENDER FAILED</h3>
                    <p className="text-xs text-muted-foreground text-center">{statusMessage}</p>
                    <Button variant="outline" size="sm" className="mt-4 font-mono w-full" onClick={() => setStatus("idle")}>
                      ACKNOWLEDGE
                    </Button>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

          </div>
        </div>
      </div>
    </div>
  );
}