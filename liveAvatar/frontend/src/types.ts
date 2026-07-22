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

// GET /api/domains -> DomainsResponse. Dev stand-in for the admin-assigned
// domain: the vendor picks one on the start screen.
export interface DomainInfo {
  id: string;
  title: string;
}

export interface DomainsResponse {
  domains: DomainInfo[];
}

// Vendor profile as returned inside GET /api/interview/{id}/state
// (backend VendorProfileModel — snake_case, doc_text excluded).
export interface VendorProfile {
  company_name: string;
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
// polling the state endpoint (never during the interview). Categories have a text
// value (e.g. "Strategic") plus resolved 0-100 points; overall is 0-100 with status.
export interface CategoryScoreData {
  id: string;
  name: string;
  weight: number;
  value: string | null;   // e.g. "Strategic", null if not covered
  points: number | null;  // resolved points (0-100) for this value
  evidence: string[];
}

export interface ScorecardData {
  categories: CategoryScoreData[];
  overall: number | null;                    // 0-100
  status: 'APPROVED' | 'REJECTED' | null;    // approval status
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

// PATCH /api/interview/{id}/profile response (backend UpdateProfileResponse).
// manually_edited_fields is the full set of fields ever manually corrected -
// unused by the frontend today, but kept for shape fidelity with the backend.
export interface UpdateProfileResponse {
  vendor_profile: VendorProfile;
  manually_edited_fields: string[];
}
