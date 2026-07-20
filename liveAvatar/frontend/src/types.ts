export type SessionStatus = 'disconnected' | 'connecting' | 'connected';
export type SpeakingState = 'idle' | 'user_speaking' | 'avatar_speaking' | 'processing';
export type NetworkQuality = 'excellent' | 'good' | 'poor' | 'unknown';

// Interview view mode. One-way: an avatar session can switch to chat, never back.
export type InterviewMode = 'avatar' | 'chat';

export type TranscriptRole = 'interviewer' | 'candidate';

export interface TranscriptTurn {
  role: TranscriptRole;
  text: string;
  timestamp: number;
}

// POST /api/interview -> CreateInterviewResponse.
export interface CreateInterviewResponse {
  interview_id: string;
}

// Vendor profile as returned inside GET /api/interview/{id}/state
// (backend VendorProfileModel — snake_case, doc_text excluded).
export interface VendorProfile {
  company_name: string;
  website: string | null;
  contact_name: string;
  contact_role: string | null;
}

// Post-interview pipeline progress; null until finalize hands off to the
// background pipeline. Terminal states: "ready" | "failed".
export type PipelineStatus =
  | 'interviewed'
  | 'scouting'
  | 'evaluating'
  | 'ready'
  | 'failed';

// Final scorecard from the holistic end-of-interview scoring pass; arrives via
// polling the state endpoint (never during the interview). Scores are 0-5.
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

// Scout research insights; arrive via the state payload.
export interface ScoutFinding {
  topic: string;
  summary: string;
  source_url: string | null;
}

// Follow-up recommendation (backend FollowupRecommendationModel).
export interface FollowupRecommendation {
  kind: 'advance' | 'clarify';
  reason: string;
  focus_categories: string[];
}

// POST /api/transcript/finalize response. scorecard/insights/recommendation are
// ALWAYS null here — they arrive via polling GET /api/interview/{id}/state.
export interface FinalizeTranscriptResponse {
  summary: string;
  summary_ok: boolean;
  pipeline_status: PipelineStatus | null;
  scorecard: ScorecardData | null;
  insights: ScoutFinding[] | null;
  recommendation: FollowupRecommendation | null;
}

// GET /api/interview/{id}/state response (backend InterviewStateResponse).
export interface InterviewStateResponse {
  status: 'created' | 'active' | 'finished';
  current_topic: string | null;
  insights: ScoutFinding[];
  updated_at: string;
  pipeline_status: PipelineStatus | null;
  scorecard: ScorecardData | null;
  recommendation: FollowupRecommendation | null;
  vendor_profile: VendorProfile;
}

// POST /api/interview/{id}/chat response.
export interface ChatResponse {
  reply: string;
  done: boolean;
}
