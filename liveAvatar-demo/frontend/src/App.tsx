import { useEffect, useRef, useState } from 'react';
import { LiveAvatarSession, SessionEvent, AgentEventsEnum } from '@heygen/liveavatar-web-sdk';
import { Mic, MicOff, Play, Square, Loader2, Video, VideoOff, SignalHigh, SignalMedium, SignalLow, SignalZero, Upload, X } from 'lucide-react';

const API_URL = import.meta.env.PROD ? '' : (import.meta.env.VITE_API_URL || 'http://localhost:3001');

type SessionStatus = 'disconnected' | 'connecting' | 'connected';
type SpeakingState = 'idle' | 'user_speaking' | 'avatar_speaking' | 'processing';
type NetworkQuality = 'excellent' | 'good' | 'poor' | 'unknown';

function App() {
  const [session, setSession] = useState<LiveAvatarSession | null>(null);
  const [status, setStatus] = useState<SessionStatus>('disconnected');
  const [speakingState, setSpeakingState] = useState<SpeakingState>('idle');
  const [error, setError] = useState<string | null>(null);
  
  const [micEnabled, setMicEnabled] = useState(false);
  const [cameraEnabled, setCameraEnabled] = useState(true);
  
  const [sessionDuration, setSessionDuration] = useState(0);
  const [networkQuality, setNetworkQuality] = useState<NetworkQuality>('unknown');
  
  const [files, setFiles] = useState<File[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  
  const videoRef = useRef<HTMLVideoElement>(null);
  const localVideoRef = useRef<HTMLVideoElement>(null);
  const localStreamRef = useRef<MediaStream | null>(null);

  useEffect(() => {
      // @ts-ignore
      const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
      if (conn) {
          const updateQuality = () => {
              if (conn.rtt === 0) setNetworkQuality('unknown');
              else if (conn.rtt < 100 && conn.downlink > 2) setNetworkQuality('excellent');
              else if (conn.rtt < 300 && conn.downlink > 1) setNetworkQuality('good');
              else setNetworkQuality('poor');
          };
          updateQuality();
          conn.addEventListener('change', updateQuality);
          return () => conn.removeEventListener('change', updateQuality);
      }
  }, []);

  const NetworkIcon = () => {
      if (networkQuality === 'excellent') return <SignalHigh className="w-4 h-4 text-emerald-500" />;
      if (networkQuality === 'good') return <SignalMedium className="w-4 h-4 text-amber-500" />;
      if (networkQuality === 'poor') return <SignalLow className="w-4 h-4 text-rose-500" />;
      return <SignalZero className="w-4 h-4 text-slate-500" />;
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files) {
          const newFiles = Array.from(e.target.files);
          if (files.length + newFiles.length > 5) {
              setError("You can only upload up to 5 files.");
              return;
          }
          
          for (const file of newFiles) {
              if (file.size > 5 * 1024 * 1024) {
                  setError(`File ${file.name} is larger than 5MB.`);
                  return;
              }
              const ext = file.name.split('.').pop()?.toLowerCase();
              if (!['pdf', 'docx', 'txt'].includes(ext || '')) {
                  setError(`File ${file.name} is not supported. Use PDF, DOCX, or TXT.`);
                  return;
              }
          }
          
          setError(null);
          setFiles(prev => [...prev, ...newFiles]);
      }
  };

  const removeFile = (index: number) => {
      setFiles(prev => prev.filter((_, i) => i !== index));
  };

  const startSession = async () => {
    try {
      setStatus('connecting');
      setError(null);
      setIsUploading(true);
      
      let currentContextId = import.meta.env.VITE_CONTEXT_ID;

      if (files.length > 0) {
          const formData = new FormData();
          files.forEach(file => formData.append('files', file));
          
          const uploadRes = await fetch(`${API_URL}/api/upload-resume`, {
              method: 'POST',
              body: formData
          });
          
          if (!uploadRes.ok) {
              const errData = await uploadRes.json();
              throw new Error(errData.detail || 'Failed to upload documents');
          }
          
          const uploadData = await uploadRes.json();
          currentContextId = uploadData.context_id;
      }
      setIsUploading(false);

      const response = await fetch(`${API_URL}/api/session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          context_id: currentContextId,
          llm_configuration_id: import.meta.env.VITE_LLM_CONFIG_ID,
        }),
      });

      if (!response.ok) throw new Error('Failed to create session on backend');

      const { session_token } = await response.json();
      localStorage.setItem('liveavatar_session_token', session_token);

      const newSession = new LiveAvatarSession(session_token);

      newSession.on(SessionEvent.SESSION_STREAM_READY, async () => {
        setStatus('connected');
        if (videoRef.current) newSession.attach(videoRef.current);
        
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ video: true });
            localStreamRef.current = stream;
            setCameraEnabled(true);
            if (localVideoRef.current) localVideoRef.current.srcObject = stream;
        } catch (e) {
            console.error("Failed to access local camera:", e);
            setCameraEnabled(false);
        }
      });

      newSession.on(SessionEvent.SESSION_DISCONNECTED, () => cleanupSession(newSession));
      
      newSession.on(AgentEventsEnum.AVATAR_SPEAK_STARTED, () => setSpeakingState('avatar_speaking'));
      newSession.on(AgentEventsEnum.AVATAR_SPEAK_ENDED, () => setSpeakingState('idle'));
      newSession.on(AgentEventsEnum.USER_SPEAK_STARTED, () => setSpeakingState('user_speaking'));
      newSession.on(AgentEventsEnum.USER_SPEAK_ENDED, () => setSpeakingState('processing'));

      await newSession.start();
      setSession(newSession);
      
    } catch (err: any) {
      console.error(err);
      setError(err.message || 'An error occurred connecting to the avatar.');
      setStatus('disconnected');
    }
  };

  const stopSession = async () => {
    if (session) {
      try { await session.stop(); } catch (e) { console.error("Error stopping session:", e); }
      cleanupSession(session);
    }
  };

  const cleanupSession = (s: LiveAvatarSession) => {
    s.removeAllListeners();
    setSession(null);
    setStatus('disconnected');
    setSpeakingState('idle');
    setMicEnabled(false);
    setCameraEnabled(true);
    localStorage.removeItem('liveavatar_session_token');
    
    if (localStreamRef.current) {
        localStreamRef.current.getTracks().forEach(track => track.stop());
        localStreamRef.current = null;
    }
  };

  const toggleMic = async () => {
      if (!session) return;
      try {
          if (micEnabled) { 
              await session.voiceChat.stop(); 
              setMicEnabled(false);
          } else { 
              await session.voiceChat.start(); 
              setMicEnabled(true);
          }
      } catch (e) { console.error("Failed to toggle mic:", e); }
  };

  const toggleCamera = () => {
      if (localStreamRef.current) {
          const videoTrack = localStreamRef.current.getVideoTracks()[0];
          if (videoTrack) {
              videoTrack.enabled = !videoTrack.enabled;
              setCameraEnabled(videoTrack.enabled);
          }
      }
  };

  useEffect(() => {
      const orphanedToken = localStorage.getItem('liveavatar_session_token');
      if (orphanedToken) {
          fetch(`${API_URL}/api/session/stop`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ session_token: orphanedToken })
          }).catch(console.error).finally(() => localStorage.removeItem('liveavatar_session_token'));
      }
  }, []);

  useEffect(() => {
      const handleBeforeUnload = () => {
          const activeToken = localStorage.getItem('liveavatar_session_token');
          if (activeToken) {
              fetch(`${API_URL}/api/session/stop`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ session_token: activeToken }),
                  keepalive: true
              });
          }
      };
      window.addEventListener('beforeunload', handleBeforeUnload);
      return () => {
          window.removeEventListener('beforeunload', handleBeforeUnload);
          if (session) {
              session.stop().catch(console.error);
              const activeToken = localStorage.getItem('liveavatar_session_token');
              if (activeToken) {
                  fetch(`${API_URL}/api/session/stop`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ session_token: activeToken })
                  }).catch(console.error);
              }
          }
      };
  }, [session]);

  useEffect(() => {
      let interval: ReturnType<typeof setInterval>;
      if (status === 'connected') {
          interval = setInterval(() => setSessionDuration(prev => prev + 1), 1000);
      } else {
          setSessionDuration(0);
      }
      return () => clearInterval(interval);
  }, [status]);

  const formatTime = (seconds: number) => {
      const m = Math.floor(seconds / 60).toString().padStart(2, '0');
      const s = (seconds % 60).toString().padStart(2, '0');
      return `${m}:${s}`;
  };

  return (
    <div className="min-h-screen bg-slate-950 text-white flex flex-col md:flex-row overflow-hidden">
        
        {/* Left Sidebar for Documents */}
        <div className="w-full md:w-80 flex flex-col border-b md:border-b-0 md:border-r border-slate-800 bg-slate-900/40 shrink-0 z-10 shadow-2xl">
            <div className="p-6 border-b border-slate-800 flex justify-between items-center bg-slate-900/80 backdrop-blur-md shrink-0">
                <h2 className="text-base font-semibold text-slate-200 tracking-wide">Context Documents</h2>
                <span className="text-xs font-medium px-2 py-1 bg-slate-800 rounded-full text-slate-400">{files.length}/5</span>
            </div>
            
            <div className="flex-1 overflow-y-auto p-6 flex flex-col gap-4">
                {files.length === 0 ? (
                    <div className="flex-1 flex flex-col items-center justify-center text-slate-500 text-center text-sm p-8 border-2 border-dashed border-slate-800 rounded-2xl bg-slate-900/20">
                        <Upload className="w-10 h-10 mb-4 opacity-20" />
                        <p className="font-medium text-slate-400">No documents yet</p>
                        <p className="mt-2 text-xs opacity-70 leading-relaxed max-w-[200px]">Drop resumes or portfolios here for the AI to reference during the interview.</p>
                    </div>
                ) : (
                    files.map((file, idx) => (
                        <div key={idx} className="flex justify-between items-center bg-slate-800/50 p-4 rounded-xl border border-slate-700/50 group transition-all hover:border-slate-600 hover:bg-slate-800 shadow-sm">
                            <div className="flex flex-col overflow-hidden mr-3">
                                <span className="text-sm text-slate-200 truncate font-medium">{file.name}</span>
                                <span className="text-xs text-slate-500 mt-0.5">{(file.size / 1024 / 1024).toFixed(1)} MB</span>
                            </div>
                            <button onClick={() => removeFile(idx)} disabled={status !== 'disconnected'} className="p-2 text-slate-500 hover:text-rose-400 hover:bg-rose-500/10 rounded-lg transition-colors disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-slate-500 shrink-0">
                                <X className="w-4 h-4" />
                            </button>
                        </div>
                    ))
                )}
            </div>

            {status === 'disconnected' && files.length < 5 && (
                <div className="p-6 border-t border-slate-800 bg-slate-900/60 backdrop-blur-md shrink-0">
                    <label className="flex items-center justify-center gap-2 bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 px-4 py-4 rounded-xl cursor-pointer transition-all border border-indigo-500/30 hover:border-indigo-500/50 hover:shadow-lg hover:shadow-indigo-500/10 group">
                        <Upload className="w-5 h-5 group-hover:-translate-y-0.5 transition-transform" />
                        <span className="text-sm font-semibold tracking-wide">Upload File</span>
                        <input type="file" multiple accept=".pdf,.docx,.txt" className="hidden" onChange={handleFileChange} disabled={status !== 'disconnected'} />
                    </label>
                </div>
            )}
        </div>

        {/* Main Content Area */}
        <div className="flex-1 flex flex-col h-screen overflow-hidden bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-slate-900 via-slate-950 to-black">
            {/* Header */}
            <div className="p-4 md:px-8 md:py-6 flex justify-between items-center shrink-0">
                <div className="flex items-center gap-4">
                    <h1 className="text-xl md:text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-white to-slate-400">Technical Interview</h1>
                    <div title={`Network Quality: ${networkQuality}`} className="cursor-help flex items-center bg-slate-800/50 backdrop-blur-md px-2.5 py-1.5 rounded-lg border border-slate-700/50 shadow-sm">
                        <NetworkIcon />
                    </div>
                </div>
                <div className="flex items-center gap-4">
                    {status === 'connected' && (
                        <span className="font-mono text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-3 py-1.5 rounded-lg text-sm font-semibold tracking-wider shadow-inner">{formatTime(sessionDuration)}</span>
                    )}
                    <div className="flex items-center gap-2.5 bg-slate-800/50 backdrop-blur-md px-3 py-1.5 rounded-lg border border-slate-700/50">
                        <div className={`w-2.5 h-2.5 rounded-full shadow-sm ${status === 'connected' ? 'bg-emerald-500 shadow-emerald-500/50' : status === 'connecting' ? 'bg-amber-500 animate-pulse shadow-amber-500/50' : 'bg-rose-500 shadow-rose-500/50'}`} />
                        <span className="text-xs font-semibold uppercase tracking-wider text-slate-300">{status}</span>
                    </div>
                </div>
            </div>

            {/* Video Area */}
            <div className="flex-1 relative flex overflow-hidden p-4 md:px-8 pb-4">
                <div className={`w-full h-full flex flex-col md:flex-row items-stretch justify-center gap-4 transition-all duration-500`}>
                  
                      {/* Avatar Video */}
                      <div className={`flex-1 relative flex items-center justify-center rounded-3xl overflow-hidden shadow-2xl transition-all duration-300 ${
                          status === 'disconnected' ? 'bg-slate-900/50 border border-slate-800' : 'bg-black'
                      } ${
                          status === 'connected' && speakingState === 'avatar_speaking' ? 'ring-2 ring-emerald-500/50 ring-offset-4 ring-offset-slate-950' : 
                          status === 'connected' && speakingState === 'processing' ? 'ring-2 ring-amber-500/50 ring-offset-4 ring-offset-slate-950' : 
                          status === 'connected' ? 'border border-slate-800' : ''
                      }`}>
                          {status === 'disconnected' && (
                             <div className="flex flex-col items-center text-slate-500">
                                 <div className="w-24 h-24 rounded-full bg-slate-800/50 flex items-center justify-center mb-6 shadow-inner">
                                     <Video className="w-10 h-10 opacity-20" />
                                 </div>
                                 <p className="text-lg font-medium text-slate-400">Ready to start</p>
                                 <p className="text-sm mt-2 opacity-60">Waiting for you to begin the interview</p>
                             </div>
                          )}
                          {status === 'connecting' && (
                             <div className="flex flex-col items-center text-slate-400 bg-slate-900/50 w-full h-full justify-center backdrop-blur-sm">
                                 <Loader2 className="w-12 h-12 mb-6 animate-spin text-indigo-500" />
                                 <p className="text-lg font-medium">{isUploading ? 'Uploading context...' : 'Connecting to avatar...'}</p>
                             </div>
                          )}
                          <video ref={videoRef} autoPlay playsInline className={`w-full h-full object-cover ${status === 'connected' ? 'opacity-100' : 'opacity-0 absolute'}`} />
                          {status === 'connected' && (
                              <div className="absolute bottom-6 left-6 bg-slate-950/80 backdrop-blur-md px-4 py-2 rounded-xl text-sm font-semibold text-slate-200 shadow-xl border border-slate-700/50 flex items-center gap-2">
                                  <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                                  AI Interviewer
                              </div>
                          )}
                      </div>

                      {/* Local Candidate Video */}
                      <div className={`flex-1 relative flex items-center justify-center rounded-3xl overflow-hidden shadow-2xl transition-all duration-300 ${
                          status === 'disconnected' ? 'bg-slate-900/50 border border-slate-800' : 'bg-slate-900/20'
                      } ${
                          status === 'connected' && speakingState === 'user_speaking' ? 'ring-2 ring-sky-500/50 ring-offset-4 ring-offset-slate-950' : 
                          status === 'connected' ? 'border border-slate-800' : ''
                      }`}>
                          {status === 'disconnected' ? (
                              <div className="flex flex-col items-center text-slate-500">
                                  <VideoOff className="w-12 h-12 mb-4 opacity-20" />
                                  <p className="font-medium text-slate-400">Camera Preview</p>
                              </div>
                          ) : !cameraEnabled ? (
                              <div className="flex flex-col items-center text-slate-500 bg-slate-900/80 w-full h-full justify-center backdrop-blur-md">
                                  <div className="w-20 h-20 rounded-full bg-slate-800 flex items-center justify-center mb-4">
                                      <VideoOff className="w-8 h-8 opacity-50" />
                                  </div>
                                  <p className="font-medium">Camera is off</p>
                              </div>
                          ) : (
                              <video ref={localVideoRef} autoPlay playsInline muted className={`w-full h-full object-cover scale-x-[-1] ${status === 'connected' ? 'opacity-100' : 'opacity-0 absolute'}`} />
                          )}
                          {status === 'connected' && (
                              <div className="absolute bottom-6 right-6 bg-slate-950/80 backdrop-blur-md px-4 py-2 rounded-xl text-sm font-semibold text-slate-200 shadow-xl border border-slate-700/50 flex items-center gap-2">
                                  You
                                  {micEnabled ? <Mic className="w-3.5 h-3.5 text-emerald-400" /> : <MicOff className="w-3.5 h-3.5 text-rose-400" />}
                              </div>
                          )}
                      </div>
                  
                  {/* VAD Status Indicator overlaid in the center between videos */}
                  {status === 'connected' && (
                      <div className="absolute top-8 left-1/2 -translate-x-1/2 bg-slate-900/90 backdrop-blur-xl px-6 py-3 rounded-2xl flex items-center gap-4 border border-slate-700/50 shadow-2xl z-10">
                          <div className="flex items-end gap-1.5 h-4">
                              <div className={`w-1.5 bg-current rounded-full ${speakingState === 'user_speaking' ? 'text-sky-400 animate-audio-bar-1' : speakingState === 'avatar_speaking' ? 'text-emerald-400 animate-audio-bar-2' : speakingState === 'processing' ? 'text-amber-400 animate-pulse h-2' : 'text-slate-600 h-1'}`} />
                              <div className={`w-1.5 bg-current rounded-full ${speakingState === 'user_speaking' ? 'text-sky-400 animate-audio-bar-2' : speakingState === 'avatar_speaking' ? 'text-emerald-400 animate-audio-bar-3' : speakingState === 'processing' ? 'text-amber-400 animate-pulse h-3' : 'text-slate-600 h-1.5'}`} />
                              <div className={`w-1.5 bg-current rounded-full ${speakingState === 'user_speaking' ? 'text-sky-400 animate-audio-bar-3' : speakingState === 'avatar_speaking' ? 'text-emerald-400 animate-audio-bar-1' : speakingState === 'processing' ? 'text-amber-400 animate-pulse h-2' : 'text-slate-600 h-1'}`} />
                          </div>
                          <span className="text-sm font-semibold tracking-wide text-slate-200">
                              {speakingState === 'user_speaking' ? 'You are speaking' : speakingState === 'avatar_speaking' ? 'Avatar speaking' : speakingState === 'processing' ? 'Thinking...' : 'Listening...'}
                          </span>
                      </div>
                  )}
                </div>
            </div>

            {/* Controls */}
            <div className="p-6 md:px-8 md:pb-8 flex flex-col justify-center items-center shrink-0">
                {status === 'disconnected' ? (
                    <div className="flex flex-col items-center w-full max-w-sm">
                        <button onClick={startSession} disabled={isUploading} className="w-full flex items-center justify-center gap-3 bg-gradient-to-r from-indigo-500 via-sky-500 to-indigo-500 bg-[length:200%_auto] hover:bg-[position:right_center] text-white px-8 py-4 rounded-2xl font-bold transition-all duration-500 shadow-[0_0_40px_-10px_rgba(99,102,241,0.5)] hover:shadow-[0_0_60px_-15px_rgba(99,102,241,0.7)] hover:-translate-y-1 disabled:opacity-50 disabled:hover:translate-y-0 text-lg">
                            {isUploading ? <Loader2 className="w-6 h-6 animate-spin" /> : <Play className="w-6 h-6 ml-1" fill="currentColor" />}
                            {isUploading ? 'Uploading Context...' : 'Start Interview'}
                        </button>
                    </div>
                ) : (
                    <div className="flex items-center justify-center gap-4">
                        {/* Audio/Video Controls */}
                        <div className="flex items-center gap-2 bg-slate-900/80 backdrop-blur-xl p-2 rounded-3xl border border-slate-700/50 shadow-2xl">
                            <button onClick={toggleMic} className={`flex items-center justify-center w-14 h-14 rounded-2xl transition-all ${micEnabled ? 'bg-slate-800 hover:bg-slate-700 text-slate-200' : 'bg-rose-500/20 text-rose-400 hover:bg-rose-500/30'}`} title={micEnabled ? 'Mute Microphone' : 'Unmute Microphone'}>
                                {micEnabled ? <Mic className="w-6 h-6" /> : <MicOff className="w-6 h-6" />}
                            </button>
                            <button onClick={toggleCamera} className={`flex items-center justify-center w-14 h-14 rounded-2xl transition-all ${cameraEnabled ? 'bg-slate-800 hover:bg-slate-700 text-slate-200' : 'bg-rose-500/20 text-rose-400 hover:bg-rose-500/30'}`} title={cameraEnabled ? 'Turn off Camera' : 'Turn on Camera'}>
                                {cameraEnabled ? <Video className="w-6 h-6" /> : <VideoOff className="w-6 h-6" />}
                            </button>
                        </div>

                        {/* End Control */}
                        <button onClick={stopSession} className="flex items-center justify-center w-14 h-14 bg-rose-500 hover:bg-rose-600 text-white rounded-3xl transition-all shadow-lg shadow-rose-500/20 hover:shadow-rose-500/40 hover:-translate-y-0.5" title="End Interview">
                            <Square className="w-5 h-5" fill="currentColor" />
                        </button>
                    </div>
                )}
            </div>
            
            {error && (
                <div className="absolute top-4 left-1/2 -translate-x-1/2 bg-rose-500/90 backdrop-blur-md text-white px-6 py-3 rounded-xl shadow-2xl border border-rose-400/50 z-50 flex items-center gap-3">
                    <X className="w-5 h-5" onClick={() => setError(null)} />
                    <span className="font-medium">{error}</span>
                </div>
            )}
        </div>
    </div>
  );
}

export default App;
