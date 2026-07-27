import { useState } from 'react';
import { API_URL } from '../config';
import type { ScoutRequest, ScoutResponse, ScoutSources } from '../types';

export function useScoutReport() {
  const [visible, setVisible] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [internetFindings, setInternetFindings] = useState('');
  const [interviewClaims, setInterviewClaims] = useState<string[]>([]);
  const [sources, setSources] = useState<ScoutSources>({});
  const [findingsOk, setFindingsOk] = useState(true);
  const [companyName, setCompanyName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async (request: ScoutRequest) => {
    setVisible(true);
    setIsGenerating(true);
    setError(null);
    setCompanyName(request.company_name);

    try {
      const res = await fetch(`${API_URL}/api/scout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
      });
      if (!res.ok) throw new Error('Failed to run Data Scout');

      const data: ScoutResponse = await res.json();
      setInternetFindings(data.internet_findings);
      setInterviewClaims(data.interview_claims);
      setSources(data.sources);
      setFindingsOk(data.findings_ok);
      if (!data.findings_ok) {
        setError('Internet findings could not be generated, but gathered sources are shown below.');
      }
    } catch (err) {
      console.error('Data Scout run failed:', err);
      setError(err instanceof Error ? err.message : 'Failed to run Data Scout');
    } finally {
      setIsGenerating(false);
    }
  };

  const dismiss = () => {
    setVisible(false);
    setIsGenerating(false);
    setInternetFindings('');
    setInterviewClaims([]);
    setSources({});
    setFindingsOk(true);
    setCompanyName(null);
    setError(null);
  };

  return {
    visible,
    isGenerating,
    internetFindings,
    interviewClaims,
    sources,
    findingsOk,
    companyName,
    error,
    run,
    dismiss,
  };
}
