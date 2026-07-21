import { useCallback, useEffect, useRef, useState } from 'react';
import { API_URL } from '../config';
import type {
  FinalizeTranscriptResponse,
  FollowupRecommendation,
  InterviewStateResponse,
  PipelineStatus,
  ScorecardData,
  ScoutFinding,
  TranscriptTurn,
} from '../types';

const POLL_INTERVAL_MS = 3000;

interface SummaryState {
  visible: boolean;
  isGenerating: boolean;
  summary: string;
  // Frozen snapshot of the transcript at the moment the session ended.
  turns: TranscriptTurn[];
  sessionId: string | null;
  // Set when the transcript was saved but the summary could not be generated.
  error: string | null;
  // Post-interview pipeline progress; null in legacy mode (no interview_id).
  pipelineStatus: PipelineStatus | null;
  // Scorecard / research insights / follow-up recommendation. Null at finalize;
  // filled in as the background pipeline reports them via state polling.
  scorecard: ScorecardData | null;
  insights: ScoutFinding[] | null;
  recommendation: FollowupRecommendation | null;
}

const INITIAL: SummaryState = {
  visible: false,
  isGenerating: false,
  summary: '',
  turns: [],
  sessionId: null,
  error: null,
  pipelineStatus: null,
  scorecard: null,
  insights: null,
  recommendation: null,
};

const isTerminal = (status: PipelineStatus | null) => status === 'ready' || status === 'failed';

export function useInterviewSummary(interviewId: string | null = null) {
  const [state, setState] = useState<SummaryState>(INITIAL);

  // Active polling interval; cleared on terminal status, dismiss, or unmount.
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // Poll GET /api/interview/{id}/state until the pipeline reaches a terminal
  // status, merging scorecard/insights/recommendation as they arrive.
  const startPolling = useCallback((id: string) => {
    stopPolling();
    const tick = async () => {
      try {
        const res = await fetch(`${API_URL}/api/interview/${id}/state`);
        if (!res.ok) return; // 404 while unknown / transient — keep last good data
        const data: InterviewStateResponse = await res.json();
        setState(prev => ({
          ...prev,
          pipelineStatus: data.pipeline_status ?? prev.pipelineStatus,
          scorecard: data.scorecard ?? prev.scorecard,
          insights: data.insights ?? prev.insights,
          recommendation: data.recommendation ?? prev.recommendation,
        }));
        if (isTerminal(data.pipeline_status)) stopPolling();
      } catch (err) {
        console.error('Interview state poll failed:', err);
      }
    };
    tick();
    pollRef.current = setInterval(tick, POLL_INTERVAL_MS);
  }, [stopPolling]);

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
        pipelineStatus: data.pipeline_status ?? null,
        error: data.summary_ok === false ? 'Summary could not be generated, but the transcript was saved.' : null,
      }));

      // Gateway mode: the background pipeline fills scorecard/insights/
      // recommendation after finalize returns — poll for them.
      if (interviewId && data.pipeline_status && !isTerminal(data.pipeline_status)) {
        startPolling(interviewId);
      }
    } catch (err) {
      console.error('Transcript finalize failed:', err);
      setState(prev => ({
        ...prev,
        isGenerating: false,
        summary: '',
        error: 'Could not reach the server to generate the summary. The transcript below is still available to download.',
      }));
    }
  }, [interviewId, startPolling]);

  const dismiss = useCallback(() => {
    stopPolling();
    setState(INITIAL);
  }, [stopPolling]);

  // Stop polling if the component unmounts mid-pipeline.
  useEffect(() => stopPolling, [stopPolling]);

  return { ...state, finalize, dismiss };
}
