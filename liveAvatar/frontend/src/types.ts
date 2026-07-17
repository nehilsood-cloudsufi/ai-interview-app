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
