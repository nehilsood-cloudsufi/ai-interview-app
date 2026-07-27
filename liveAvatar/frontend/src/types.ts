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

// Scout research report; arrives via the state payload (backend
// ScoutReportModel). `internet_findings` is a synthesized Markdown report,
// `interview_claims` are factual claims extracted from the transcript/intake
// material. The raw `sources` data the standalone /api/scout tool exposes is
// deliberately not included here - see GithubRepo etc. below for that shape.
export interface ScoutReport {
  internet_findings: string;
  interview_claims: string[];
  findings_ok: boolean;
  company_website: string | null;
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

// --- Data Scout Agent (POST /api/scout) ---
// On-demand, company-agnostic web research. Same underlying agent/report
// shape as ScoutReport/insights above (the automatic post-interview scout),
// but this response additionally exposes `scout_id` and the raw `sources`
// data, which the automatic pipeline path omits from the UI-facing response.

export interface GithubRepo {
  name: string | null;
  description: string | null;
  language: string | null;
  stargazers_count: number | null;
  html_url: string | null;
  readme_excerpt: string | null;
}

// Conditional source: only gathered (and only present in ScoutSources) when
// a github.com URL was actually discovered in the transcript or website -
// absent entirely for non-technical companies, never an empty placeholder.
export interface GithubProfile {
  username: string;
  account_type: 'User' | 'Organization';
  name: string | null;
  bio: string | null;
  company: string | null;
  location: string | null;
  public_repos: number | null;
  followers: number | null;
  blog: string | null;
  html_url: string | null;
  hireable: boolean | null;
  created_at: string | null;
  repos: GithubRepo[];
}

export interface WebSearchResult {
  title: string;
  url: string;
  snippet: string;
  // Full page text fetched from this result's URL, when available - richer
  // than `snippet` alone. Null if the fetch wasn't attempted or failed.
  text: string | null;
}

export interface SubpageResult {
  url: string;
  text: string;
}

export interface PageResult {
  url: string;
  text: string;
  subpages: SubpageResult[];
}

export interface SearchQueryResult {
  query: string;
  results: WebSearchResult[];
}

// Pass B only: one entry per factual claim extracted from the transcript,
// each with its own targeted search results (deduplicated against Pass A).
export interface TargetedSearchResult {
  claim: string;
  results: WebSearchResult[];
}

export interface LinkLookup {
  url: string;
  domain: string;
  results: WebSearchResult[];
  // Always false - this platform blocks full profile content from being
  // publicly retrieved, so `results` is a partial, snippet-only view.
  full_profile_accessible: boolean;
}

// Every key is optional: Scout only includes a key when it actually
// gathered data for it. In particular, `github` is entirely absent (not an
// empty array) when no GitHub URL was found - never treat its absence as a
// negative finding.
export interface ScoutSources {
  pages?: PageResult[];
  blind_search?: SearchQueryResult[];
  github?: GithubProfile[];
  link_lookups?: LinkLookup[];
  targeted_search?: TargetedSearchResult[];
  interview_claims?: string[];
}

// POST /api/scout request body. transcript is entered manually in the UI
// for now, until the interview module pipes it in via pub/sub later.
export interface ScoutRequest {
  company_name: string;
  representative_name: string;
  representative_role?: string | null;
  extra_context?: string | null;
  documents?: string[] | null;
  transcript?: string | null;
}

// POST /api/scout/document response - extracted plain text of one uploaded
// document, for the standalone Scout form's own document handling
// (independent of the intake form's /api/interview/{id}/document).
export interface ScoutDocumentResponse {
  text: string;
}

// POST /api/scout / GET /api/scout/{id} response. internet_findings and
// interview_claims are deliberately separate, peer fields - Scout gathers
// and presents only; the Evaluator does all comparison.
export interface ScoutResponse {
  scout_id: string;
  internet_findings: string;
  interview_claims: string[];
  sources: ScoutSources;
  findings_ok: boolean;
}
