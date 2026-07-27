import { type ReactNode, type ComponentType } from 'react';
import { X, Loader2, AlertTriangle, ShieldAlert, Download, Code2, Globe, Search } from 'lucide-react';
import type { ScoutSources, WebSearchResult } from '../types';

const DISCLAIMER_TEXT =
  'Built from unverified public web sources. Informational only — do not use as the sole basis for a vendor decision. ' +
  'Scout only gathers and presents facts; it does not evaluate, score, or compare them.';

function domainOf(url: string): string {
  try {
    const host = new URL(url).hostname.toLowerCase();
    return host.startsWith('www.') ? host.slice(4) : host;
  } catch {
    return url;
  }
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`;
}

// Prefer the fetched full page text over the search snippet when available -
// richer context from what the other site actually says, not just a preview.
function SearchResultItem({ result }: { result: WebSearchResult }) {
  return (
    <div>
      <a href={result.url} target="_blank" rel="noreferrer" className="text-xs font-semibold text-slate-300 hover:text-indigo-400 hover:underline">
        {result.title}
      </a>
      {(result.text || result.snippet) && (
        <p className="text-xs text-slate-400 mt-0.5 line-clamp-4">{result.text || result.snippet}</p>
      )}
    </div>
  );
}

function CountBadge({ count }: { count: number }) {
  return (
    <span className="text-[10px] font-semibold text-slate-300 bg-slate-700 rounded-full min-w-[1.25rem] text-center px-2 py-0.5">
      {count}
    </span>
  );
}

function SubSectionHeader({ icon: Icon, title, count }: { icon: ComponentType<{ className?: string }>; title: string; count: number }) {
  return (
    <div className="flex items-center justify-between mb-3">
      <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400 flex items-center gap-2">
        <Icon className="w-3.5 h-3.5" /> {title}
      </h4>
      <CountBadge count={count} />
    </div>
  );
}

// --- Source reference rows ---------------------------------------------
// A single, compact "where did this data come from" list - no raw page
// text/snippets here, just domain/type/count. GitHub is only ever included
// when sources.github is actually present and non-empty (never an empty
// "no GitHub data" row - that would bias the report for non-technical
// companies).

interface SourceRow {
  label: string;
  description: string;
  url: string | null;
}

function buildSourceRows(sources: ScoutSources): SourceRow[] {
  const rows: SourceRow[] = [];

  for (const page of sources.pages ?? []) {
    const subpageCount = page.subpages.length;
    rows.push({
      label: domainOf(page.url),
      description: subpageCount > 0 ? `Website — ${plural(subpageCount, 'subpage')} fetched` : 'Website fetched',
      url: page.url,
    });
  }

  for (const entry of sources.blind_search ?? []) {
    rows.push({
      label: `Web search: "${entry.query}"`,
      description: plural(entry.results.length, 'result'),
      url: null,
    });
  }

  if (sources.github && sources.github.length > 0) {
    const repoCount = sources.github.reduce((acc, p) => acc + p.repos.length, 0);
    rows.push({
      label: 'GitHub',
      description: `${plural(sources.github.length, 'profile')}, ${plural(repoCount, 'repo')}`,
      url: sources.github[0].html_url,
    });
  }

  for (const lookup of sources.link_lookups ?? []) {
    rows.push({
      label: domainOf(lookup.url),
      description: `Social profile — ${plural(lookup.results.length, 'result')}`,
      url: lookup.url,
    });
  }

  for (const entry of sources.targeted_search ?? []) {
    rows.push({
      label: `Targeted search: "${entry.claim}"`,
      description: plural(entry.results.length, 'result'),
      url: null,
    });
  }

  return rows;
}

// --- Lightweight inline markdown renderer -----------------------------
// No markdown library is installed; internet_findings only ever uses a
// small, predictable subset (headings, bold, bullets, blockquotes, hr,
// paragraphs), so a small line-based parser is enough.

let mdKey = 0;

// Backtick text matching one of these four labels exactly renders as a
// colored pill badge instead of a <code> tag - anything else in backticks
// stays a normal <code> tag.
const STATUS_BADGE_CLASSES: Record<string, string> = {
  VERIFIED: 'bg-green-600 text-white text-[10px] font-bold px-2 py-0.5 rounded-full inline-block tracking-wide mr-2',
  CONTRADICTED: 'bg-red-600 text-white text-[10px] font-bold px-2 py-0.5 rounded-full inline-block tracking-wide mr-2',
  PARTIAL: 'bg-amber-500 text-white text-[10px] font-bold px-2 py-0.5 rounded-full inline-block tracking-wide mr-2',
  'NO DATA': 'bg-gray-500 text-white text-[10px] font-bold px-2 py-0.5 rounded-full inline-block tracking-wide mr-2',
};

function renderInline(text: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g);
  return parts.map(part => {
    mdKey += 1;
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return (
        <strong key={mdKey} className="text-white font-semibold">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      const inner = part.slice(1, -1);
      const badgeClass = STATUS_BADGE_CLASSES[inner];
      if (badgeClass) {
        return (
          <span key={mdKey} className={badgeClass}>
            {inner}
          </span>
        );
      }
      return (
        <code key={mdKey} className="bg-slate-800 text-slate-300 text-xs px-1.5 py-0.5 rounded">
          {inner}
        </code>
      );
    }
    const linkMatch = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (linkMatch) {
      return (
        <a key={mdKey} href={linkMatch[2]} target="_blank" rel="noreferrer" className="text-indigo-400 hover:underline">
          {linkMatch[1]}
        </a>
      );
    }
    return <span key={mdKey}>{part}</span>;
  });
}

// A claim block starts with a line like "`CONTRADICTED` **claim text**" and
// continues through its reason line and any "↳" source lines, until a blank
// line. Grouped into one bordered container so each claim reads as a
// distinct card rather than a run of loose paragraphs.
const CLAIM_LABELS = ['VERIFIED', 'CONTRADICTED', 'PARTIAL', 'NO DATA'];
const CLAIM_HEADER_RE = new RegExp(`^\`(?:${CLAIM_LABELS.join('|')})\`\\s`);

