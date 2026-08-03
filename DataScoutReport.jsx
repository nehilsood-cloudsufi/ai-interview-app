import { useState, useMemo } from "react";
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
} from "lucide-react";

/* ── sample report data ── */
const COMPANY = "CloudSufi";
const REP_NAME = "Irfan Khan";
const REP_ROLE = "CEO";
const REPORT_DATE = "July 23, 2026";

const CLAIMS = [
  {
    status: "CONTRADICTED",
    claim: "Revenue approximately $120 million USD",
    note: "Public sources estimate annual revenue between $17.8 million and $18.9 million.",
    sources: [
      { name: "ZoomInfo", url: "zoominfo.com/c/cloudsufi", text: "Annual revenue reported at $17.8 million" },
      { name: "Growjo", url: "growjo.com/company/CLOUDSUFI", text: "Estimated revenue $18.9 million annually" },
      { name: "RocketReach", url: "rocketreach.co/cloudsufi-profile", text: "Annual revenue $17.8 million in 2026" },
    ],
  },
  {
    status: "CONTRADICTED",
    claim: "Founded in 2017",
    note: "Multiple public records consistently place the founding year at 2019.",
    sources: [
      { name: "LinkedIn", url: "linkedin.com/company/cloudsufi", text: "Company founded in 2019" },
      { name: "Crunchbase", url: "crunchbase.com/organization/cloudsufi", text: "Founded 2019, cloud-based decision support" },
      { name: "Tracxn", url: "tracxn.com/d/companies/cloudsufi", text: "Founded in 2019, unfunded" },
    ],
  },
  {
    status: "CONTRADICTED",
    claim: "Headquartered in New York",
    note: "All public sources place headquarters in San Jose, California.",
    sources: [
      { name: "ZoomInfo", url: "zoominfo.com/c/cloudsufi", text: "San Jose, California, United States" },
      { name: "CloudSufi", url: "cloudsufi.com/about-us", text: "333 West San Carlos Street, San Jose, CA 95110" },
    ],
  },
  {
    status: "CONTRADICTED",
    claim: "Primary cloud partner is AWS",
    note: "Public industry reporting emphasizes Google Cloud as the primary strategic partner.",
    sources: [
      { name: "CRN", url: "crn.com/news/ai/2026/cloudsufi", text: "Identified as a top Google Cloud Premier partner" },
      { name: "CloudSufi", url: "cloudsufi.com/partners", text: "Partnership with Google Cloud" },
      { name: "PR Newswire", url: "prnewswire.com/news-releases/cloudsufi", text: "Google Cloud AI technology" },
    ],
  },
  {
    status: "CONTRADICTED",
    claim: "Google Cloud engagement started in 2018",
    note: "Company was founded in 2019. No source supports a 2018 engagement start date.",
    sources: [
      { name: "LinkedIn", url: "linkedin.com/company/cloudsufi", text: "Founded 2019" },
      { name: "Crunchbase", url: "crunchbase.com/organization/cloudsufi", text: "Founded 2019" },
    ],
  },
  {
    status: "PARTIAL",
    claim: "800 employees globally",
    note: "Public sources report employee counts ranging from 111 to 500.",
    sources: [
      { name: "LinkedIn", url: "linkedin.com/company/cloudsufi", text: "Company size 201–500 employees" },
      { name: "RocketReach", url: "rocketreach.co/cloudsufi-profile", text: "Reports 111 employees" },
      { name: "Growjo", url: "growjo.com/company/CLOUDSUFI", text: "130 employees, 2% annual growth" },
    ],
  },
  {
    status: "PARTIAL",
    claim: "Gen AI Lab has over 500 AI specialists",
    note: "Public sources describe 500 as a hiring target or capacity, not confirmed headcount.",
    sources: [
      { name: "CloudSufi", url: "cloudsufi.com", text: "Gen AI Lab hiring 500 experts" },
      { name: "PR Newswire", url: "prnewswire.com/news-releases/cloudsufi", text: "Lab capacity of over 500 AI professionals" },
    ],
  },
  {
    status: "VERIFIED",
    claim: "Irfan Khan is the CEO",
    note: "Confirmed as President, CEO, Co-Founder, and Chairman of the Board across multiple sources.",
    sources: [
      { name: "CloudSufi", url: "cloudsufi.com/about-us", text: "President & CEO, Co-Founder, Chairman of the Board" },
      { name: "Forbes Council", url: "councils.forbes.com/profile/Irfan-Khan", text: "Listed as President and CEO" },
      { name: "RocketReach", url: "rocketreach.co/cloudsufi-management", text: "CEO and President" },
    ],
  },
  {
    status: "VERIFIED",
    claim: "Specializes in data engineering and AI",
    note: "Consistently described as a data engineering and AI company across all sources.",
    sources: [
      { name: "ZoomInfo", url: "zoominfo.com/c/cloudsufi", text: "Specializes in data engineering and advanced analytics" },
      { name: "PR Newswire", url: "prnewswire.com/news-releases/cloudsufi", text: "Global AI and data innovation company" },
    ],
  },
  {
    status: "VERIFIED",
    claim: "Offers Velocity Packs and Antifragile Supply Chain solutions",
    note: "Both products documented on the company website with detailed service descriptions.",
    sources: [
      { name: "CloudSufi", url: "cloudsufi.com/book-workshop", text: "Velocity Packs listed across multiple industries" },
      { name: "CloudSufi", url: "cloudsufi.com", text: "Antifragile Supply Chain Visibility and Demand Sensing" },
    ],
  },
  {
    status: "VERIFIED",
    claim: "Offices in the US, India, and Mexico",
    note: "Confirmed across website, press releases, and third-party profiles.",
    sources: [
      { name: "CloudSufi", url: "cloudsufi.com/about-us", text: "San Jose, Atlanta, Boston, Dallas, Noida, Guadalajara" },
      { name: "PR Newswire", url: "prnewswire.com/news-releases/cloudsufi", text: "Enterprise AI Factory in Guadalajara, Mexico" },
    ],
  },
  {
    status: "NO_DATA",
    claim: "Gartner #1 data consulting firm ranking 2025",
    note: "No public sources reference any Gartner ranking for CloudSufi.",
    sources: [],
  },
  {
    status: "NO_DATA",
    claim: "Forbes fastest growing data company 2024",
    note: "No public sources reference this recognition.",
    sources: [],
  },
  {
    status: "NO_DATA",
    claim: "AWS Advanced Partner and Microsoft Gold Partner status",
    note: "No public sources found. All references point to Google Cloud Premier Partner.",
    sources: [],
  },
  {
    status: "NO_DATA",
    claim: "US federal government engagements",
    note: "No public sources found referencing federal government work.",
    sources: [],
  },
  {
    status: "NO_DATA",
    claim: "Client retention rate of 95%",
    note: "No public data found on retention metrics.",
    sources: [],
  },
];

