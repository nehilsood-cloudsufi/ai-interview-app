import { useCallback, useState } from 'react';
import { API_URL } from '../config';
import type { TranscriptTurn } from '../types';

interface SummaryState {
  visible: boolean;
  isGenerating: boolean;
  summary: string;
  // Frozen snapshot of the transcript at the moment the session ended.
  turns: TranscriptTurn[];
  sessionId: string | null;
  // Set when the transcript was saved but the summary could not be generated.
  error: string | null;
}

const INITIAL: SummaryState = {
  visible: false,
  isGenerating: false,
  summary: '',
  turns: [],
  sessionId: null,
  error: null,
};

export function useInterviewSummary() {
  const [state, setState] = useState<SummaryState>(INITIAL);

  const finalize = useCallback(async (turns: TranscriptTurn[], sessionId: string | null) => {
    if (turns.length === 0) return;

    setState({
      visible: true,
      isGenerating: true,
      summary: '',
      turns,
      sessionId,
      error: null,
    });

    try {
      const res = await fetch(`${API_URL}/api/transcript/finalize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId ?? 'unknown', turns }),
      });

      if (!res.ok) throw new Error('Failed to finalize transcript');

      const data = await res.json();
      setState(prev => ({
        ...prev,
        isGenerating: false,
        summary: data.summary ?? '',
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
  }, []);

  const dismiss = useCallback(() => setState(INITIAL), []);

  return { ...state, finalize, dismiss };
}
