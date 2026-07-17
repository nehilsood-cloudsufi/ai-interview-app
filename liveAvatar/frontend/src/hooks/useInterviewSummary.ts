import { useCallback, useState } from 'react';
import { API_URL } from '../config';
import type { FinalizeTranscriptResponse, FollowupProposal, ScorecardData, ScoutFinding, TranscriptTurn } from '../types';

interface SummaryState {
  visible: boolean;
  isGenerating: boolean;
  summary: string;
  // Frozen snapshot of the transcript at the moment the session ended.
  turns: TranscriptTurn[];
  sessionId: string | null;
  // Set when the transcript was saved but the summary could not be generated.
  error: string | null;
  // Final scorecard + research insights from the finalize response (gateway
  // mode only; null in legacy mode or on failure).
  scorecard: ScorecardData | null;
  insights: ScoutFinding[] | null;
  // Coordinator follow-up proposal; null when nothing was recommended.
  followup: FollowupProposal | null;
}

const INITIAL: SummaryState = {
  visible: false,
  isGenerating: false,
  summary: '',
  turns: [],
  sessionId: null,
  error: null,
  scorecard: null,
  insights: null,
  followup: null,
};

export function useInterviewSummary(interviewId: string | null = null) {
  const [state, setState] = useState<SummaryState>(INITIAL);

  const finalize = useCallback(async (turns: TranscriptTurn[], sessionId: string | null) => {
    if (turns.length === 0) return;

    setState({
      ...INITIAL,
      visible: true,
      isGenerating: true,
      turns,
      sessionId,
    });

    try {
      const res = await fetch(`${API_URL}/api/transcript/finalize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId ?? 'unknown',
          turns,
          interview_id: interviewId ?? undefined,
        }),
      });

      if (!res.ok) throw new Error('Failed to finalize transcript');

      const data: FinalizeTranscriptResponse = await res.json();
      setState(prev => ({
        ...prev,
        isGenerating: false,
        summary: data.summary ?? '',
        scorecard: data.scorecard ?? null,
        insights: data.insights ?? null,
        followup: data.followup ?? null,
        error: data.summary_ok === false ? 'Summary could not be generated, but the transcript was saved.' : null,
      }));
    } catch (err) {
      console.error('Transcript finalize failed:', err);
      setState(prev => ({
        ...prev,
        isGenerating: false,
        summary: '',
        error: 'Could not reach the server to generate the summary. The transcript below is still available to download.',
      }));
    }
  }, [interviewId]);

  const dismiss = useCallback(() => setState(INITIAL), []);

  return { ...state, finalize, dismiss };
}
