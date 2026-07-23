import { useCallback, useRef, useState } from 'react';
import { API_URL } from '../config';
import { useIntervalPoll } from './useIntervalPoll';
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
  // Post-interview pipeline progress; null until finalize hands the interview
  // off to the background pipeline (terminal states: "ready" | "failed").
  pipelineStatus: PipelineStatus | null;
  // Scorecard / research insights / follow-up recommendation. Null at finalize;
  // filled in as the background pipeline reports them via state polling.
  scorecard: ScorecardData | null;
  // Scout research findings. Intentionally NOT rendered anywhere in the
  // SummaryPanel UI — the interview must stay unbiased by company research, so
  // the findings reach the human reviewer only via the downloaded Markdown
  // record (utils/downloadTranscript.ts). Kept here purely to feed that export.
  insights: ScoutFinding[] | null;
  recommendation: FollowupRecommendation | null;
  // True once the pipeline reports its Evaluator call itself failed (e.g. a
  // too-short transcript) - scorecard stays null in that case too, but this
  // tells SummaryPanel to explain why rather than showing nothing.
  evaluationFailed: boolean;
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
  evaluationFailed: false,
};

const isTerminal = (status: PipelineStatus | null) => status === 'ready' || status === 'failed';

/**
 * Owns the end-of-interview finalize + summary flow behind the SummaryPanel.
 *
 * Returns the flattened SummaryState (visible, isGenerating, summary, turns,
 * sessionId, error, pipelineStatus, scorecard, insights, recommendation) plus:
 * - `finalize(turns, sessionId)` — POSTs /api/transcript/finalize, then shows
 *   the panel with the summary (or a soft-fail error if it couldn't be
 *   generated). When an interviewId is set, this starts polling for the
 *   background pipeline's results.
 * - `dismiss()` — hides the panel and stops polling.
 *
 * Lifecycle: after finalize hands the interview to the pipeline, polls GET
 * /api/interview/{id}/state every 3s, merging scorecard/insights/
 * recommendation as they arrive, until the pipeline reaches a terminal status
 * ('ready' | 'failed') — then polling stops. dismiss() also stops it; unmount
 * cleanup is owned by useIntervalPoll.
 */
export function useInterviewSummary(interviewId: string | null = null) {
  const [state, setState] = useState<SummaryState>(INITIAL);

  // Which interview to poll; null = not polling. Set by finalize once the
  // background pipeline starts, cleared on terminal status or dismiss
  // (unmount cleanup is owned by useIntervalPoll).
  const [pollingId, setPollingId] = useState<string | null>(null);

  // The sessionId finalize has already been called for. A duplicate
  // finalize trigger for the SAME session (e.g. a network-quality switch
  // racing the normal session-end path) must not re-POST: it would reset
  // state back to INITIAL mid-flight and race the server-side idempotency
  // short-circuit. dismiss() deliberately does NOT clear this - dismissing
  // and re-finalizing the same session would still be a duplicate.
  const finalizedSessionRef = useRef<string | null>(null);

  // Poll GET /api/interview/{id}/state until the pipeline reaches a terminal
  // status, merging scorecard/insights/recommendation as they arrive.
  useIntervalPoll(async (signal) => {
    if (!pollingId) return;
    try {
      const res = await fetch(`${API_URL}/api/interview/${pollingId}/state`);
      if (!res.ok) return; // 404 while unknown / transient — keep last good data
      const data: InterviewStateResponse = await res.json();
      if (signal.cancelled) return;
      setState(prev => ({
        ...prev,
        pipelineStatus: data.pipeline_status ?? prev.pipelineStatus,
        scorecard: data.scorecard ?? prev.scorecard,
        insights: data.insights ?? prev.insights,
        recommendation: data.recommendation ?? prev.recommendation,
        evaluationFailed: data.evaluation_failed ?? prev.evaluationFailed,
      }));
      if (isTerminal(data.pipeline_status)) setPollingId(null);
    } catch (err) {
      console.error('Interview state poll failed:', err);
    }
  }, POLL_INTERVAL_MS, pollingId !== null);

  const finalize = useCallback(async (turns: TranscriptTurn[], sessionId: string | null) => {
    if (turns.length === 0) return;
    if (sessionId !== null && finalizedSessionRef.current === sessionId) return;
    finalizedSessionRef.current = sessionId;

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
        setPollingId(interviewId);
      }
    } catch (err) {
      console.error('Transcript finalize failed:', err);
      // The server never processed the request on this fetch-reject path, so
      // this ref must only dedupe successful/in-flight-completed finalizes -
      // clear it so a retry for the same session isn't silently no-op'd.
      finalizedSessionRef.current = null;
      setState(prev => ({
        ...prev,
        isGenerating: false,
        summary: '',
        error: 'Could not reach the server to generate the summary. The transcript below is still available to download.',
      }));
    }
  }, [interviewId]);

  const dismiss = useCallback(() => {
    setPollingId(null);
    setState(INITIAL);
  }, []);

  return { ...state, finalize, dismiss };
}
