import { useMemo, useState } from 'react';
import {
  Download,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  HelpCircle,
  Building2,
  Clock,
  FileText,
} from 'lucide-react';
import type { ScoutAdditionalFinding, ScoutAdditionalFindingCategory, ScoutClaim } from '../types';

/**
 * Visual design ported 1:1 from the reference DataScoutReport.jsx mockup
 * (dark navy theme, stat cards, collapsible claim cards, numbered Additional
 * Findings list). ONE deliberate behavioral difference from that mockup:
 * claims are rendered as a single list in INTERVIEW ORDER (the order given
 * in `claims`), never grouped/reordered by status - the reference mockup
 * groups by status (CONTRADICTED first, etc.) with a collapsible section per
 * group, which would silently re-sort claims out of interview order. The
 * per-claim card styling (status badge/color/icon, expand-for-note+sources)
 * is preserved; only the "group claims into an accordion per status" layer
 * was dropped, since it's fundamentally incompatible with interview-order
 * output. Stat counts (top cards) still tally by status - that's just a
 * count, not a reordering.
 */

const STATUS_CONFIG: Record<
  ScoutClaim['status'],
  { label: string; icon: typeof CheckCircle2; color: string; bg: string; border: string }
> = {
  CONTRADICTED: { label: 'Contradicted', icon: XCircle, color: '#ef4444', bg: 'rgba(239,68,68,0.08)', border: 'rgba(239,68,68,0.2)' },
  PARTIAL: { label: 'Partial', icon: AlertTriangle, color: '#f59e0b', bg: 'rgba(245,158,11,0.08)', border: 'rgba(245,158,11,0.2)' },
  VERIFIED: { label: 'Verified', icon: CheckCircle2, color: '#22c55e', bg: 'rgba(34,197,94,0.08)', border: 'rgba(34,197,94,0.2)' },
  NO_DATA: { label: 'No Data', icon: HelpCircle, color: '#64748b', bg: 'rgba(100,116,139,0.08)', border: 'rgba(100,116,139,0.2)' },
};

const STATUS_ORDER: ScoutClaim['status'][] = ['CONTRADICTED', 'PARTIAL', 'VERIFIED', 'NO_DATA'];

// Fixed display order + emoji for Additional Findings category subheadings -
// mirrors scout_claims_report_prompt/scout_system_prompt's category list and
// order exactly (config.py).
const FINDING_CATEGORY_ORDER: ScoutAdditionalFindingCategory[] = [
  'Financial',
  'Leadership',
  'Legal & Risk',
  'Partnerships',
  'Workforce',
  'Recent News',
];
const FINDING_CATEGORY_EMOJI: Record<ScoutAdditionalFindingCategory, string> = {
  Financial: '📊',
  Leadership: '👥',
  'Legal & Risk': '⚖️',
  Partnerships: '🤝',
  Workforce: '👨‍💼',
  'Recent News': '📰',
};

type VerificationStatus = 'VERIFIED' | 'NOT_FOUND' | 'NO_DATA';

const VERIFICATION_STATUS_CONFIG: Record<
  VerificationStatus,
  { label: string; icon: typeof CheckCircle2; color: string; bg: string; border: string }
> = {
  VERIFIED: { label: 'Verified', icon: CheckCircle2, color: '#22c55e', bg: 'rgba(34,197,94,0.08)', border: 'rgba(34,197,94,0.2)' },
  NOT_FOUND: { label: 'Not Found', icon: XCircle, color: '#ef4444', bg: 'rgba(239,68,68,0.08)', border: 'rgba(239,68,68,0.2)' },
  NO_DATA: { label: 'No Data', icon: HelpCircle, color: '#64748b', bg: 'rgba(100,116,139,0.08)', border: 'rgba(100,116,139,0.2)' },
};

interface ParsedVerification {
  status: VerificationStatus;
  name: string;
  role?: string;
  company: string;
  companyClaimed: boolean;
  summary?: string;
  focusAreas: string[];
  knownWork: string[];
  emailAddress?: string;
  emailNote?: string;
  evidence: { source: string; supports: string; url: string }[];
}

