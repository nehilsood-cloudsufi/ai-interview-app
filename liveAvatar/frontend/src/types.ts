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

// Final scorecard from the holistic end-of-interview scoring pass; arrives
// with the finalize response (never during the interview). Scores are 0-5.
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
  insights: ScoutFinding[];
  updated_at: string;
}

// Coordinator follow-up recommendation attached to the finalize response.
export interface FollowupRecommendation {
  kind: 'advance' | 'clarify';
  reason: string;
  focus_categories: string[];
}

export interface FollowupProposal {
  recommendation: FollowupRecommendation;
  title: string;
  agenda: string[];
  duration_minutes: number;
  email_draft: string;
}

// POST /api/transcript/finalize response. The enriched fields are only
// present in gateway mode (a live interview_id was sent with the request).
export interface FinalizeTranscriptResponse {
  summary: string;
  summary_ok: boolean;
  scorecard?: ScorecardData | null;
  insights?: ScoutFinding[] | null;
  followup?: FollowupProposal | null;
}
