export type SessionStatus = 'disconnected' | 'connecting' | 'connected';
export type SpeakingState = 'idle' | 'user_speaking' | 'avatar_speaking' | 'processing';
export type NetworkQuality = 'excellent' | 'good' | 'poor' | 'unknown';

export type TranscriptRole = 'interviewer' | 'candidate';

export interface TranscriptTurn {
  role: TranscriptRole;
  text: string;
  timestamp: number;
}
