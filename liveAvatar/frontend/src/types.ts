export type SessionStatus = 'disconnected' | 'connecting' | 'connected';
export type SpeakingState = 'idle' | 'user_speaking' | 'avatar_speaking' | 'processing';
export type NetworkQuality = 'excellent' | 'good' | 'poor' | 'unknown';

export type TranscriptRole = 'interviewer' | 'candidate';

export interface TranscriptTurn {
  role: TranscriptRole;
  text: string;
  timestamp: number;
}

// Vendor intake form fields (POSTed as multipart to /api/vendor-profile).
export interface VendorProfile {
  companyName: string;
  website: string;
  contactName: string;
  contactRole: string;
}

export interface VendorProfileResponse {
  interview_id: string;
}

// Live interview state (GET /api/interview/{interviewId}/state). Scores are 0-5.
export interface CategoryScoreData {
  id: string;
  name: string;
  weight: number;
  score: number | null;
  evidence: string[];
}

export interface ScorecardData {
  categories: CategoryScoreData[];
  overall: number | null;
  answered_questions: number;
}

// Scout research insights arrive with the state payload; rendered by a later task.
export interface ScoutFinding {
  topic: string;
  summary: string;
  source_url: string | null;
}

export interface InterviewStateResponse {
  status: 'created' | 'active' | 'finished';
  current_topic: string | null;
  scorecard: ScorecardData;
  insights: ScoutFinding[];
  updated_at: string;
}