/**
 * The backend renders the "## Representative Verification" block as its own
 * fixed-format Markdown section (see representative_verification.render_
 * representative_verification), currently prepended to `internet_findings`
 * since that's the only field the production ScoutReportModel exposes for
 * it. splitScoutMarkdown pulls that leading block out (everything up to the
 * next `## ` heading) so DataScoutReport can render it as its own card
 * instead of it being buried in the plain-Markdown ScoutMarkdown view -
 * `rest` is whatever prose follows (currently unused - claims_report already
 * covers that content structurally).
 */
export function splitScoutMarkdown(markdown: string): { verification: string; rest: string } {
  if (!markdown.startsWith('## Representative Verification')) {
    return { verification: '', rest: markdown };
  }
  const nextHeadingIndex = markdown.indexOf('\n## ', 3);
  if (nextHeadingIndex === -1) return { verification: markdown.trim(), rest: '' };
  return { verification: markdown.slice(0, nextHeadingIndex).trim(), rest: markdown.slice(nextHeadingIndex).trim() };
}

function parseRepresentativeVerification(markdown: string): ParsedVerification | null {
  if (!markdown.trim()) return null;

  const lines = markdown.split('\n');
  const parsed: ParsedVerification = {
    status: 'NO_DATA',
    name: '',
    company: '',
    companyClaimed: false,
    focusAreas: [],
    knownWork: [],
    evidence: [],
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trim();
    if (!line || line.startsWith('## ')) {
      i++;
      continue;
    }

    let match = line.match(/^\*\*Status:\*\*\s*(.+)$/);
    if (match) {
      parsed.status = match[1].includes('VERIFIED') ? 'VERIFIED' : match[1].includes('NOT FOUND') ? 'NOT_FOUND' : 'NO_DATA';
      i++;
      continue;
    }
    match = line.match(/^\*\*Name:\*\*\s*(.+)$/);
    if (match) {
      parsed.name = match[1];
      i++;
      continue;
    }
    match = line.match(/^\*\*Role:\*\*\s*(.+)$/);
    if (match) {
      parsed.role = match[1];
      i++;
      continue;
    }
    match = line.match(/^\*\*Company:\*\*\s*(.+?)(\s*\(claimed\))?$/);
    if (match) {
      parsed.company = match[1];
      parsed.companyClaimed = !!match[2];
      i++;
      continue;
    }
    match = line.match(/^\*\*Summary:\*\*\s*(.+)$/);
    if (match) {
      parsed.summary = match[1];
      i++;
      continue;
    }
    match = line.match(/^\*\*Focus areas:\*\*\s*(.+)$/);
    if (match) {
      parsed.focusAreas = match[1].split(' · ');
      i++;
      continue;
    }
    match = line.match(/^\*\*Known work:\*\*\s*(.+)$/);
    if (match) {
      parsed.knownWork = match[1].split(' · ');
      i++;
      continue;
    }
    match = line.match(/^\*\*Email:\*\*\s*(.+)$/);
    if (match) {
      parsed.emailAddress = match[1];
      const noteLine = lines[i + 1]?.trim();
      if (noteLine && !noteLine.startsWith('**')) {
        parsed.emailNote = noteLine;
        i += 2;
        continue;
      }
      i++;
      continue;
    }
    if (line === '**Evidence:**') {
      i++;
      while (i < lines.length) {
        const evidenceLine = lines[i].trim();
        const evidenceMatch = evidenceLine.match(/^-\s*\*\*(.+?)\*\*\s*—\s*(.+)$/);
        if (!evidenceMatch) break;
        const urlLine = lines[i + 1]?.trim();
        const urlMatch = urlLine?.match(/^↳\s*(.+)$/);
        parsed.evidence.push({ source: evidenceMatch[1], supports: evidenceMatch[2], url: urlMatch ? urlMatch[1] : '' });
        i += urlMatch ? 2 : 1;
      }
      continue;
    }
    i++;
  }

  return parsed;
}

