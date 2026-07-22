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

// PATCH /api/interview/{id}/profile response (backend UpdateProfileResponse).
// manually_edited_fields is the full set of fields ever manually corrected -
// unused by the frontend today, but kept for shape fidelity with the backend.
export interface UpdateProfileResponse {
  vendor_profile: VendorProfile;
  manually_edited_fields: string[];
}

// --- Data Scout Agent (POST /api/scout) ---
// On-demand, company-agnostic web research. Distinct from ScoutFinding/
// insights above, which come from the automatic post-interview scout.

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
  company_website?: string | null;
  representative_name?: string | null;
  representative_role?: string | null;
  transcript?: string | null;
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
