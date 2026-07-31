import type { ReactNode } from 'react';

/**
 * Minimal renderer for the Data Scout report's constrained Markdown dialect
 * (see the backend's scout_system_prompt, which is engineered to emit exactly
 * these constructs): `## ` section headings, `**bold**`, backtick-wrapped
 * status labels (rendered as colored pill badges), other inline code,
 * [text](url) links, `- ` bullets, and `↳ ` source lines. Deliberately not a
 * general-purpose Markdown library — the report format is a fixed contract,
 * and anything outside it renders as plain text rather than pulling a
 * renderer dependency into the bundle.
 */

// Status labels the synthesis prompt wraps in literal backticks — each maps
// to a pill color; any other backtick span renders as ordinary inline code.
const LABEL_STYLES: Record<string, string> = {
  CONTRADICTED: 'bg-red-500/15 text-red-300 border-red-500/40',
  PARTIAL: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  VERIFIED: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  'NO DATA': 'bg-slate-500/15 text-slate-300 border-slate-500/40',
};

const INLINE_RE = /(\*\*[^*]+?\*\*|`[^`]+?`|\[[^\]]+?\]\(https?:\/\/[^)\s]+?\))/g;
const LINK_RE = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/;

function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE_RE)) {
    const token = match[0];
    const start = match.index ?? 0;
    if (start > last) nodes.push(text.slice(last, start));
    if (token.startsWith('**')) {
      nodes.push(
        <strong key={start} className="font-semibold text-slate-100">
          {token.slice(2, -2)}
        </strong>,
      );
    } else if (token.startsWith('`')) {
      const label = token.slice(1, -1);
      const pill = LABEL_STYLES[label];
      nodes.push(
        pill ? (
          <span
            key={start}
            className={`inline-block border rounded-full px-2 py-0.5 text-[11px] font-semibold tracking-wide align-middle ${pill}`}
          >
            {label}
          </span>
        ) : (
          <code key={start} className="text-xs bg-slate-700/50 rounded px-1">
            {label}
          </code>
        ),
      );
    } else {
      const link = token.match(LINK_RE);
      if (link) {
        nodes.push(
          <a
            key={start}
            href={link[2]}
            target="_blank"
            rel="noreferrer"
            className="text-sky-400 hover:underline"
          >
            {link[1]}
          </a>,
        );
      } else {
        nodes.push(token);
      }
    }
    last = start + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function renderLine(line: string, key: number): ReactNode {
  const trimmed = line.trim();
  if (!trimmed) return <div key={key} className="h-2" />;
  if (trimmed.startsWith('## ')) {
    return (
      <h4 key={key} className="text-sm font-semibold text-slate-100 mt-4 mb-1 first:mt-0">
        {renderInline(trimmed.slice(3))}
      </h4>
    );
  }
  if (trimmed.startsWith('↳')) {
    return (
      <p key={key} className="pl-4 text-xs text-slate-400">
        {renderInline(trimmed)}
      </p>
    );
  }
  if (trimmed.startsWith('- ')) {
    return (
      <p key={key} className="pl-4 relative before:content-['•'] before:absolute before:left-1 before:text-slate-500">
        {renderInline(trimmed.slice(2))}
      </p>
    );
  }
  return <p key={key}>{renderInline(trimmed)}</p>;
}

export function ScoutMarkdown({ markdown }: { markdown: string }) {
  return (
    <div className="text-sm text-slate-200 leading-relaxed">
      {markdown.split('\n').map((line, index) => renderLine(line, index))}
    </div>
  );
}