const ADDITIONAL = [
  { text: "Enterprise AI Factory opened in Guadalajara, Mexico in July 2026 for same-timezone North American delivery.", url: "prnewswire.com" },
  { text: "Boston Innovation Lab launched December 2025 with Google Cloud AI technology.", url: "prnewswire.com" },
  { text: "Glassdoor rating 4.1/5 from 138 reviews. 77% CEO approval. Mixed feedback on work-life balance.", url: "glassdoor.com" },
  { text: "AmbitionBox rating 3.9/5 from 58 reviews. Culture praised. Some report tight deadlines and hire-fire concerns.", url: "ambitionbox.com" },
  { text: "CEO Irfan Khan previously held executive roles at Bristlecone, Microsoft, and Hughes Software.", url: "cloudsufi.com" },
  { text: "CLOUDSUFI India Private Limited incorporated April 2020, registered under ROC Delhi.", url: "tracxn.com" },
  { text: "Company listed as unfunded on Tracxn — no recorded institutional funding rounds.", url: "tracxn.com" },
  { text: "CRN featured CEO discussing shift toward industrialized AI factories for enterprise clients in 2026.", url: "crn.com" },
];

/* ── status config ── */
const STATUS = {
  CONTRADICTED: { label: "Contradicted", icon: XCircle, color: "#ef4444", bg: "rgba(239,68,68,0.08)", border: "rgba(239,68,68,0.2)" },
  PARTIAL: { label: "Partial", icon: AlertTriangle, color: "#f59e0b", bg: "rgba(245,158,11,0.08)", border: "rgba(245,158,11,0.2)" },
  VERIFIED: { label: "Verified", icon: CheckCircle2, color: "#22c55e", bg: "rgba(34,197,94,0.08)", border: "rgba(34,197,94,0.2)" },
  NO_DATA: { label: "No Data", icon: HelpCircle, color: "#64748b", bg: "rgba(100,116,139,0.08)", border: "rgba(100,116,139,0.2)" },
};