function renderMarkdown(markdown: string): ReactNode[] {
  const lines = markdown.split('\n');
  const elements: ReactNode[] = [];

  let listBuffer: string[] = [];
  let quoteBuffer: string[] = [];
  let paraBuffer: string[] = [];
  let claimBlockBuffer: ReactNode[] = [];

  const flushClaimBlock = () => {
    if (claimBlockBuffer.length === 0) return;
    mdKey += 1;
    elements.push(
      <div key={mdKey} className="border-b border-slate-700/40 pb-4 mb-4 last:border-b-0 last:mb-0 last:pb-0">
        {claimBlockBuffer}
      </div>
    );
    claimBlockBuffer = [];
  };

  const flushList = () => {
    if (listBuffer.length === 0) return;
    mdKey += 1;
    elements.push(
      <ul key={mdKey} className="list-disc list-outside pl-5 space-y-1.5 text-slate-200 leading-7">
        {listBuffer.map(item => {
          mdKey += 1;
          return <li key={mdKey}>{renderInline(item)}</li>;
        })}
      </ul>
    );
    listBuffer = [];
  };

  const flushQuote = () => {
    if (quoteBuffer.length === 0) return;
    mdKey += 1;
    elements.push(
      <aside key={mdKey} className="border-l-2 border-slate-600 pl-4 py-0.5 text-slate-400 text-sm italic space-y-1">
        {quoteBuffer.map(line => {
          mdKey += 1;
          return <p key={mdKey}>{renderInline(line)}</p>;
        })}
      </aside>
    );
    quoteBuffer = [];
  };

  const flushPara = () => {
    if (paraBuffer.length === 0) return;
    mdKey += 1;
    elements.push(
      <p key={mdKey} className="text-slate-200 leading-7">
        {renderInline(paraBuffer.join(' '))}
      </p>
    );
    paraBuffer = [];
  };

  const flushAll = () => {
    flushList();
    flushQuote();
    flushPara();
    flushClaimBlock();
  };

  for (const rawLine of lines) {
    const trimmed = rawLine.trim();

    if (trimmed === '') {
      flushAll();
      continue;
    }

    if (/^-{3,}\s*$/.test(trimmed)) {
      flushAll();
      mdKey += 1;
      elements.push(<hr key={mdKey} className="border-slate-800 my-2" />);
      continue;
    }

    const h3Match = trimmed.match(/^###\s+(.*)/);
    if (h3Match) {
      flushAll();
      mdKey += 1;
      elements.push(
        <h3 key={mdKey} className="text-base font-semibold text-white mt-2">
          {renderInline(h3Match[1])}
        </h3>
      );
      continue;
    }

    const h2Match = trimmed.match(/^##\s+(.*)/);
    if (h2Match) {
      flushAll();
      mdKey += 1;
      elements.push(
        <h2 key={mdKey} className="text-xl font-light text-white mt-6 pb-2 border-b border-slate-800">
          {renderInline(h2Match[1])}
        </h2>
      );
      continue;
    }

    const quoteMatch = trimmed.match(/^>\s?(.*)/);
    if (quoteMatch) {
      flushList();
      flushPara();
      flushClaimBlock();
      quoteBuffer.push(quoteMatch[1]);
      continue;
    }

    const listMatch = trimmed.match(/^[-*]\s+(.*)/);
    if (listMatch) {
      flushQuote();
      flushPara();
      flushClaimBlock();
      listBuffer.push(listMatch[1]);
      continue;
    }

    if (CLAIM_HEADER_RE.test(trimmed)) {
      flushList();
      flushQuote();
      flushPara();
      flushClaimBlock();
      mdKey += 1;
      claimBlockBuffer.push(
        <p key={mdKey} className="text-slate-200 leading-6">
          {renderInline(trimmed)}
        </p>
      );
      continue;
    }

    if (claimBlockBuffer.length > 0) {
      mdKey += 1;
      if (trimmed.startsWith('↳')) {
        claimBlockBuffer.push(
          <p key={mdKey} className="text-slate-400 pl-4 text-sm mt-0.5">
            {renderInline(trimmed)}
          </p>
        );
      } else {
        claimBlockBuffer.push(
          <p key={mdKey} className="text-slate-300 text-sm mt-1">
            {renderInline(trimmed)}
          </p>
        );
      }
      continue;
    }

    flushList();
    flushQuote();
    paraBuffer.push(trimmed);
  }

  flushAll();
  return elements;
}

// Badges only make sense as colored pills in the live UI - the downloaded
// .md file has no CSS, so each backtick-wrapped label is swapped for a plain
// text symbol equivalent instead.
const DOWNLOAD_LABEL_REPLACEMENTS: [RegExp, string][] = [
  [/`VERIFIED`/g, '✅ VERIFIED'],
  [/`CONTRADICTED`/g, '❌ CONTRADICTED'],
  [/`PARTIAL`/g, '⚠️ PARTIAL'],
  [/`NO DATA`/g, '🔍 NO DATA'],
];

function applyDownloadLabels(text: string): string {
  return DOWNLOAD_LABEL_REPLACEMENTS.reduce((acc, [pattern, replacement]) => acc.replace(pattern, replacement), text);
}

function buildReportMarkdown(companyName: string | null, internetFindings: string, sources: ScoutSources): string {
  const rows = buildSourceRows(sources);
  const lines: string[] = [];

  lines.push(`# Data Scout Report — ${companyName || 'Unknown'}`);
  lines.push(`Generated: ${new Date().toLocaleString()}`);
  lines.push('');
  lines.push(DISCLAIMER_TEXT);
  lines.push('');
  lines.push(applyDownloadLabels(internetFindings) || 'No findings available.');
  lines.push('');
  lines.push('## Sources');
  if (rows.length > 0) {
    rows.forEach((row, i) => {
      const urlPart = row.url ? ` (${row.url})` : '';
      lines.push(`${i + 1}. ${row.label} — ${row.description}${urlPart}`);
    });
  } else {
    lines.push('No sources gathered.');
  }

  return lines.join('\n');
}

function downloadReport(companyName: string | null, internetFindings: string, sources: ScoutSources) {
  const markdown = buildReportMarkdown(companyName, internetFindings, sources);
  const blob = new Blob([markdown], { type: 'text/markdown' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  const safeName = (companyName || 'report').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
  a.href = url;
  a.download = `data-scout-report-${safeName || 'unknown'}.md`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

interface ScoutPanelProps {
  visible: boolean;
  isGenerating: boolean;
  internetFindings: string;
  interviewClaims: string[];
  sources: ScoutSources;
  companyName: string | null;
  error: string | null;
  onDismiss: () => void;
}

export function ScoutPanel({
  visible,
  isGenerating,
  internetFindings,
  interviewClaims,
  sources,
  companyName,
  error,
  onDismiss,
}: ScoutPanelProps) {
  if (!visible) return null;

  const sourceRows = buildSourceRows(sources);
  const canDownload = !isGenerating && (!!internetFindings || interviewClaims.length > 0 || sourceRows.length > 0);

  const hasGithub = !!sources.github && sources.github.length > 0;
  const hasPages = !!sources.pages && sources.pages.length > 0;
  const hasBlindSearch = !!sources.blind_search && sources.blind_search.length > 0;
  const hasLinkLookups = !!sources.link_lookups && sources.link_lookups.length > 0;
  const hasTargetedSearch = !!sources.targeted_search && sources.targeted_search.length > 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="w-full max-w-3xl max-h-[90vh] flex flex-col bg-slate-900 rounded-2xl border border-slate-700/60 shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700/50 shrink-0">
          <h2 className="text-lg font-bold text-white">
            Data Scout Report{companyName ? `: ${companyName}` : ''}
          </h2>
          <div className="flex items-center gap-2">
            {canDownload && (
              <button
                onClick={() => downloadReport(companyName, internetFindings, sources)}
                className="flex items-center gap-1.5 text-xs font-semibold text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 border border-slate-700/50 rounded-lg px-3 py-1.5 transition-colors"
              >
                <Download className="w-3.5 h-3.5" /> Download Report
              </button>
            )}
            <button
              onClick={onDismiss}
              className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
              aria-label="Close"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Single scrolling body */}
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-8">
          {/* Disclaimer - always shown, independent of backend response */}
          <div className="flex items-start gap-2 text-slate-400 bg-slate-800/60 border border-slate-700/50 rounded-lg px-3 py-2">
            <ShieldAlert className="w-4 h-4 mt-0.5 shrink-0 text-slate-500" />
            <span className="text-xs leading-relaxed">{DISCLAIMER_TEXT}</span>
          </div>

          {isGenerating ? (
            <div className="flex items-center gap-3 text-slate-400">
              <Loader2 className="w-5 h-5 animate-spin" />
              <span className="text-sm">Scouting company…</span>
            </div>
          ) : (
            <>
              {error && (
                <div className="flex items-start gap-2 text-amber-300 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
                  <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                  <span className="text-sm">{error}</span>
                </div>
              )}

              {/* Peer section A: Internet Findings */}
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 pb-2 mb-3 border-b border-slate-800">
                  Internet Findings
                </h3>
                {internetFindings ? (
                  <div className="space-y-1 leading-7">{renderMarkdown(internetFindings)}</div>
                ) : (
                  !error && <p className="text-sm text-slate-500 italic">No internet findings available.</p>
                )}
              </section>

              {/* Raw source details - collapsed by default */}
              <details className="group">
                <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wider text-slate-400 pb-2 border-b border-slate-800 select-none hover:text-slate-200 transition-colors">
                  View Raw Source Data
                </summary>

                <div className="pt-6 space-y-8">
                  {/* Sources used - compact numbered reference list */}
                  <section>
                    <SubSectionHeader icon={Search} title="Sources" count={sourceRows.length} />
                    {sourceRows.length > 0 ? (
                      <div className="border border-slate-800 rounded-lg overflow-hidden divide-y divide-slate-800">
                        {sourceRows.map((row, i) => (
                          <div key={i} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                            <span className="shrink-0 w-6 h-6 flex items-center justify-center rounded-full bg-slate-700 text-slate-300 text-[11px] font-semibold">
                              {i + 1}
                            </span>
                            <div className="flex-1 min-w-0 flex items-baseline gap-1.5 flex-wrap">
                              {row.url ? (
                                <a href={row.url} target="_blank" rel="noreferrer" className="text-indigo-400 hover:underline font-medium truncate">
                                  {row.label}
                                </a>
                              ) : (
                                <span className="text-slate-200 font-medium truncate">{row.label}</span>
                              )}
                            </div>
                            <span className="shrink-0 text-slate-400 text-xs">{row.description}</span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-sm text-slate-500 italic">No sources gathered.</p>
                    )}
                  </section>

                  {/* Company website */}
                  {hasPages && (
                    <section className="pt-6 border-t border-slate-800/70">
                      <SubSectionHeader icon={Globe} title="Company Website" count={sources.pages!.length} />
                      <div className="space-y-3">
                        {sources.pages!.map((p, i) => (
                          <div key={i} className="bg-slate-800/50 border border-slate-700/50 rounded-xl p-3">
                            <a href={p.url} target="_blank" rel="noreferrer" className="text-sm text-indigo-400 hover:underline truncate block">
                              {p.url}
                            </a>
                            <p className="text-xs text-slate-400 mt-1 line-clamp-3">{p.text}</p>

                            {p.subpages.length > 0 && (
                              <div className="mt-2 pl-3 border-l-2 border-slate-700 space-y-2">
                                {p.subpages.map((sp, j) => (
                                  <div key={j}>
                                    <a href={sp.url} target="_blank" rel="noreferrer" className="text-xs text-indigo-400/80 hover:underline truncate block">
                                      {sp.url}
                                    </a>
                                    <p className="text-xs text-slate-500 mt-0.5 line-clamp-2">{sp.text}</p>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </section>
                  )}

                  {/* GitHub - only rendered when sources.github is present and
                      non-empty. Never shown as an empty "no GitHub data"
                      section, which would bias against non-technical companies. */}
                  {hasGithub && (
                    <section className="pt-6 border-t border-slate-800/70">
                      <SubSectionHeader icon={Code2} title="GitHub" count={sources.github!.length} />
                      <div className="space-y-3">
                        {sources.github!.map((profile, i) => (
                          <div key={i} className="bg-slate-800/50 border border-slate-700/50 rounded-xl p-4">
                            <div className="flex items-baseline justify-between gap-2">
                              <span className="flex items-center gap-1.5">
                                <a
                                  href={profile.html_url ?? `https://github.com/${profile.username}`}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="text-sm font-semibold text-indigo-400 hover:underline"
                                >
                                  {profile.name || profile.username}
                                </a>
                                {profile.account_type === 'Organization' && (
                                  <span className="text-[10px] uppercase tracking-wider text-slate-500 bg-slate-700/50 px-1.5 py-0.5 rounded">Org</span>
                                )}
                              </span>
                              <span className="text-xs text-slate-500">{profile.public_repos ?? 0} public repos</span>
                            </div>
                            {profile.bio && <p className="text-sm text-slate-300 mt-1">{profile.bio}</p>}
                            {profile.repos.length > 0 && (
                              <div className="mt-2 space-y-2">
                                {profile.repos.slice(0, 5).map((repo, j) => (
                                  <div key={j} className="text-xs text-slate-400">
                                    <a href={repo.html_url ?? undefined} target="_blank" rel="noreferrer" className="text-slate-300 hover:underline font-medium">
                                      {repo.name}
                                    </a>
                                    {repo.description && <span className="text-slate-500"> — {repo.description}</span>}
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </section>
                  )}

                  {/* Social media / login-walled links */}
                  {hasLinkLookups && (
                    <section className="pt-6 border-t border-slate-800/70">
                      <SubSectionHeader icon={Search} title="Social Media Profiles" count={sources.link_lookups!.length} />
                      <div className="space-y-3">
                        {sources.link_lookups!.map((lookup, i) => (
                          <div key={i} className="bg-slate-800/50 border border-slate-700/50 rounded-xl p-3">
                            <a href={lookup.url} target="_blank" rel="noreferrer" className="text-sm text-indigo-400 hover:underline truncate block">
                              {lookup.url}
                            </a>
                            {lookup.results.length > 0 ? (
                              <div className="mt-2 space-y-2">
                                {lookup.results.map((r, j) => (
                                  <SearchResultItem key={j} result={r} />
                                ))}
                              </div>
                            ) : (
                              <p className="text-xs text-slate-500 italic mt-1">No public search results found for this link.</p>
                            )}
                          </div>
                        ))}
                      </div>
                    </section>
                  )}

                  {/* Blind (transcript-independent) web search */}
                  {hasBlindSearch && (
                    <section className="pt-6 border-t border-slate-800/70">
                      <SubSectionHeader icon={Search} title="Web Search" count={sources.blind_search!.reduce((acc, q) => acc + q.results.length, 0)} />
                      <div className="space-y-4">
                        {sources.blind_search!
                          .filter(q => q.results.length > 0)
                          .map((q, i) => (
                            <div key={i}>
                              <p className="text-xs text-slate-500 mb-1.5">Query: "{q.query}"</p>
                              <div className="space-y-2">
                                {q.results.map((r, j) => (
                                  <div key={j} className="bg-slate-800/50 border border-slate-700/50 rounded-xl p-3">
                                    <SearchResultItem result={r} />
                                  </div>
                                ))}
                              </div>
                            </div>
                          ))}
                      </div>
                    </section>
                  )}

                  {/* Targeted search from interview claims (Pass B) */}
                  {hasTargetedSearch && (
                    <section className="pt-6 border-t border-slate-800/70">
                      <SubSectionHeader icon={Search} title="Targeted Search (from Interview Claims)" count={sources.targeted_search!.reduce((acc, q) => acc + q.results.length, 0)} />
                      <div className="space-y-4">
                        {sources.targeted_search!.map((entry, i) => (
                          <div key={i}>
                            <p className="text-xs text-slate-500 mb-1.5">Claim: "{entry.claim}"</p>
                            <div className="space-y-2">
                              {entry.results.map((r, j) => (
                                <div key={j} className="bg-slate-800/50 border border-slate-700/50 rounded-xl p-3">
                                  <SearchResultItem result={r} />
                                </div>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    </section>
                  )}
                </div>
              </details>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