function RepresentativeVerificationSection({ markdown }: { markdown: string }) {
  const parsed = useMemo(() => parseRepresentativeVerification(markdown), [markdown]);
  if (!parsed) return null;

  const cfg = VERIFICATION_STATUS_CONFIG[parsed.status];
  const Icon = cfg.icon;

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 20 }}>
        <h2 style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#64748b', margin: 0 }}>
          Representative Verification
        </h2>
        <div style={{ flex: 1, height: 1, background: 'rgba(51,65,85,0.3)' }} />
      </div>

      <div style={{ background: cfg.bg, border: `1px solid ${cfg.border}`, borderRadius: 10, padding: '16px 18px', marginBottom: 36 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 5,
              background: cfg.color,
              color: '#fff',
              fontSize: 10,
              fontWeight: 700,
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              padding: '4px 10px',
              borderRadius: 5,
              whiteSpace: 'nowrap',
            }}
          >
            <Icon size={11} />
            {cfg.label}
          </span>
        </div>

        <div style={{ fontSize: 12, color: '#94a3b8', margin: '0 0 10px', lineHeight: 1.8 }}>
          {parsed.name && (
            <div>
              <span style={{ color: '#818cf8', fontWeight: 500 }}>Name:</span> <span style={{ color: '#e2e8f0' }}>{parsed.name}</span>
            </div>
          )}
          {parsed.role && (
            <div>
              <span style={{ color: '#818cf8', fontWeight: 500 }}>Role:</span> {parsed.role}
            </div>
          )}
          {parsed.company && (
            <div>
              <span style={{ color: '#818cf8', fontWeight: 500 }}>Company:</span> {parsed.company}
              {parsed.companyClaimed ? ' (claimed)' : ''}
            </div>
          )}
        </div>

        {parsed.summary && <p style={{ fontSize: 13, color: '#cbd5e1', lineHeight: 1.6, margin: '0 0 10px' }}>{parsed.summary}</p>}

        {(parsed.focusAreas.length > 0 || parsed.knownWork.length > 0) && (
          <div style={{ fontSize: 12, color: '#94a3b8', margin: '0 0 10px', lineHeight: 1.7 }}>
            {parsed.focusAreas.length > 0 && (
              <div>
                <span style={{ color: '#818cf8', fontWeight: 500 }}>Focus areas:</span> {parsed.focusAreas.join(' · ')}
              </div>
            )}
            {parsed.knownWork.length > 0 && (
              <div>
                <span style={{ color: '#818cf8', fontWeight: 500 }}>Known work:</span> {parsed.knownWork.join(' · ')}
              </div>
            )}
          </div>
        )}

        {parsed.emailNote && (
          <p style={{ fontSize: 12, color: '#94a3b8', margin: '0 0 10px', lineHeight: 1.6 }}>
            {parsed.emailAddress && (
              <>
                <span style={{ color: '#818cf8', fontWeight: 500 }}>Email:</span> {parsed.emailAddress}
                <br />
              </>
            )}
            {parsed.emailNote}
          </p>
        )}

        {parsed.evidence.length > 0 && (
          <div style={{ borderTop: '1px solid rgba(51,65,85,0.25)', paddingTop: 10 }}>
            <p style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#475569', margin: '0 0 8px' }}>
              Evidence
            </p>
            {parsed.evidence.map((item, idx) => (
              <div key={idx} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 6 }}>
                <ExternalLink size={12} color="#475569" style={{ marginTop: 3, flexShrink: 0 }} />
                <p style={{ fontSize: 13, color: '#94a3b8', margin: 0, lineHeight: 1.5 }}>
                  <span style={{ color: '#818cf8', fontWeight: 500 }}>{item.source}</span>
                  <span style={{ color: '#334155', margin: '0 6px' }}>·</span>
                  {item.supports}
                  {item.url && <span style={{ color: '#475569', fontSize: 11, marginLeft: 6 }}>({item.url})</span>}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

interface DataScoutReportProps {
  companyName: string;
  repName: string;
  repRole: string;
  reportDate: string;
  claims: ScoutClaim[];
  additionalFindings: ScoutAdditionalFinding[];
  verificationMarkdown?: string;
  onExport?: () => void;
}

/**
 * Builds a standalone Markdown record of just this report (header,
 * disclaimer, Representative Verification, Interview Claims, Additional
 * Findings) - independent of the full interview transcript export in
 * utils/downloadTranscript.ts, since this button downloads the Scout report
 * on its own (e.g. from the dev test harness, which has no transcript/
 * summary to attach it to).
 */
function buildScoutReportMarkdown({
  companyName,
  repName,
  repRole,
  reportDate,
  claims,
  additionalFindings,
  verificationMarkdown,
}: Pick<DataScoutReportProps, 'companyName' | 'repName' | 'repRole' | 'reportDate' | 'claims' | 'additionalFindings' | 'verificationMarkdown'>): string {
  const parts: string[] = ['# Data Scout Report', ''];
  parts.push(`_${companyName}${repName ? ` · ${repName}${repRole ? `, ${repRole}` : ''}` : ''} · ${reportDate}_`);
  parts.push('');
  parts.push(
    '_Built from unverified public web sources. Informational only — do not use as the sole basis for a vendor decision._',
  );
  parts.push('');

  if (verificationMarkdown) {
    parts.push(verificationMarkdown);
    parts.push('');
  }

  parts.push('## Interview Claims', '');
  if (claims.length === 0) {
    parts.push('No claims required attention.');
  } else {
    for (const claim of claims) {
      parts.push(`\`${claim.status}\` **${claim.claim}**`);
      parts.push(claim.note);
      for (const source of claim.sources) {
        parts.push(`↳ ${source.name} (${source.url}) — ${source.text}`);
      }
      parts.push('');
    }
  }

  parts.push('## Additional Findings', '');
  const groups = new Map<ScoutAdditionalFindingCategory, ScoutAdditionalFinding[]>();
  additionalFindings.forEach((finding) => {
    const bucket = groups.get(finding.category) ?? [];
    bucket.push(finding);
    groups.set(finding.category, bucket);
  });
  const populatedGroups = FINDING_CATEGORY_ORDER.map((category) => ({ category, findings: groups.get(category) ?? [] })).filter(
    (group) => group.findings.length > 0,
  );
  if (populatedGroups.length === 0) {
    parts.push('No additional findings.');
  } else {
    populatedGroups.forEach(({ category, findings }) => {
      parts.push(`### ${FINDING_CATEGORY_EMOJI[category]} ${category}`, '');
      findings.forEach((item, i) => {
        parts.push(`${i + 1}. ${item.text}${item.url ? ` ([source](${item.url}))` : ''}`);
      });
      parts.push('');
    });
  }

  return parts.join('\n');
}

function downloadScoutReport(props: Parameters<typeof buildScoutReportMarkdown>[0]): void {
  const markdown = buildScoutReportMarkdown(props);
  const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `data-scout-report-${props.companyName.trim().toLowerCase().replace(/\s+/g, '-') || 'report'}.md`;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

export function DataScoutReport({
  companyName,
  repName,
  repRole,
  reportDate,
  claims,
  additionalFindings,
  verificationMarkdown,
  onExport,
}: DataScoutReportProps) {
  const handleExport =
    onExport ?? (() => downloadScoutReport({ companyName, repName, repRole, reportDate, claims, additionalFindings, verificationMarkdown }));
  const [openClaims, setOpenClaims] = useState<Record<number, boolean>>({});
  const toggleClaim = (idx: number) => setOpenClaims((prev) => ({ ...prev, [idx]: !prev[idx] }));

  const counts = useMemo(() => {
    const c: Record<ScoutClaim['status'], number> = { CONTRADICTED: 0, PARTIAL: 0, VERIFIED: 0, NO_DATA: 0 };
    claims.forEach((claim) => {
      c[claim.status] += 1;
    });
    return { ...c, total: claims.length };
  }, [claims]);

  // Groups additionalFindings by category, in the fixed FINDING_CATEGORY_ORDER
  // - a category with zero findings is simply absent from the result, so the
  // render below never shows an empty subheading.
  const groupedFindings = useMemo(() => {
    const groups = new Map<ScoutAdditionalFindingCategory, ScoutAdditionalFinding[]>();
    additionalFindings.forEach((finding) => {
      const bucket = groups.get(finding.category) ?? [];
      bucket.push(finding);
      groups.set(finding.category, bucket);
    });
    return FINDING_CATEGORY_ORDER.map((category) => ({ category, findings: groups.get(category) ?? [] })).filter(
      (group) => group.findings.length > 0,
    );
  }, [additionalFindings]);

  return (
    <div
      style={{
        background: '#0b1120',
        color: '#cbd5e1',
        fontFamily: "'Inter', -apple-system, sans-serif",
        borderRadius: 12,
        overflow: 'hidden',
      }}
    >
      {/* HEADER */}
      <header style={{ background: 'rgba(11,17,32,0.92)', borderBottom: '1px solid rgba(51,65,85,0.4)' }}>
        <div style={{ padding: '14px 28px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: 10,
                background: 'rgba(99,102,241,0.12)',
                border: '1px solid rgba(99,102,241,0.25)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <FileText size={18} color="#818cf8" />
            </div>
            <div>
              <h1 style={{ fontSize: 16, fontWeight: 600, color: '#f1f5f9', margin: 0, lineHeight: 1.3 }}>Data Scout Report</h1>
              <p style={{ fontSize: 12, color: '#64748b', margin: 0, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                <Building2 size={11} /> {companyName}
                {repName && (
                  <>
                    <span style={{ margin: '0 4px', color: '#334155' }}>·</span>
                    {repName}
                    {repRole ? `, ${repRole}` : ''}
                  </>
                )}
                <span style={{ margin: '0 4px', color: '#334155' }}>·</span>
                <Clock size={11} /> {reportDate}
              </p>
            </div>
          </div>
          <button
            onClick={handleExport}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              background: 'rgba(30,41,59,0.7)',
              border: '1px solid rgba(51,65,85,0.5)',
              color: '#94a3b8',
              fontSize: 13,
              fontWeight: 500,
              padding: '7px 14px',
              borderRadius: 8,
              cursor: 'pointer',
            }}
          >
            <Download size={14} /> Export
          </button>
        </div>
      </header>

      <main style={{ padding: '24px 28px' }}>
        {/* DISCLAIMER */}
        <p
          style={{
            fontSize: 11,
            color: '#64748b',
            background: 'rgba(15,23,42,0.5)',
            border: '1px solid rgba(30,41,59,0.5)',
            borderRadius: 8,
            padding: '10px 16px',
            lineHeight: 1.6,
            marginBottom: 20,
          }}
        >
          Built from unverified public web sources. Informational only — do not use as the sole basis for a vendor decision.
        </p>

        {/* STATS */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 10, marginBottom: 28 }}>
          {STATUS_ORDER.map((status) => {
            const cfg = STATUS_CONFIG[status];
            const Icon = cfg.icon;
            return (
              <div
                key={status}
                style={{ background: 'rgba(15,23,42,0.6)', border: '1px solid rgba(30,41,59,0.5)', borderRadius: 10, padding: '14px 16px', textAlign: 'center' }}
              >
                <Icon size={16} color={cfg.color} style={{ marginBottom: 4 }} />
                <div style={{ fontSize: 22, fontWeight: 700, color: cfg.color, lineHeight: 1.2 }}>{counts[status]}</div>
                <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>{cfg.label}</div>
              </div>
            );
          })}
          <div style={{ background: 'rgba(15,23,42,0.6)', border: '1px solid rgba(30,41,59,0.5)', borderRadius: 10, padding: '14px 16px', textAlign: 'center' }}>
            <FileText size={16} color="#818cf8" style={{ marginBottom: 4 }} />
            <div style={{ fontSize: 22, fontWeight: 700, color: '#818cf8', lineHeight: 1.2 }}>{counts.total}</div>
            <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>Total</div>
          </div>
        </div>

        {/* REPRESENTATIVE VERIFICATION */}
        {verificationMarkdown && <RepresentativeVerificationSection markdown={verificationMarkdown} />}

        {/* SECTION LABEL */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 20 }}>
          <h2 style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#64748b', margin: 0 }}>
            Interview Claims
          </h2>
          <div style={{ flex: 1, height: 1, background: 'rgba(51,65,85,0.3)' }} />
        </div>

        {/* CLAIMS - single list, INTERVIEW ORDER (see module docstring above) */}
        {claims.length === 0 ? (
          <p style={{ fontSize: 13, color: '#64748b', fontStyle: 'italic', marginBottom: 36 }}>No claims required attention.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 36 }}>
            {claims.map((claim, idx) => {
              const cfg = STATUS_CONFIG[claim.status];
              const Icon = cfg.icon;
              const isExpanded = !!openClaims[idx];

              return (
                <div
                  key={idx}
                  style={{ background: cfg.bg, border: `1px solid ${cfg.border}`, borderRadius: 10, overflow: 'hidden' }}
                >
                  <button
                    onClick={() => toggleClaim(idx)}
                    style={{
                      width: '100%',
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 12,
                      padding: '14px 18px',
                      background: 'none',
                      border: 'none',
                      cursor: 'pointer',
                      textAlign: 'left',
                    }}
                  >
                    <span
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 5,
                        background: cfg.color,
                        color: '#fff',
                        fontSize: 10,
                        fontWeight: 700,
                        textTransform: 'uppercase',
                        letterSpacing: '0.05em',
                        padding: '4px 10px',
                        borderRadius: 5,
                        whiteSpace: 'nowrap',
                        flexShrink: 0,
                      }}
                    >
                      <Icon size={11} />
                      {cfg.label}
                    </span>

                    <div style={{ flex: 1, minWidth: 0 }}>
                      <p style={{ fontSize: 14, fontWeight: 500, color: '#e2e8f0', margin: 0, lineHeight: 1.4 }}>{claim.claim}</p>
                      {!isExpanded && (
                        <p
                          style={{
                            fontSize: 13,
                            color: '#94a3b8',
                            margin: '4px 0 0',
                            lineHeight: 1.5,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {claim.note}
                        </p>
                      )}
                    </div>

                    {isExpanded ? (
                      <ChevronUp size={14} color="#475569" style={{ marginTop: 2, flexShrink: 0 }} />
                    ) : (
                      <ChevronDown size={14} color="#475569" style={{ marginTop: 2, flexShrink: 0 }} />
                    )}
                  </button>

                  {isExpanded && (
                    <div style={{ padding: '0 18px 16px', marginTop: -4 }}>
                      <p style={{ fontSize: 13, color: '#cbd5e1', lineHeight: 1.7, margin: '0 0 12px' }}>{claim.note}</p>

                      {claim.sources.length > 0 && (
                        <div style={{ borderTop: '1px solid rgba(51,65,85,0.25)', paddingTop: 10 }}>
                          <p style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#475569', margin: '0 0 8px' }}>
                            Sources
                          </p>
                          {claim.sources.map((src, si) => (
                            <div key={si} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 6 }}>
                              <ExternalLink size={12} color="#475569" style={{ marginTop: 3, flexShrink: 0 }} />
                              <p style={{ fontSize: 13, color: '#94a3b8', margin: 0, lineHeight: 1.5 }}>
                                <span style={{ color: '#818cf8', fontWeight: 500 }}>{src.name}</span>
                                <span style={{ color: '#334155', margin: '0 6px' }}>·</span>
                                {src.text}
                                <span style={{ color: '#475569', fontSize: 11, marginLeft: 6 }}>({src.url})</span>
                              </p>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* ADDITIONAL FINDINGS */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
          <h2 style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#64748b', margin: 0 }}>
            Additional Findings
          </h2>
          <div style={{ flex: 1, height: 1, background: 'rgba(51,65,85,0.3)' }} />
        </div>

        {groupedFindings.length === 0 ? (
          <p style={{ fontSize: 13, color: '#64748b', fontStyle: 'italic' }}>No additional findings.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            {groupedFindings.map(({ category, findings }) => (
              <div key={category}>
                <p style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', margin: '0 0 8px' }}>
                  {FINDING_CATEGORY_EMOJI[category]} {category}
                </p>
                <div style={{ background: 'rgba(15,23,42,0.4)', border: '1px solid rgba(30,41,59,0.4)', borderRadius: 12, overflow: 'hidden' }}>
                  {findings.map((item, i) => (
                    <div
                      key={i}
                      style={{
                        display: 'flex',
                        alignItems: 'flex-start',
                        gap: 12,
                        padding: '14px 20px',
                        borderBottom: i < findings.length - 1 ? '1px solid rgba(30,41,59,0.3)' : 'none',
                      }}
                    >
                      <span
                        style={{
                          width: 22,
                          height: 22,
                          borderRadius: '50%',
                          background: 'rgba(99,102,241,0.1)',
                          border: '1px solid rgba(99,102,241,0.2)',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          fontSize: 10,
                          fontWeight: 600,
                          color: '#818cf8',
                          flexShrink: 0,
                          marginTop: 1,
                        }}
                      >
                        {i + 1}
                      </span>
                      <div>
                        <p style={{ fontSize: 13, color: '#cbd5e1', lineHeight: 1.6, margin: 0 }}>{item.text}</p>
                        {item.url && (
                          <p style={{ fontSize: 11, color: '#475569', margin: '4px 0 0', display: 'flex', alignItems: 'center', gap: 4 }}>
                            <ExternalLink size={10} />
                            <span style={{ color: '#6366f1' }}>{item.url}</span>
                          </p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
