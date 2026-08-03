/**
 * Shared TypeScript types for the frontend: session/UI enums plus the
 * request/response shapes for the backend interview, chat, profile, and
 * transcript endpoints (mirroring the backend Pydantic models).
 */

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

// POST /api/interview/{id}/document response (backend UploadDocumentResponse).
// Oversize documents are trimmed to the intake word limit, never rejected —
// `truncated` drives the StartScreen's short trim notice.
export interface UploadDocumentResponse {
  filename: string;
  word_count: number;
  truncated: boolean;
}

// GET /api/domains -> DomainsResponse. Dev stand-in for the admin-assigned
// domain: the vendor picks one on the start screen.
export interface DomainInfo {
  id: string;
  title: string;
}

export interface DomainsResponse {
  domains: DomainInfo[];
  // The server's default domain id (settings.default_domain) — the picker
  // preselects it rather than the first list entry.
  default: string;
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

// One cited source for a structured claim (backend ScoutClaimSourceModel).
export interface ScoutClaimSource {
  name: string;
  url: string;
  text: string;
}

// One judged claim in the structured claims report (backend ScoutClaimModel)
// - the structured-JSON counterpart of one Markdown claim block in
// `internet_findings`, kept in interview order (never grouped by status).
export interface ScoutClaim {
  status: 'CONTRADICTED' | 'PARTIAL' | 'VERIFIED' | 'NO_DATA';
  claim: string;
  note: string;
  sources: ScoutClaimSource[];
}

// Category grouping for ScoutAdditionalFinding (backend
// ScoutAdditionalFindingModel.category) - fixed display order for
// DataScoutReport's category subheadings.
export type ScoutAdditionalFindingCategory =
  | 'Financial'
  | 'Leadership'
  | 'Legal & Risk'
  | 'Partnerships'
  | 'Workforce'
  | 'Recent News';

// One entry in the structured additional-findings list (backend
// ScoutAdditionalFindingModel).
export interface ScoutAdditionalFinding {
  text: string;
  url: string;
  category: ScoutAdditionalFindingCategory;
}

// Structured-JSON counterpart of `internet_findings` (backend
// ScoutClaimsReportModel), for DataScoutReport to render directly instead of
// parsing Markdown. `claims` stays in interview order - do not re-sort/group
// by status.
export interface ScoutClaimsReport {
  claims: ScoutClaim[];
  additional_findings: ScoutAdditionalFinding[];
}

// Scout research report; arrives via the state payload (backend
// ScoutReportModel). `internet_findings` is a synthesized Markdown report,
// `interview_claims` are factual claims extracted from the transcript/intake
// material. The raw `sources` data the agent gathers along the way is
// deliberately not included here - too large/granular for the UI.
// `claims_report` is the structured-JSON counterpart of `internet_findings`
// (null if that call failed or the record predates this field).
export interface ScoutReport {
  internet_findings: string;
  interview_claims: string[];
  findings_ok: boolean;
  company_website: string | null;
  claims_report?: ScoutClaimsReport | null;
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
  insights: ScoutReport | null;
  recommendation: FollowupRecommendation | null;
}

// GET /api/interview/{id}/state response (backend InterviewStateResponse).
export interface InterviewStateResponse {
  status: 'created' | 'active' | 'finished';
  current_topic: string | null;
  // True once the script reached END (closing spoken/being spoken). The
  // avatar view auto-stops the session shortly after this flips.
  done: boolean;
  insights: ScoutReport | null;
  updated_at: string;
  pipeline_status: PipelineStatus | null;
  scorecard: ScorecardData | null;
  recommendation: FollowupRecommendation | null;
  // True when the pipeline's Evaluator call itself raised (e.g. a too-short
  // transcript) - scorecard stays null in that case too, but this flag lets
  // the UI tell the vendor scoring genuinely failed rather than silently
  // rendering nothing where the scorecard would be.
  evaluation_failed?: boolean;
  vendor_profile: VendorProfile;
}

// POST /api/interview/{id}/chat response.
export interface ChatResponse {
  reply: string;
  done: boolean;
}

// PATCH /api/interview/{id}/profile response (backend UpdateProfileResponse).
// The wire response also carries manually_edited_fields (the full set of
// fields ever manually corrected), but nothing in the frontend reads it, so
// it's intentionally not typed here.
export interface UpdateProfileResponse {
  vendor_profile: VendorProfile;
}

// --- DEV-ONLY: Data Scout Agent manual test harness (POST /api/scout-test) ---
// Not part of the real interview flow - see components/ScoutTestPage.tsx.

// Mirrors backend RepresentativeVerification (app.services.
// representative_verification) field-for-field.
export interface RepresentativeVerification {
  verdict: 'VERIFIED' | 'UNVERIFIED' | 'FLAG' | 'NO_DATA';
  email_valid: boolean;
  email_type: 'corporate' | 'personal' | 'invalid' | 'missing';
  email_domain: string;
  domain_match: boolean;
  employer_found: string;
  role: string;
  summary: string;
  background: {
    tenure: string;
    projects: string[];
    focus_areas: string[];
  };
  source_urls: string[];
}

// POST /api/scout-test response (backend ScoutTestResponse). Unlike the
// production ScoutReport type above, this DOES carry the raw `sources` dict
// and the full `representative_verification` structure - this dev harness
// exists to inspect everything Scout gathered and decided.
export interface ScoutTestResponse {
  internet_findings: string;
  interview_claims: string[];
  sources: Record<string, unknown>;
  findings_ok: boolean;
  company_website: string | null;
  representative_verification: RepresentativeVerification;
  claims_report: ScoutClaimsReport;
}