const ORDER = ["CONTRADICTED", "PARTIAL", "VERIFIED", "NO_DATA"];

export default function DataScoutReport() {
  const [openGroup, setOpenGroup] = useState("CONTRADICTED");
  const [openClaims, setOpenClaims] = useState({});

  const grouped = useMemo(() => {
    const g = { CONTRADICTED: [], PARTIAL: [], VERIFIED: [], NO_DATA: [] };
    CLAIMS.forEach((c) => g[c.status]?.push(c));
    return g;
  }, []);

  const counts = useMemo(() => {
    const c = {};
    ORDER.forEach((s) => (c[s] = grouped[s].length));
    c.total = CLAIMS.length;
    return c;
  }, [grouped]);

  const toggleClaim = (id) => setOpenClaims((prev) => ({ ...prev, [id]: !prev[id] }));

  return (
    <div style={{ minHeight: "100vh", background: "#0b1120", color: "#cbd5e1", fontFamily: "'Inter', -apple-system, sans-serif" }}>

      {/* ── HEADER ── */}
      <header style={{ position: "sticky", top: 0, zIndex: 10, background: "rgba(11,17,32,0.92)", backdropFilter: "blur(12px)", borderBottom: "1px solid rgba(51,65,85,0.4)" }}>
        <div style={{ maxWidth: 900, margin: "0 auto", padding: "14px 28px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <div style={{ width: 36, height: 36, borderRadius: 10, background: "rgba(99,102,241,0.12)", border: "1px solid rgba(99,102,241,0.25)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <FileText size={18} color="#818cf8" />
            </div>
            <div>
              <h1 style={{ fontSize: 16, fontWeight: 600, color: "#f1f5f9", margin: 0, lineHeight: 1.3 }}>
                Data Scout Report
              </h1>
              <p style={{ fontSize: 12, color: "#64748b", margin: 0, display: "flex", alignItems: "center", gap: 6 }}>
                <Building2 size={11} /> {COMPANY}
                <span style={{ margin: "0 4px", color: "#334155" }}>·</span>
                {REP_NAME}, {REP_ROLE}
                <span style={{ margin: "0 4px", color: "#334155" }}>·</span>
                <Clock size={11} /> {REPORT_DATE}
              </p>
            </div>
          </div>
          <button style={{ display: "flex", alignItems: "center", gap: 6, background: "rgba(30,41,59,0.7)", border: "1px solid rgba(51,65,85,0.5)", color: "#94a3b8", fontSize: 13, fontWeight: 500, padding: "7px 14px", borderRadius: 8, cursor: "pointer" }}>
            <Download size={14} /> Export
          </button>
        </div>
      </header>

      <main style={{ maxWidth: 900, margin: "0 auto", padding: "24px 28px" }}>

        {/* ── DISCLAIMER ── */}
        <p style={{ fontSize: 11, color: "#64748b", background: "rgba(15,23,42,0.5)", border: "1px solid rgba(30,41,59,0.5)", borderRadius: 8, padding: "10px 16px", lineHeight: 1.6, marginBottom: 20 }}>
          Built from unverified public web sources. Informational only — do not use as the sole basis for a vendor decision.
        </p>

        {/* ── STATS ── */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 10, marginBottom: 28 }}>
          {ORDER.map((s) => {
            const cfg = STATUS[s];
            const Icon = cfg.icon;
            return (
              <div key={s} style={{ background: "rgba(15,23,42,0.6)", border: "1px solid rgba(30,41,59,0.5)", borderRadius: 10, padding: "14px 16px", textAlign: "center" }}>
                <Icon size={16} color={cfg.color} style={{ marginBottom: 4 }} />
                <div style={{ fontSize: 22, fontWeight: 700, color: cfg.color, lineHeight: 1.2 }}>{counts[s]}</div>
                <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>{cfg.label}</div>
              </div>
            );
          })}
          <div style={{ background: "rgba(15,23,42,0.6)", border: "1px solid rgba(30,41,59,0.5)", borderRadius: 10, padding: "14px 16px", textAlign: "center" }}>
            <FileText size={16} color="#818cf8" style={{ marginBottom: 4 }} />
            <div style={{ fontSize: 22, fontWeight: 700, color: "#818cf8", lineHeight: 1.2 }}>{counts.total}</div>
            <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>Total</div>
          </div>
        </div>

        {/* ── SECTION LABEL ── */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20 }}>
          <h2 style={{ fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.08em", color: "#64748b", margin: 0 }}>
            Interview Claims
          </h2>
          <div style={{ flex: 1, height: 1, background: "rgba(51,65,85,0.3)" }} />
        </div>

        {/* ── CLAIM GROUPS ── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16, marginBottom: 36 }}>
          {ORDER.map((status) => {
            if (grouped[status].length === 0) return null;
            const cfg = STATUS[status];
            const Icon = cfg.icon;
            const isOpen = openGroup === status;

            return (
              <div key={status}>
                {/* group header */}
                <button
                  onClick={() => setOpenGroup(isOpen ? null : status)}
                  style={{ display: "flex", alignItems: "center", gap: 8, background: "none", border: "none", cursor: "pointer", padding: "0 0 8px 0", width: "100%" }}
                >
                  <Icon size={15} color={cfg.color} />
                  <span style={{ fontSize: 13, fontWeight: 600, color: cfg.color }}>{cfg.label}</span>
                  <span style={{ fontSize: 11, color: "#475569", background: "rgba(30,41,59,0.6)", padding: "2px 8px", borderRadius: 10 }}>
                    {grouped[status].length}
                  </span>
                  <div style={{ flex: 1 }} />
                  {isOpen ? <ChevronUp size={14} color="#475569" /> : <ChevronDown size={14} color="#475569" />}
                </button>

                {/* claims list */}
                {isOpen && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {grouped[status].map((claim, idx) => {
                      const claimId = `${status}-${idx}`;
                      const isExpanded = openClaims[claimId];

                      return (
                        <div
                          key={claimId}
                          style={{
                            background: cfg.bg,
                            border: `1px solid ${cfg.border}`,
                            borderRadius: 10,
                            overflow: "hidden",
                            transition: "all 0.15s ease",
                          }}
                        >
                          {/* claim header */}
                          <button
                            onClick={() => toggleClaim(claimId)}
                            style={{
                              width: "100%",
                              display: "flex",
                              alignItems: "flex-start",
                              gap: 12,
                              padding: "14px 18px",
                              background: "none",
                              border: "none",
                              cursor: "pointer",
                              textAlign: "left",
                            }}
                          >
                            {/* badge */}
                            <span style={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 5,
                              background: cfg.color,
                              color: "#fff",
                              fontSize: 10,
                              fontWeight: 700,
                              textTransform: "uppercase",
                              letterSpacing: "0.05em",
                              padding: "4px 10px",
                              borderRadius: 5,
                              whiteSpace: "nowrap",
                              flexShrink: 0,
                            }}>
                              <Icon size={11} />
                              {cfg.label}
                            </span>

                            <div style={{ flex: 1, minWidth: 0 }}>
                              <p style={{ fontSize: 14, fontWeight: 500, color: "#e2e8f0", margin: 0, lineHeight: 1.4 }}>
                                {claim.claim}
                              </p>
                              {!isExpanded && (
                                <p style={{ fontSize: 13, color: "#94a3b8", margin: "4px 0 0", lineHeight: 1.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                  {claim.note}
                                </p>
                              )}
                            </div>

                            {isExpanded ? <ChevronUp size={14} color="#475569" style={{ marginTop: 2, flexShrink: 0 }} /> : <ChevronDown size={14} color="#475569" style={{ marginTop: 2, flexShrink: 0 }} />}
                          </button>

                          {/* expanded detail */}
                          {isExpanded && (
                            <div style={{ padding: "0 18px 16px", marginTop: -4 }}>
                              <p style={{ fontSize: 13, color: "#cbd5e1", lineHeight: 1.7, margin: "0 0 12px" }}>
                                {claim.note}
                              </p>

                              {claim.sources.length > 0 && (
                                <div style={{ borderTop: "1px solid rgba(51,65,85,0.25)", paddingTop: 10 }}>
                                  <p style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.1em", color: "#475569", margin: "0 0 8px" }}>
                                    Sources
                                  </p>
                                  {claim.sources.map((src, si) => (
                                    <div key={si} style={{ display: "flex", alignItems: "flex-start", gap: 8, marginBottom: 6 }}>
                                      <ExternalLink size={12} color="#475569" style={{ marginTop: 3, flexShrink: 0 }} />
                                      <p style={{ fontSize: 13, color: "#94a3b8", margin: 0, lineHeight: 1.5 }}>
                                        <span style={{ color: "#818cf8", fontWeight: 500 }}>{src.name}</span>
                                        <span style={{ color: "#334155", margin: "0 6px" }}>·</span>
                                        {src.text}
                                        <span style={{ color: "#475569", fontSize: 11, marginLeft: 6 }}>({src.url})</span>
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
              </div>
            );
          })}
        </div>

        {/* ── ADDITIONAL FINDINGS ── */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
          <h2 style={{ fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.08em", color: "#64748b", margin: 0 }}>
            Additional Findings
          </h2>
          <div style={{ flex: 1, height: 1, background: "rgba(51,65,85,0.3)" }} />
        </div>

        <div style={{ background: "rgba(15,23,42,0.4)", border: "1px solid rgba(30,41,59,0.4)", borderRadius: 12, overflow: "hidden" }}>
          {ADDITIONAL.map((item, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 12,
                padding: "14px 20px",
                borderBottom: i < ADDITIONAL.length - 1 ? "1px solid rgba(30,41,59,0.3)" : "none",
              }}
            >
              <span style={{
                width: 22,
                height: 22,
                borderRadius: "50%",
                background: "rgba(99,102,241,0.1)",
                border: "1px solid rgba(99,102,241,0.2)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 10,
                fontWeight: 600,
                color: "#818cf8",
                flexShrink: 0,
                marginTop: 1,
              }}>
                {i + 1}
              </span>
              <div>
                <p style={{ fontSize: 13, color: "#cbd5e1", lineHeight: 1.6, margin: 0 }}>{item.text}</p>
                <p style={{ fontSize: 11, color: "#475569", margin: "4px 0 0", display: "flex", alignItems: "center", gap: 4 }}>
                  <ExternalLink size={10} />
                  <span style={{ color: "#6366f1" }}>{item.url}</span>
                </p>
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
