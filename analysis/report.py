#!/usr/bin/env python3
"""Build a self-contained HTML report from the .NET + Go experiment outputs.

Reads diagnostics/{dotnet,go}/{cells.json,metrics.json} and emits analysis/report.html
with inline SVG charts (no external assets — Artifact CSP-safe) plus full per-query
metric tables. Pure standard library.
"""
import json
import html
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIAG = ROOT / "diagnostics"
OUT = ROOT / "analysis" / "report.html"

DEPTH_ORDER = ["3-hpk", "2-hpk", "1-hpk", "0-hpk"]
DEPTH_LABEL = {"3-hpk": "3 HPK (y+m+d)", "2-hpk": "2 HPK (y+m)", "1-hpk": "1 HPK (y)", "0-hpk": "0 HPK (none)"}
# Series colours = the two SDKs' real brand identities (.NET purple, Go cyan).
SDK_COLOR = {"dotnet": "#512BD4", "go": "#00ADD8"}


def load(sdk, name):
    p = DIAG / sdk / name
    return json.loads(p.read_text()) if p.exists() else []


def esc(x):
    return html.escape(str(x))


def fmt(x, nd=1):
    if isinstance(x, (int, float)):
        return f"{x:,.{nd}f}" if x % 1 else f"{int(x):,}"
    return esc(x)


# ---------------------------------------------------------------- SVG helpers
def grouped_bars(title, cats, series, ylabel, unit="", height=240, logscale=False):
    """series = {name: {cat: value}}. Grouped bar chart as inline SVG."""
    W, H = 720, height
    padL, padR, padB, padT = 64, 12, 46, 30
    plotW, plotH = W - padL - padR, H - padB - padT
    names = list(series)
    vals = [series[n].get(c, 0) or 0 for n in names for c in cats]
    vmax = max(vals + [1])
    import math as _m
    def y(v):
        if logscale:
            v = max(v, 0.1)
            return padT + plotH - (_m.log10(v) / _m.log10(max(vmax, 10))) * plotH
        return padT + plotH - (v / vmax) * plotH
    gw = plotW / len(cats)
    bw = gw / (len(names) + 0.5) * 0.9
    s = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="{esc(title)}">']
    s.append(f'<text x="{W/2}" y="18" class="ct" text-anchor="middle">{esc(title)}</text>')
    # gridlines
    for gy in range(5):
        yy = padT + plotH * gy / 4
        s.append(f'<line x1="{padL}" y1="{yy:.1f}" x2="{W-padR}" y2="{yy:.1f}" class="grid"/>')
    s.append(f'<text x="10" y="{padT+plotH/2}" class="ax" transform="rotate(-90 10 {padT+plotH/2})" text-anchor="middle">{esc(ylabel)}</text>')
    for ci, c in enumerate(cats):
        gx = padL + ci * gw
        for ni, n in enumerate(names):
            v = series[n].get(c, 0) or 0
            bx = gx + (gw - bw * len(names)) / 2 + ni * bw
            yy = y(v)
            s.append(f'<rect x="{bx:.1f}" y="{yy:.1f}" width="{bw*0.86:.1f}" height="{padT+plotH-yy:.1f}" fill="{SDK_COLOR.get(n,"#888")}"><title>{esc(n)} {esc(c)}: {fmt(v)}{unit}</title></rect>')
            if v:
                s.append(f'<text x="{bx+bw*0.43:.1f}" y="{yy-3:.1f}" class="bl" text-anchor="middle">{fmt(v)}</text>')
        s.append(f'<text x="{gx+gw/2:.1f}" y="{H-padB+16}" class="ax" text-anchor="middle">{esc(DEPTH_LABEL.get(c,c))}</text>')
    # legend
    lx = padL
    for n in names:
        s.append(f'<rect x="{lx}" y="{H-14}" width="10" height="10" fill="{SDK_COLOR.get(n,"#888")}"/>')
        s.append(f'<text x="{lx+14}" y="{H-5}" class="lg">{esc(n)}</text>')
        lx += 70
    s.append('</svg>')
    return "".join(s)


def line_chart(title, xs, series, xlabel, ylabel, height=240):
    """series = {name: [y for each x]}. Multi-line SVG."""
    W, H = 720, height
    padL, padR, padB, padT = 64, 12, 46, 30
    plotW, plotH = W - padL - padR, H - padB - padT
    allv = [v for ys in series.values() for v in ys if v is not None]
    vmax = max(allv + [1])
    def X(i): return padL + (i / max(len(xs) - 1, 1)) * plotW
    def Y(v): return padT + plotH - (v / vmax) * plotH
    s = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="{esc(title)}">']
    s.append(f'<text x="{W/2}" y="18" class="ct" text-anchor="middle">{esc(title)}</text>')
    for gy in range(5):
        yy = padT + plotH * gy / 4
        s.append(f'<line x1="{padL}" y1="{yy:.1f}" x2="{W-padR}" y2="{yy:.1f}" class="grid"/>')
        s.append(f'<text x="{padL-6}" y="{yy+3:.1f}" class="ax" text-anchor="end">{fmt(vmax*(4-gy)/4)}</text>')
    for i, xv in enumerate(xs):
        s.append(f'<text x="{X(i):.1f}" y="{H-padB+16}" class="ax" text-anchor="middle">{esc(xv)}</text>')
    s.append(f'<text x="{W/2}" y="{H-2}" class="ax" text-anchor="middle">{esc(xlabel)}</text>')
    s.append(f'<text x="12" y="{padT+plotH/2}" class="ax" transform="rotate(-90 12 {padT+plotH/2})" text-anchor="middle">{esc(ylabel)}</text>')
    for n, ys in series.items():
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(ys) if v is not None)
        col = SDK_COLOR.get(n.split(" ")[0], "#888")
        dash = ' stroke-dasharray="5 4"' if "p95" in n or "p99" in n else ""
        s.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2"{dash}/>')
        for i, v in enumerate(ys):
            if v is not None:
                s.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="2.5" fill="{col}"><title>{esc(n)} @{xs[i]}: {fmt(v)}</title></circle>')
    lx = padL
    for n in series:
        col = SDK_COLOR.get(n.split(" ")[0], "#888")
        s.append(f'<rect x="{lx}" y="{H-14}" width="10" height="10" fill="{col}"/>')
        s.append(f'<text x="{lx+14}" y="{H-5}" class="lg">{esc(n)}</text>')
        lx += max(90, len(n) * 7)
    s.append('</svg>')
    return "".join(s)


def codeblock(code):
    return f'<pre class="code"><code>{esc(code)}</code></pre>'


def table(headers, rows, right_from=1, wrap=False):
    s = [f'<div class="tw{" wrap" if wrap else ""}"><table><thead><tr>']
    for i, h in enumerate(headers):
        cls = ' class="r"' if i >= right_from else ""
        s.append(f'<th{cls}>{esc(h)}</th>')
    s.append('</tr></thead><tbody>')
    for r in rows:
        s.append('<tr>')
        for i, c in enumerate(r):
            cls = ' class="r"' if i >= right_from else ""
            s.append(f'<td{cls}>{c if isinstance(c,str) and c.startswith("<") else esc(c)}</td>')
        s.append('</tr>')
    s.append('</tbody></table></div>')
    return "".join(s)


# ---------------------------------------------------------------- data
metrics = load("dotnet", "metrics.json") + load("go", "metrics.json")
cells = load("dotnet", "cells.json") + load("go", "cells.json")
mi = {(m["sdk"], m["depth"], m["variant"], m["form"]): m for m in metrics}
sdks = [s for s in ("dotnet", "go") if any(m["sdk"] == s for m in metrics)]

# Concrete query targets (for showing exact SQL + partition-key values).
try:
    _mf = json.loads((ROOT / "config" / "seed-manifest.json").read_text())
    Y = _mf["query"]["year"]
    M = _mf["query"]["month"]["month"]
    D = _mf["query"]["day"]["day"]
except Exception:
    Y, M, D = "2024", "03", "15"


def mget(sdk, depth, variant, form, field):
    m = mi.get((sdk, depth, variant, form))
    return m.get(field) if m and not m.get("error") else None


# depth gradient uses the where-only / none variant, select form
def depth_series(field, form="select"):
    out = {}
    for sdk in sdks:
        d = {}
        for depth in DEPTH_ORDER:
            variant = "none" if depth == "0-hpk" else "where"
            v = mget(sdk, depth, variant, form, field)
            if v is not None:
                d[depth] = v
        out[sdk] = d
    return out


# Test date = when the diagnostic outputs were last written (i.e. the run date),
# derived from the data files so it always reflects the actual experiment.
_diag_mtimes = [(DIAG / sdk / n).stat().st_mtime
                for sdk in ("dotnet", "go") for n in ("metrics.json", "cells.json")
                if (DIAG / sdk / n).exists()]
TEST_DATE = datetime.date.fromtimestamp(max(_diag_mtimes)).strftime("%B %-d, %Y") if _diag_mtimes else None
REPO_URL = "https://github.com/vineethvarghese/cosmosdb-hpk"

parts = []
parts.append(f"<h1>Cosmos DB Hierarchical Partition Keys — Go vs .NET deep analysis</h1>")
_meta = (f"Tested {TEST_DATE} · " if TEST_DATE else "") + \
    f'<a href="{REPO_URL}">Source &amp; test data on GitHub ↗</a>'
parts.append(f'<p class="meta">{_meta}</p>')
nrich = len([m for m in metrics if not m.get("error")])
parts.append(f'<p class="sub">{nrich} query configs profiled · concurrency sweep 1–{max((c["concurrency"] for c in cells), default=6)} · '
             f'depth = number of HPK levels constrained. RU is server-computed, but not identical across SDKs — Go runs '
             f'~10–19% higher than .NET for the same query (see §B); latency mixes transport (.NET Direct/TCP vs Go Gateway/HTTPS). '
             f'Superscripts link to <a href="#refs">references</a>.</p>')

# ---- Abstract ----
_ab_docs = mget("dotnet", "0-hpk", "none", "select", "retrievedDocumentCount") or 0
_ab_pmin = mget("dotnet", "3-hpk", "where", "select", "partitionsContacted")
_ab_pmax = mget("dotnet", "0-hpk", "none", "select", "partitionsContacted")
_ab_rmin = mget("dotnet", "3-hpk", "where", "select", "requestCharge")
_ab_rmax = mget("dotnet", "0-hpk", "none", "select", "requestCharge")
_ab_rx = f"{int(_ab_rmax / _ab_rmin)}×" if (_ab_rmin and _ab_rmax) else "steeply"
_ab_docstr = f"{_ab_docs / 1e6:.2f}M-document" if _ab_docs else "large"
parts.append(
    '<div class="abstract"><b>Abstract.</b> This report benchmarks Azure Cosmos DB hierarchical partition keys '
    f'(HPK, <code>/year/month/day</code>) on a {_ab_docstr} container spread across {_ab_pmax or "several"} physical '
    'partitions, comparing the Go (<code>azcosmos</code>) and .NET (<code>Microsoft.Azure.Cosmos</code>) SDKs. It sweeps a '
    'matrix of prefix depth (3 / 2 / 1 / 0 HPK levels) × query expression (WHERE clause, explicit partition key, or both) '
    '× result form (SELECT&nbsp;* / COUNT), capturing request charge, latency, bytes read, documents scanned and physical '
    'partitions contacted, plus a client-concurrency sweep. Findings: deeper prefixes prune to fewer partitions '
    f'({_ab_pmax}→{_ab_pmin}), and RU scales ~{_ab_rx} by depth. The two SDKs report RU of the same order of magnitude '
    'for a given query, but not identical — Go runs ~10–19% higher than .NET (see §B) — and they diverge further in '
    'execution — .NET resolves partitions client-side and reads them in parallel over TCP (Direct mode), while Go routes '
    'and serves entirely through the gateway, making it slower on large cross-partition scans and unable to run '
    'cross-partition aggregates. The partial-<code>PartitionKey</code> over-read (#5404) is also reproduced. Methodology, '
    'per-query graphs and full data follow; the exhaustive tables are in the appendix.</div>')

# ---- Table of contents ----
parts.append(
    '<nav class="toc"><div class="toctitle">Contents</div><ul>'
    '<li><a href="#arch">A · Architecture — how a partial-HPK query routes</a></li>'
    '<li><a href="#sdkdiff">B · Go vs .NET SDK — detailed differences</a></li>'
    '<li><a href="#src">C · Why they differ — a source-code walkthrough</a></li>'
    '<li><a href="#s1">1 · Prefix depth → partitions, RU, bytes</a></li>'
    '<li><a href="#s2">2 · WHERE vs PartitionKey vs both</a></li>'
    '<li><a href="#s3">3 · Go SDK — gateway routing &amp; limits</a></li>'
    '<li><a href="#s4">4 · Client throughput as concurrency scales</a></li>'
    '<li><a href="#s5">5 · Per-query — exact SQL, HPK, Go vs .NET</a></li>'
    '<li><a href="#s6">6 · Go — full HPK: WHERE vs PartitionKey vs both</a></li>'
    '<li><a href="#appA">Appendix A · Full per-query metrics</a></li>'
    '<li><a href="#appB">Appendix B · Full concurrency-sweep cells</a></li>'
    '<li><a href="#refs">References</a></li>'
    '</ul></nav>')


# ---- Key findings (summary band, surfaced before the detail) ----
def _cell(sdk, d, v, f, c, field):
    r = next((x for x in cells if x["sdk"] == sdk and x["depth"] == d and x["variant"] == v and x["form"] == f and x["concurrency"] == c), None)
    return r.get(field) if r else None

_pmin = mget("dotnet", "3-hpk", "where", "select", "partitionsContacted")
_pmax = mget("dotnet", "0-hpk", "none", "select", "partitionsContacted")
_rumin = mget("dotnet", "3-hpk", "where", "select", "requestCharge")
_rumax = mget("dotnet", "0-hpk", "none", "select", "requestCharge")
_opk = mget("dotnet", "2-hpk", "pk", "select", "retrievedDocumentCount")
_owh = mget("dotnet", "2-hpk", "where", "select", "retrievedDocumentCount")
_goerr = len([x for x in metrics if x["sdk"] == "go" and x.get("error")])
_maxc = max((c["concurrency"] for c in cells), default=6)
_goscan = _cell("go", "0-hpk", "none", "select", _maxc, "p50")
_netscan = _cell("dotnet", "0-hpk", "none", "select", _maxc, "p50")


def _card(big, label, sub):
    return f'<div class="card"><div class="big">{big}</div><div class="cl">{esc(label)}</div><div class="cs">{esc(sub)}</div></div>'


_cards = []
if _pmin is not None and _pmax is not None:
    _cards.append(_card(f"{_pmin} → {_pmax}", "partitions contacted", "3-HPK vs 0-HPK — deeper prefix, fewer partitions"))
if _rumin and _rumax:
    _cards.append(_card(f"{_rumin:,.0f} → {_rumax:,.0f}", "RU by prefix depth (SELECT *)", f"{_rumax/_rumin:,.0f}× more work with no prefix"))
if _opk and _owh:
    _cards.append(_card(f"{_opk/_owh:,.0f}×", "partial-PK over-read", f"pk-only scans {_opk:,.0f} vs {_owh:,.0f} docs — #5404"))
if _goerr:
    _cards.append(_card(f"{_goerr}", "Go configs unsupported", "cross-partition aggregates → gateway BadRequest"))
if _goscan and _netscan:
    _cards.append(_card(f"{_goscan/1000:,.0f}s vs {_netscan/1000:,.1f}s", f"0-HPK scan p50 @ conc {_maxc}", "Go gateway (sequential) vs .NET Direct (parallel)"))
_cards.append(_card("~10–19%", "Go's RU premium vs .NET", "same query, same order of magnitude, but not identical — widest on the 0-HPK full scan"))
parts.append('<div class="cards">' + "".join(_cards) + "</div>")


# ---- Section A: architecture narrative + captured evidence ----
def _go_routing_rows():
    out = []
    for depth, variant, form, desc in [
        ("2-hpk", "where", "select", "WHERE year+month — 2-HPK prefix"),
        ("1-hpk", "where", "select", "WHERE year — 1-HPK prefix"),
        ("0-hpk", "none", "select", "no prefix — full scan")]:
        f = DIAG / "go" / f"{depth}-{variant}-{form}.diag.json"
        if not f.exists():
            continue
        caps = json.loads(f.read_text())
        ids = [c.get("partitionKeyRangeId") for c in caps if c.get("partitionKeyRangeId")]
        uniq = sorted(set(ids), key=lambda x: int(x) if x.isdigit() else 999)
        out.append([desc, f'<code>{",".join(uniq) or "—"}</code>', str(len(uniq)), str(len(caps))])
    return out


def _dotnet_evidence():
    import re
    for cand in ("1-hpk-where-select", "2-hpk-where-select", "0-hpk-none-select"):
        f = DIAG / "dotnet" / f"{cand}.diag.json"
        if f.exists():
            t = f.read_text()
            stages = sorted(set(re.findall(r'"name":"(Get Partition Key Range[^"]*)"', t)))
            rntbd = sorted(set(re.findall(r'rntbd://[^/"]+', t)))
            guids = sorted(set(re.findall(r'partitions/([0-9a-f-]{8,})', t)))
            return stages, rntbd, guids
    return [], [], []


arch = ['<h2 id="arch">A · Architecture — how a partial-HPK query routes, and why the SDKs differ</h2>']
arch.append('<p><b>Where that routing decision is made differs between the two SDKs.</b> When a query constrains a '
            '<em>prefix</em> of the hierarchical key (only <code>year</code>, or <code>year+month</code>), Cosmos can '
            'restrict execution to the physical partitions whose hash-range overlaps that prefix, rather than fan out '
            'to all of them<sup><a href="#r1">[1]</a></sup><sup><a href="#r2">[2]</a></sup>.</p>')
arch.append(table(["", "Go (azcosmos)", ".NET (Microsoft.Azure.Cosmos, Direct)"],
    [["Who finds the partitions?", "Gateway (server-side, from WHERE)", "Client (cached partition-key-range map)"],
     ["Who serves the documents?", "Gateway pages them back", "Partition replicas directly (rntbd / TCP)"],
     ["Cross-partition parallelism", "No — sequential paging", "Yes — parallel per partition"],
     ["Cross-partition aggregate (COUNT/DISTINCT)", "Refused (BadRequest)", "Client-side pipeline"],
     ["Gateway used for", "routing AND data", "metadata only (routing map, query plan)"],
     ["Direct / TCP mode available?", "No — gateway-only", "Yes — default"]], right_from=99, wrap=True))
arch.append('<p><b>Go — gateway routes and serves.</b> azcosmos<sup><a href="#r7">[7]</a></sup> is gateway-only for queries; Direct/TCP mode exists '
            'only on the .NET and Java SDKs<sup><a href="#r3">[3]</a></sup>. The client passes an <em>empty</em> '
            'partition key plus the WHERE clause; the gateway parses it, prunes to the matching partition-key ranges, '
            'executes there, and pages results back — one round-trip per page. Cross-partition aggregates are refused '
            'by the gateway<sup><a href="#r6">[6]</a></sup>. The ranges the gateway returned (captured from the '
            '<code>x-ms-documentdb-partitionkeyrangeid</code> response header per page):</p>')
_gr = _go_routing_rows()
if _gr:
    arch.append(table(["Go query (empty PK + WHERE)", "partition-key ranges hit (gateway)", "distinct", "pages"], _gr, right_from=2))
_stages, _rntbd, _guids = _dotnet_evidence()
arch.append('<p><b>.NET — client routes, partitions serve.</b> In Direct mode (default) the client caches the '
            'partition-key-range map, computes the target ranges itself from the WHERE prefix, opens TCP (rntbd) '
            'connections <em>directly to the partition replicas</em>, and queries them in parallel; a client-side '
            'pipeline merges/aggregates (so cross-partition COUNT/DISTINCT work)<sup><a href="#r4">[4]</a></sup>. '
            'The gateway is used only for metadata. The .NET trace for the same 1-HPK query confirms this:</p>')
_ev = []
if _stages:
    _ev.append("client-side routing stages: " + ", ".join(f"<code>{esc(s)}</code>" for s in _stages))
if _rntbd:
    _ev.append("direct TCP transport: " + ", ".join(f"<code>{esc(r)}</code>" for r in _rntbd))
if _guids:
    _ev.append("partition replica(s) read directly: " + ", ".join(f"<code>{esc(g)}</code>" for g in _guids))
if _ev:
    arch.append('<ul class="ev">' + "".join(f"<li>{e}</li>" for e in _ev) + "</ul>")
arch.append('<p class="note">Consequence (see §4): .NET\'s parallel-direct execution is faster on large scans, and '
            'it can run cross-partition aggregates; Go is simpler but gateway-bottlenecked (one round-trip per page). '
            'RU is server-computed and the same order of magnitude across SDKs, but not identical — Go runs ~10–19% '
            'higher than .NET (see §B)<sup><a href="#r9">[9]</a></sup>.</p>')
parts.extend(arch)


# ---- Section B: detailed Go vs .NET SDK differences ----
def _m(sdk, d, v, f, field):
    x = mi.get((sdk, d, v, f))
    return x.get(field) if x and not x.get("error") else None


_net1 = _m("dotnet", "1-hpk", "where", "select", "requestCharge")
_go1 = _m("go", "1-hpk", "where", "select", "requestCharge")
_net3 = _m("dotnet", "3-hpk", "where", "select", "requestCharge")
_go3 = _m("go", "3-hpk", "where", "select", "requestCharge")

GO_CODE = '''pk := azcosmos.NewPartitionKey()            // empty → gateway routes from WHERE
pager := container.NewQueryItemsPager(
    "SELECT * FROM c WHERE c.year=@y AND c.month=@m", pk,
    &azcosmos.QueryOptions{QueryParameters: []azcosmos.QueryParameter{
        {Name: "@y", Value: "2024"}, {Name: "@m", Value: "03"}}})
for pager.More() {
    page, _ := pager.NextPage(ctx)          // page.Items is [][]byte
    for _, raw := range page.Items {         // you unmarshal each doc
        var o Order; _ = json.Unmarshal(raw, &o)
    }
}
// No partial partition key exists; cross-partition COUNT/DISTINCT → BadRequest'''

NET_CODE = '''var it = container.GetItemQueryIterator<Order>(   // SDK deserializes to Order
    new QueryDefinition("SELECT * FROM c WHERE c.year=@y AND c.month=@m")
        .WithParameter("@y", "2024").WithParameter("@m", "03"),
    requestOptions: new QueryRequestOptions {
        MaxConcurrency = -1,                      // parallel cross-partition fan-out
        // PartitionKey = new PartitionKeyBuilder()  // optional PARTIAL key
        //     .Add("2024").Add("03").Build()
    });
while (it.HasMoreResults) {
    FeedResponse<Order> page = await it.ReadNextAsync();
}
// Cross-partition COUNT / DISTINCT work via the client-side pipeline'''

secB = ['<h2 id="sdkdiff">B · Go vs .NET SDK — detailed differences</h2>']
secB.append('<p>Both SDKs target the same Cosmos DB NoSQL API and the same HPK container, and both return RU of the '
            'same order of magnitude for a given query (RU is computed server-side) — but not <b>identical</b>: Go '
            'runs ~10–19% higher than .NET for the same query, widening on larger scans (see below). They use '
            'different execution models, and that difference accounts for the behavioural, performance, and RU '
            'differences measured here.</p>')
secB.append(table(["Dimension", "Go — azcosmos", ".NET — Microsoft.Azure.Cosmos v3"], [
    ["Connectivity mode", "Gateway only (HTTPS / REST)", "Direct (TCP / rntbd) by default, or Gateway"],
    ["Who resolves partitions", "Gateway — server-side, from the query", "Client — cached partition-key-range map + query plan"],
    ["Who serves documents", "Gateway pages them back", "Partition replicas, directly over TCP"],
    ["Cross-partition fan-out", "Sequential — one gateway round-trip per page", "Parallel across partitions (MaxConcurrency)"],
    ["Cross-partition aggregates (COUNT / DISTINCT / GROUP BY / ORDER BY)", "Not supported — gateway returns BadRequest", "Supported — client-side query pipeline"],
    ["Partial hierarchical partition key", "Not expressible — key is empty or all N levels", "PartitionKeyBuilder supports 1..N-level (partial) keys"],
    ["Routing a partial-prefix query", "Only via the WHERE clause", "WHERE clause and/or a partial PartitionKey option"],
    ["Result materialization", "Raw [][]byte — you unmarshal each item", "Deserialized to your type<T> by the SDK"],
    ["Query diagnostics", "Thin: RequestCharge, ActivityID, QueryMetrics/IndexMetrics strings; rest via raw headers", "Rich CosmosDiagnostics: per-partition store calls, backend vs transit latency, query metrics, retries, region"],
    ["Paging / parallelism controls", "PageSizeHint", "MaxConcurrency, MaxItemCount, MaxBufferedItemCount"],
    ["Feature surface", "Leaner / newer: CRUD, batch, simple + filtered queries", "Mature: full query engine, bulk, change feed, transactional batch, encryption"],
], right_from=99, wrap=True))

secB.append('<h3 class="h3">Measured consequences (this run)</h3>')
secB.append('<ul class="ev">')
if _goscan and _netscan:
    secB.append(f'<li><b>Large cross-partition scans run substantially faster on .NET.</b> 0-HPK <code>SELECT *</code> '
                f'(1.27M docs) p50 at concurrency {_maxc}: <b>Go {_goscan/1000:,.0f} s</b> vs <b>.NET {_netscan/1000:,.1f} s</b>. '
                f'Go makes roughly 1,300 sequential gateway round-trips (one per page); .NET queries all 10 partitions in parallel over TCP.</li>')
secB.append('<li><b>Go cannot run cross-partition aggregates.</b> Any COUNT/DISTINCT not scoped to a single full '
            'partition key fails with BadRequest — including a 2-HPK COUNT whose data physically lives on one partition, '
            'because Go can’t pass a partial partition key, so the gateway treats it as cross-partition.</li>')
if _net1 and _go1 and _net3 and _go3:
    secB.append(f'<li><b>RU is server-computed but not identical — Go runs ~10–19% higher than .NET</b> for the same '
                f'query, and the gap widens with scan size: 1-HPK <code>SELECT *</code> = {_go1:,.0f} RU (Go) vs '
                f'{_net1:,.0f} RU (.NET) (+11.2%); 3-HPK = {_go3:,.0f} vs {_net3:,.0f} RU (+9.7%); the gap reaches '
                '+18.7% on the 0-HPK full scan (23,568 vs 19,859 RU). The likely cause is execution path, not '
                'transport: .NET\'s Direct-mode + Optimistic Direct Execution (§C.3) is charged less than Go\'s '
                'gateway-served execution, and ODE does not apply to the 0-HPK full scan — exactly where the gap is '
                'widest<sup><a href="#r4">[4]</a></sup>.</li>')
secB.append('<li><b>The partial-<code>PartitionKey</code> trap is .NET-only</b> (Go can’t express it): a partial '
            'PartitionKeyBuilder <em>without</em> a WHERE clause over-reads the whole physical partition<sup><a href="#r5">[5]</a></sup> (#5404<sup><a href="#r8">[8]</a></sup>). '
            'In Go the WHERE clause is the only path, which sidesteps that trap.</li>')
secB.append('</ul>')

secB.append('<h3 class="h3">The same query in each SDK</h3>')
secB.append('<div class="grid2">')
secB.append('<div><div class="cl2">Go — route via WHERE, unmarshal raw bytes</div>' + codeblock(GO_CODE) + '</div>')
secB.append('<div><div class="cl2">.NET — SDK routes &amp; deserializes; optional partial PK</div>' + codeblock(NET_CODE) + '</div>')
secB.append('</div>')

secB.append('<h3 class="h3">When to use which</h3>')
secB.append('<p>Which SDK fits depends on the workload and, for Go specifically, the partitioning strategy'
            '<sup><a href="#r12">[12]</a></sup>:</p>')
secB.append(table(["Workload", "Recommended", "Why"], [
    ["Point reads/writes, single-partition queries", "Either", "Comparable RU & latency; Go is a touch leaner"],
    ["Filtered lists on a partial-HPK prefix", "Either — put the prefix in WHERE", "Both prune correctly to the prefix’s ranges"],
    ["Large cross-partition scans", ".NET", "Parallel Direct-mode execution; Go serializes through the gateway"],
    ["Cross-partition COUNT / DISTINCT / GROUP BY / ORDER BY", ".NET (or Java)", "Go’s gateway can’t run the client-side pipeline"],
    ["Strict-firewall / simplest networking", "Go (or .NET in Gateway mode)", "Single HTTPS endpoint, no TCP port range needed"],
    ["Deep query diagnostics / per-partition tuning", ".NET", "Rich CosmosDiagnostics; Go exposes very little"],
], right_from=99, wrap=True))
parts.extend(secB)


# ---- Section C: source-code walkthrough of the SDK differences ----
GH_GO = "https://github.com/Azure/azure-sdk-for-go/blob/main/sdk/data/azcosmos"
GH_NET = "https://github.com/Azure/azure-cosmos-dotnet-v3/blob/master/Microsoft.Azure.Cosmos/src"


def _lnk(text, url):
    return f'<a href="{url}">{esc(text)}</a>'


secC = ['<h2 id="src">C · Why they differ — a source-code walkthrough</h2>']
secC.append('<p>Both SDKs talk to the same service; the behavioural differences come from the client libraries '
            '(Go<sup><a href="#r13">[13]</a></sup>, .NET<sup><a href="#r14">[14]</a></sup>), not '
            'the service itself. This section traces the source of each. Three terms first, for readers new to Cosmos: a '
            '<b>physical partition</b> is a backend server holding a slice of the data; the service divides the key space '
            'into <b>partition-key ranges</b> (one per physical partition)<sup><a href="#r11">[11]</a></sup>; and a query must reach every range that could '
            'hold matching rows. <em>Where</em> an SDK decides which ranges to hit, and <em>how</em> it reads them, is the '
            'central difference between the two.</p>')
secC.append('<p><b>In one sentence:</b> .NET is a <em>thick</em> client — it caches the partition map, plans the query, '
            'connects straight to the partition servers over TCP, and merges the results itself. Go is a <em>thin</em> '
            'client — it hands the SQL to the Cosmos <b>gateway</b> over HTTPS and lets the gateway route and execute.</p>')

secC.append('<h3 class="h3">C.1 · Connectivity — TCP to replicas vs HTTPS to the gateway</h3>')
secC.append(f'<p>.NET has two modes in {_lnk("ConnectionMode.cs", GH_NET + "/ConnectionMode.cs")}: <b>Direct</b> (the '
            'default) opens persistent <b>TCP</b> connections to the backend replicas, and <b>Gateway</b> routes '
            'everything through an HTTPS endpoint. Choosing Direct switches the protocol to TCP and unlocks TCP-only tuning '
            f'knobs ({_lnk("CosmosClientOptions.cs", GH_NET + "/CosmosClientOptions.cs")}). The Go SDK has no such choice: '
            f'its client is built on a plain HTTP pipeline (<code>azruntime.NewPipeline</code>, {_lnk("cosmos_client.go", GH_GO + "/cosmos_client.go")}) '
            '— a grep of the whole package finds no rntbd / TCP / Direct code. Go is <b>gateway-only</b><sup><a href="#r3">[3]</a></sup>.</p>')

secC.append('<h3 class="h3">C.2 · Finding the partitions — a client-side map vs the gateway</h3>')
secC.append(f'<p>.NET caches the partition layout on the client: {_lnk("PartitionKeyRangeCache.cs", GH_NET + "/Routing/PartitionKeyRangeCache.cs")} '
            f'holds a {_lnk("CollectionRoutingMap.cs", GH_NET + "/Routing/CollectionRoutingMap.cs")} whose '
            '<code>GetOverlappingRanges</code> does a binary search over the ordered ranges to answer "which physical '
            'partitions overlap this key range?" — locally, with no per-query gateway call. The Go package has no routing '
            'map and no range cache; <code>x-ms-documentdb-partitionkeyrangeid</code> appears only as a header <em>name '
            'constant</em>. Go therefore never computes which partitions a query hits — it POSTs the query and the gateway decides.</p>')

secC.append('<h3 class="h3">C.3 · Planning the query</h3>')
secC.append(f'<p>Before executing, .NET obtains a <b>query plan</b>. {_lnk("QueryPlanRetriever.cs", GH_NET + "/Query/Core/QueryPlan/QueryPlanRetriever.cs")} '
            'offers two paths — parse it <em>locally</em> with the native <code>ServiceInterop</code> library, or fetch it '
            'from the gateway; on non-Windows platforms (Linux / <b>macOS</b>, as in this run) it falls back to the gateway '
            f'round-trip. A fast-path, <b>Optimistic Direct Execution</b> ({_lnk("CosmosQueryExecutionContextFactory.cs", GH_NET + "/Query/Core/Pipeline/CosmosQueryExecutionContextFactory.cs")}), '
            'skips the plan and pipeline entirely when the query targets a single partition<sup><a href="#r4">[4]</a></sup>. '
            'Go has no query-plan step at all — the SQL string goes to the gateway as-is.</p>')

secC.append('<h3 class="h3">C.4 · Executing across partitions — and why Go can’t aggregate</h3>')
secC.append(f'<p>This is the central difference. .NET owns a <b>client-side query pipeline</b>: '
            f'{_lnk("ParallelCrossPartitionQueryPipelineStage.cs", GH_NET + "/Query/Core/Pipeline/CrossPartition/Parallel/ParallelCrossPartitionQueryPipelineStage.cs")} '
            'fans out to every target partition in parallel, and merge stages sit on top — '
            f'{_lnk("OrderBy", GH_NET + "/Query/Core/Pipeline/CrossPartition/OrderBy/OrderByCrossPartitionQueryPipelineStage.cs")} (a k-way merge of sorted streams), '
            f'{_lnk("Aggregate", GH_NET + "/Query/Core/Pipeline/Aggregate/AggregateQueryPipelineStage.cs")} (a <code>SingleGroupAggregator</code> computing SUM/COUNT/AVG across partitions), and '
            f'{_lnk("Distinct", GH_NET + "/Query/Core/Pipeline/Distinct/DistinctQueryPipelineStage.cs")} (a hash map dropping duplicates across the merged stream). '
            'Because that distribute-and-merge pipeline runs <em>inside the client</em>, .NET can compute cross-partition '
            '<code>COUNT</code> / <code>DISTINCT</code> / <code>ORDER BY</code> / <code>GROUP BY</code> itself. Go has none '
            f'of it: cross-partition is a single header <code>x-ms-documentdb-query-enablecrosspartition=true</code> '
            f'(defaulted on, {_lnk("cosmos_query_request_options.go", GH_GO + "/cosmos_query_request_options.go")}) and the '
            'gateway does the work. The Go SDK’s own doc-comment is explicit:</p>')
secC.append('<blockquote class="quote">"The Azure Cosmos DB Gateway API, used by the Go SDK, can only perform a LIMITED set '
            'of cross-partition queries. Specifically, the gateway can only perform simple projections and filtering on '
            'cross partition queries&hellip; If you provide a query that the gateway cannot execute, it will return a '
            f'BadRequest error."<cite>— {_lnk("cosmos_container.go · NewQueryItemsPager", GH_GO + "/cosmos_container.go")}<sup><a href="#r6">[6]</a></sup></cite></blockquote>')
secC.append('<p>That comment matches the measured result: every cross-partition <code>COUNT</code> failed on Go with BadRequest, while .NET returned it.</p>')

secC.append('<h3 class="h3">C.5 · Partial partition keys</h3>')
secC.append(f'<p>.NET’s {_lnk("PartitionKeyBuilder.cs", GH_NET + "/PartitionKeyBuilder.cs")} collects components via chained '
            '<code>Add(&hellip;)</code>, and <code>Build()</code> makes a key with <em>exactly</em> as many levels as you '
            'added — so 1 or 2 of a 3-level key is a valid <b>partial prefix</b>, passed via '
            '<code>QueryRequestOptions.PartitionKey</code>. In Go, a '
            f'{_lnk("partition_key.go", GH_GO + "/partition_key.go")} <code>PartitionKey</code> is just a slice of values, '
            'but the query API’s doc-comment requires <em>all</em> levels or an empty key — <em>"you must specify ALL '
            'partition keys that the container has"</em> — a query-API contract, not a limit of the <code>PartitionKey</code> '
            'type itself. So the only route to a partial prefix in Go is the WHERE clause (§A).</p>')

secC.append('<h3 class="h3">C.6 · The #5404 over-read, mechanically</h3>')
secC.append(f'<p>When a partial <code>PartitionKey</code> scopes a .NET query, '
            f'{_lnk("FeedRangePartitionKey.cs", GH_NET + "/FeedRange/FeedRanges/FeedRangePartitionKey.cs")} turns it into an '
            '<b>effective-partition-key (EPK) range</b>: a full key collapses to a point, but a prefix becomes a '
            '<code>min..max</code> sub-range. That range routes correctly to the right physical partition — but with <em>no '
            'WHERE predicate</em> there is nothing index-seekable to filter <em>within</em> the partition, so the engine '
            'enumerates the whole scoped range (and pages fill sparsely). That is an ~11× over-read measured here '
            '(343,000 vs 31,000 docs at 2-HPK), consistent with the ~30× slowdown reported in '
            f'{_lnk("issue #5404", "https://github.com/Azure/azure-cosmos-dotnet-v3/issues/5404")}<sup><a href="#r8">[8]</a></sup> '
            '(the exact backend read-scoping lives in the closed <code>Microsoft.Azure.Documents</code> layer, so this is '
            'grounded in the open source plus the issue’s own numbers). Fix: keep the WHERE clause so the index filters precisely.</p>')

secC.append('<h3 class="h3">Source map</h3>')
secC.append(table(["Behaviour", "Go — azcosmos (gateway-only)", ".NET — Microsoft.Azure.Cosmos"], [
    ["Transport", _lnk("cosmos_client.go", GH_GO + "/cosmos_client.go") + " — azcore HTTP pipeline", _lnk("ConnectionMode.cs", GH_NET + "/ConnectionMode.cs") + " — Direct/TCP (default) or Gateway"],
    ["Partition routing", "&mdash; none; the gateway routes", _lnk("PartitionKeyRangeCache.cs", GH_NET + "/Routing/PartitionKeyRangeCache.cs") + " + CollectionRoutingMap"],
    ["Query plan", "&mdash; none; SQL sent to gateway", _lnk("QueryPlanRetriever.cs", GH_NET + "/Query/Core/QueryPlan/QueryPlanRetriever.cs") + " (+ Optimistic Direct Execution)"],
    ["Cross-partition execution", _lnk("cosmos_container.go", GH_GO + "/cosmos_container.go") + " — enablecrosspartition header; gateway executes", _lnk("ParallelCrossPartitionQueryPipelineStage.cs", GH_NET + "/Query/Core/Pipeline/CrossPartition/Parallel/ParallelCrossPartitionQueryPipelineStage.cs") + " + Aggregate / Distinct / OrderBy stages"],
    ["Partial partition key", _lnk("partition_key.go", GH_GO + "/partition_key.go") + " — query API requires all levels or empty", _lnk("PartitionKeyBuilder.cs", GH_NET + "/PartitionKeyBuilder.cs") + " — partial prefix supported"],
    ["Result / diagnostics", "raw <code>[][]byte</code>; QueryMetrics / IndexMetrics strings", "deserialized to <code>T</code>; rich <code>CosmosDiagnostics</code>"],
], right_from=99, wrap=True))
secC.append('<p class="note">Go source: package <code>github.com/Azure/azure-sdk-for-go/sdk/data/azcosmos</code> v1.4.2 '
            '(read from the local module cache). .NET source: <code>Azure/azure-cosmos-dotnet-v3</code> (<code>master</code>). '
            'The low-level rntbd/TCP transport and the backend read-scoping live in the closed '
            '<code>Microsoft.Azure.Documents</code> layer and are not in the open repos.</p>')
parts.extend(secC)


# ---- Section 1: routing + RU + bytes by depth
parts.append('<h2 id="s1">1 · Prefix depth → partitions, RU, bytes (WHERE-only, SELECT *)</h2>')
parts.append('<div class="grid2">')
parts.append(grouped_bars("Physical partitions contacted", DEPTH_ORDER, depth_series("partitionsContacted"), "partitions"))
parts.append(grouped_bars("Request charge (RU)", DEPTH_ORDER, depth_series("requestCharge"), "RU", logscale=True))
parts.append(grouped_bars("Bytes read (retrieved doc size)", DEPTH_ORDER, depth_series("retrievedDocumentSize"), "bytes", logscale=True))
parts.append(grouped_bars("Docs retrieved", DEPTH_ORDER, depth_series("retrievedDocumentCount"), "docs", logscale=True))
parts.append('</div>')
parts.append('<p class="note">.NET counts distinct physical-partition GUIDs from Direct-mode store addresses (authoritative). '
             'Go counts distinct gateway <code>x-ms-documentdb-partitionkeyrangeid</code> headers — labelled gateway-reported. '
             'At 1-HPK <code>WHERE</code> <code>SELECT&nbsp;*</code> the two disagree (Go reports 3 ranges contacted, .NET '
             'reports 2 GUIDs); the .NET GUID-based count is treated as authoritative throughout this report. The 10 '
             'physical partitions were forced by provisioning the container at 100,000 RU/s autoscale, then scaling down — '
             'partition splits do not merge back<sup><a href="#r10">[10]</a></sup>.</p>')

# ---- Section 2: WHERE vs PK vs WHERE+PK (the #5404 story), .NET, select form
parts.append('<h2 id="s2">2 · WHERE vs PartitionKey vs both — RU by variant (.NET, SELECT *)</h2>')
var_series = {}
for variant in ["where", "pk", "where+pk"]:
    d = {}
    for depth in ["3-hpk", "2-hpk", "1-hpk"]:
        v = mget("dotnet", depth, variant, "select", "requestCharge")
        if v is not None:
            d[depth] = v
    var_series[variant] = d
# reuse grouped_bars but color by variant
VAR_COLOR = {"where": "#512BD4", "pk": "#d64545", "where+pk": "#2e8b57"}  # red = the bad pk-only path
_old = dict(SDK_COLOR)
SDK_COLOR.update(VAR_COLOR)
parts.append(grouped_bars("RU by variant — PartitionKey-only (no WHERE) is the #5404 slow path", ["3-hpk", "2-hpk", "1-hpk"], var_series, "RU", logscale=True))
SDK_COLOR.clear(); SDK_COLOR.update(_old)
parts.append('<p class="note">Attaching a partial <code>PartitionKeyBuilder</code> <em>without</em> a WHERE clause '
             '(<code>pk</code>) costs substantially more RU and latency than the WHERE clause (see the ~11× over-read in '
             '§C.6) — reproduces azure-cosmos-dotnet-v3#5404. The over-read is pronounced when the prefix scopes a '
             'sub-range <em>within</em> a single physical partition (2-HPK here); at 1-HPK the prefix already spans '
             'whole partitions, so <code>pk</code>-only and WHERE are comparable (15,806 vs 16,574 RU — see Appendix A).</p>')

# ---- Section 3: Go gateway routing deep-dive
parts.append('<h2 id="s3">3 · Go SDK — gateway routing &amp; capability limits</h2>')
parts.append('<p class="note">Go has no partial-prefix partition key: for 1/2-HPK it passes an <b>empty</b> key and the '
             '<b>gateway</b> routes from the WHERE clause. These are the partition-key ranges the gateway returned, '
             'plus where azcosmos <b>cannot</b> serve a query (cross-partition aggregates → error).</p>')
grows = []
for m in metrics:
    if m["sdk"] != "go":
        continue
    grows.append([f'{m["depth"]}', m["variant"], m["form"],
                  m.get("partitionsContacted", 0) if not m.get("error") else "—",
                  f'<code>{esc(m.get("pkRangeIds") or "—")}</code>' if not m.get("error") else "—",
                  f'<span class="err">{esc(m["error"])}</span>' if m.get("error") else "ok"])
parts.append(table(["depth", "variant", "form", "pkRanges (gw)", "pkRangeIds returned by gateway", "status"], grows, right_from=3))

# ---- Section 4: concurrency scaling
parts.append('<h2 id="s4">4 · Client throughput as concurrency scales (1→N)</h2>')
concs = sorted({c["concurrency"] for c in cells})
def cell_series(depth, variant, form, field):
    out = {}
    for sdk in sdks:
        ys = []
        for cc in concs:
            row = next((c for c in cells if c["sdk"] == sdk and c["depth"] == depth and c["variant"] == variant and c["form"] == form and c["concurrency"] == cc), None)
            ys.append(row[field] if row else None)
        out[sdk] = ys
    return out
# pick representative queries: 3-hpk point read, 1-hpk scan, 0-hpk full fan-out
reps = [("3-hpk", "where", "select", "3-HPK day (point-ish)"),
        ("1-hpk", "where", "count", "1-HPK year COUNT"),
        ("0-hpk", "none", "count", "0-HPK full-scan COUNT")]
parts.append('<div class="grid2">')
for depth, variant, form, label in reps:
    parts.append(line_chart(f"Throughput — {label}", concs, cell_series(depth, variant, form, "qps"), "concurrency", "queries/s"))
    lat = {}
    for sdk in sdks:
        for stat in ["p50", "p95"]:
            ys = [next((c[stat] for c in cells if c["sdk"] == sdk and c["depth"] == depth and c["variant"] == variant and c["form"] == form and c["concurrency"] == cc), None) for cc in concs]
            lat[f"{sdk} {stat}"] = ys
    parts.append(line_chart(f"Latency — {label}", concs, lat, "concurrency", "ms"))
parts.append('</div>')

# ---- Appendix A: full per-query metric table (detailed numbers, tucked at the end) ----
appendix = ['<h2 id="appA">Appendix A · Full per-query metrics (rich probe, concurrency 1)</h2>']
cols = ["sdk", "depth", "variant", "form", "requestCharge", "partitionsContacted",
        "retrievedDocumentCount", "retrievedDocumentSize", "outputDocumentCount", "outputDocumentSize",
        "indexLookupMs", "documentLoadMs", "totalExecMs", "pages"]
hdr = ["SDK", "depth", "variant", "form", "RU", "parts", "retDocs", "retBytes", "outDocs", "outBytes", "idxMs", "loadMs", "execMs", "pages"]
mrows = []
for m in sorted(metrics, key=lambda m: (DEPTH_ORDER.index(m["depth"]) if m["depth"] in DEPTH_ORDER else 9, m["variant"], m["form"], m["sdk"])):
    if m.get("error"):
        mrows.append([m["sdk"], m["depth"], m["variant"], m["form"]] + [f'<span class="err">{esc(m["error"])}</span>'] + [""] * 9)
    else:
        mrows.append([m[c] if not isinstance(m.get(c), float) else fmt(m[c]) for c in cols])
appendix.append(table(hdr, mrows, right_from=4))

# ---- Appendix B: full concurrency-sweep cells (detailed numbers) ----
appendix.append('<h2 id="appB">Appendix B · Full concurrency-sweep cells</h2>')
ccols = ["sdk", "depth", "variant", "form", "concurrency", "qps", "ruPerSec", "p50", "p95", "p99", "max", "completions", "errors"]
chdr = ["SDK", "depth", "variant", "form", "conc", "qps", "RU/s", "p50", "p95", "p99", "max", "n", "err"]
crows = [[c[k] if not isinstance(c.get(k), float) else fmt(c[k]) for k in ccols]
         for c in sorted(cells, key=lambda c: (DEPTH_ORDER.index(c["depth"]) if c["depth"] in DEPTH_ORDER else 9, c["variant"], c["form"], c["sdk"], c["concurrency"]))]
appendix.append(table(chdr, crows, right_from=4))

# ---- Section 5 (was 7): per-query — exact SQL + HPK + Go vs .NET ----
parts.append('<h2 id="s5">5 · Per-query — exact SQL, HPK used, and Go vs .NET performance</h2>')
parts.append(f'<p class="note">Query targets: year=<code>{esc(Y)}</code>, month=<code>{esc(M)}</code>, day=<code>{esc(D)}</code>. '
             'Each query shows its <b>exact SQL</b> and the <b>partition key used</b>, then latency vs concurrency '
             '(p50 solid · p95 dashed) and the full sweep data for both SDKs. Where Go cannot express or execute a query, '
             'that is stated and only .NET is shown.</p>')

_go_keys = {(c["depth"], c["variant"], c["form"]) for c in cells if c["sdk"] == "go"}
_net_keys = {(c["depth"], c["variant"], c["form"]) for c in cells if c["sdk"] == "dotnet"}


def _q_exact(depth, variant, form):
    sel = "SELECT VALUE COUNT(1)" if form == "count" else "SELECT *"
    pred = {"3-hpk": f'c.year = "{Y}" AND c.month = "{M}" AND c.day = "{D}"',
            "2-hpk": f'c.year = "{Y}" AND c.month = "{M}"',
            "1-hpk": f'c.year = "{Y}"', "0-hpk": ""}[depth]
    if variant == "pk" or depth == "0-hpk":
        return f"{sel} FROM c"
    return f"{sel} FROM c WHERE {pred}"


def _hpk_used(depth, variant):
    lv = {"3-hpk": 3, "2-hpk": 2, "1-hpk": 1, "0-hpk": 0}[depth]
    if variant in ("pk", "where+pk"):
        vals = {"3-hpk": f'"{Y}", "{M}", "{D}"', "2-hpk": f'"{Y}", "{M}"', "1-hpk": f'"{Y}"'}[depth]
        kind = "full key (3 of 3 levels)" if lv == 3 else f"partial prefix ({lv} of 3 levels)"
        return f"PartitionKey = [{vals}] — {kind}"
    if depth == "0-hpk":
        return "PartitionKey = none — full cross-partition scan (all partitions)"
    return f"PartitionKey = none — routed by WHERE prefix ({'/'.join(['year', 'month', 'day'][:lv])})"


def _go_status(depth, variant, form):
    if (depth, variant, form) in _go_keys:
        return None
    gm = mi.get(("go", depth, variant, form))
    if gm and gm.get("error"):
        return "Go cannot execute this — " + esc(gm["error"][:100])
    return "Go cannot express this — the azcosmos query API requires a full or empty partition key (all levels or none); a partial prefix must go in the WHERE clause instead"


_allkeys = sorted(_net_keys, key=lambda k: (DEPTH_ORDER.index(k[0]) if k[0] in DEPTH_ORDER else 9, k[1], k[2]))
for _depth, _variant, _form in _allkeys:
    def _row(sdk, cc, field, d=_depth, v=_variant, f=_form):
        r = next((c for c in cells if c["sdk"] == sdk and c["depth"] == d and c["variant"] == v and c["form"] == f and c["concurrency"] == cc), None)
        return r.get(field) if r else None
    _status = _go_status(_depth, _variant, _form)
    _lat = {}
    for _sdk in ["dotnet", "go"]:
        for _stat in ["p50", "p95"]:
            _ys = [_row(_sdk, cc, _stat) for cc in concs]
            if any(y is not None for y in _ys):
                _lat[f"{_sdk} {_stat}"] = _ys
    parts.append(f'<h3 class="h3">{esc(_depth)} · {esc(_variant)} · {esc(_form)}</h3>')
    parts.append(f'<div class="qsql"><span class="ql">SQL</span><code>{esc(_q_exact(_depth, _variant, _form))}</code></div>')
    parts.append(f'<div class="qsql"><span class="ql">HPK</span><code>{esc(_hpk_used(_depth, _variant))}</code></div>')
    if _status:
        parts.append(f'<div class="qnote">{_status}</div>')
    parts.append(line_chart("Latency vs concurrency (ms)", concs, _lat, "concurrency", "ms"))

    def _cv(sdk, cc, field):
        v = _row(sdk, cc, field)
        return fmt(v) if v is not None else "—"
    _drows = [[cc, _cv("dotnet", cc, "qps"), _cv("go", cc, "qps"), _cv("dotnet", cc, "p50"), _cv("go", cc, "p50"),
               _cv("dotnet", cc, "p95"), _cv("go", cc, "p95"), _cv("dotnet", cc, "ruPerSec"), _cv("go", cc, "ruPerSec")] for cc in concs]
    parts.append(table(["conc", ".NET qps", "Go qps", ".NET p50", "Go p50", ".NET p95", "Go p95", ".NET RU/s", "Go RU/s"], _drows, right_from=1))


# ---- Section 8: Go — full HPK in WHERE vs explicit PartitionKey vs both ----
parts.append('<h2 id="s6">6 · Go — full HPK: WHERE clause vs explicit partition key vs both</h2>')
parts.append(f'<p class="note">For the complete 3-level key (year=<code>{esc(Y)}</code>, month=<code>{esc(M)}</code>, '
             f'day=<code>{esc(D)}</code>) the Go SDK can express the same lookup three ways. This isolates whether it '
             'matters in Go — routing, RU, and latency — when the key is <b>complete</b> (the only case where Go can attach an explicit key).</p>')


def _gm(variant, field, form="select"):
    m = mi.get(("go", "3-hpk", variant, form))
    return m.get(field) if m and not m.get("error") else None


def _gc(variant, cc, field, form="select"):
    r = next((c for c in cells if c["sdk"] == "go" and c["depth"] == "3-hpk" and c["variant"] == variant and c["form"] == form and c["concurrency"] == cc), None)
    return r.get(field) if r else None


_expl = {"where": f'empty PK + WHERE c.year="{Y}" AND c.month="{M}" AND c.day="{D}"',
         "pk": f'NewPartitionKeyString("{Y}").AppendString("{M}").AppendString("{D}"), no WHERE',
         "where+pk": "full PartitionKey + full WHERE"}
_vrows = []
for _v in ["where", "pk", "where+pk"]:
    _pc = _gm(_v, "partitionsContacted")
    _vrows.append([_v, _expl[_v],
                   (f"{_pc}(gw)" if _pc is not None else "—"),
                   fmt(_gm(_v, "requestCharge") or 0), fmt(_gm(_v, "requestCharge", "count") or 0),
                   fmt(_gm(_v, "retrievedDocumentCount") or 0),
                   fmt(_gc(_v, 1, "p50") or 0), fmt(_gc(_v, _maxc, "p50") or 0)])
parts.append(table(["variant", "how it's written in Go", "parts", "RU (SELECT*)", "RU (COUNT)", "docs",
                    "p50 @c1 (ms)", f"p50 @c{_maxc} (ms)"], _vrows, right_from=2, wrap=True))

_VAR2 = {"where": "#512BD4", "pk": "#d64545", "where+pk": "#2e8b57"}
_saved = dict(SDK_COLOR)
SDK_COLOR.update(_VAR2)
_vlat = {_v: [_gc(_v, cc, "p50") for cc in concs] for _v in ["where", "pk", "where+pk"]}
parts.append(line_chart("Go 3-HPK — p50 latency vs concurrency, by how the key is expressed", concs, _vlat, "concurrency", "ms"))
SDK_COLOR.clear()
SDK_COLOR.update(_saved)

# data-driven finding
_ruw = _gm("where", "requestCharge") or 0
_rup = _gm("pk", "requestCharge") or 0
_ruwp = _gm("where+pk", "requestCharge") or 0
_verdict = ("all three route to the same single partition and cost essentially the same RU" if abs(_ruw - _rup) < max(_ruw, 1) * 0.15
            else "the three differ in RU — see the table")
parts.append(f'<p class="note"><b>Finding:</b> with a complete key, {_verdict} '
             f'(WHERE-only {_ruw:,.1f} RU · explicit-PK {_rup:,.1f} RU · both {_ruwp:,.1f} RU). '
             'Specifying the key <em>both</em> ways tends to add a small constant overhead with no routing benefit. So for a '
             '<b>full</b> key the explicit partition key and the WHERE clause are interchangeable in Go — the difference that '
             'matters is only for <em>partial</em> prefixes, which Go can express solely through the WHERE clause.</p>')

# ---- Appendix (detailed number dumps) then References ----
parts.extend(appendix)
parts.append('<h2 id="refs">References</h2>')
REFS = [
    ("r1", "Hierarchical partition keys — Azure Cosmos DB", "https://learn.microsoft.com/en-us/azure/cosmos-db/hierarchical-partition-keys"),
    ("r2", "Hierarchical partition keys — FAQ", "https://learn.microsoft.com/en-us/azure/cosmos-db/hierarchical-partition-keys-faq"),
    ("r3", "SDK connectivity modes — Direct vs Gateway (Direct only on .NET & Java)", "https://learn.microsoft.com/en-us/azure/cosmos-db/nosql/sdk-connection-modes"),
    ("r4", "Query performance tips for SDKs — query-plan cache, Optimistic Direct Execution, cross-partition parallelism", "https://learn.microsoft.com/en-us/azure/cosmos-db/nosql/performance-tips-query-sdk"),
    ("r5", "SQL query metrics — retrieved/output document count & size, time breakdown", "https://learn.microsoft.com/en-us/azure/cosmos-db/nosql/query-metrics"),
    ("r6", "Querying with the REST API — queries that cannot be served by the gateway", "https://learn.microsoft.com/rest/api/cosmos-db/querying-cosmosdb-resources-using-the-rest-api#queries-that-cannot-be-served-by-gateway"),
    ("r7", "azcosmos — Go SDK reference", "https://pkg.go.dev/github.com/Azure/azure-sdk-for-go/sdk/data/azcosmos"),
    ("r8", "PartitionKeyBuilder vs WHERE for partial HPK — azure-cosmos-dotnet-v3 issue #5404", "https://github.com/Azure/azure-cosmos-dotnet-v3/issues/5404"),
    ("r9", "Request Units in Azure Cosmos DB", "https://learn.microsoft.com/en-us/azure/cosmos-db/request-units"),
    ("r10", "Autoscale provisioned throughput", "https://learn.microsoft.com/en-us/azure/cosmos-db/provision-throughput-autoscale"),
    ("r11", "Partitioning and horizontal scaling", "https://learn.microsoft.com/en-us/azure/cosmos-db/partitioning-overview"),
    ("r12", "Scaling multi-tenant Go apps — choosing a partitioning approach (DevBlogs)", "https://devblogs.microsoft.com/cosmosdb/scaling-multi-tenant-go-applications-choosing-the-right-database-partitioning-approach/"),
    ("r13", "Go SDK source — azure-sdk-for-go / sdk/data/azcosmos", "https://github.com/Azure/azure-sdk-for-go/tree/main/sdk/data/azcosmos"),
    ("r14", ".NET SDK source — azure-cosmos-dotnet-v3", "https://github.com/Azure/azure-cosmos-dotnet-v3/tree/master/Microsoft.Azure.Cosmos/src"),
]
parts.append('<ol class="refs">' + "".join(
    f'<li id="{a}"><a href="{u}">{esc(t)}</a><div class="u">{esc(u)}</div></li>' for a, t, u in REFS) + '</ol>')

CSS = """
:root{--bg:#ffffff;--fg:#182030;--muted:#5b6678;--card:#f4f6fb;--card2:#eef1f8;--border:#e2e6f0;--accent:#0078d4;--err:#c62f43}
@media(prefers-color-scheme:dark){:root{--bg:#0f1420;--fg:#e6ecf5;--muted:#93a0b4;--card:#171e2b;--card2:#1e2736;--border:#293445;--accent:#4aa3e8;--err:#ff6b7d}}
:root[data-theme=dark]{--bg:#0f1420;--fg:#e6ecf5;--muted:#93a0b4;--card:#171e2b;--card2:#1e2736;--border:#293445;--accent:#4aa3e8;--err:#ff6b7d}
:root[data-theme=light]{--bg:#ffffff;--fg:#182030;--muted:#5b6678;--card:#f4f6fb;--card2:#eef1f8;--border:#e2e6f0;--accent:#0078d4;--err:#c62f43}
*{box-sizing:border-box}
body{margin:0 auto;max-width:1060px;padding:32px 24px 64px;background:var(--bg);color:var(--fg);font:15px/1.55 ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
h1{font-size:29px;line-height:1.15;letter-spacing:-.02em;margin:0 0 6px;text-wrap:balance;font-weight:700}
h2{font-size:18px;letter-spacing:-.01em;margin:40px 0 12px;padding-bottom:7px;border-bottom:1px solid var(--border);font-weight:650;text-wrap:balance}
.sub{color:var(--muted);margin:0 0 6px;max-width:72ch}
.meta{color:var(--muted);margin:0 0 10px;font-size:12.5px;font-variant-numeric:tabular-nums}.meta a{font-weight:600}
.abstract{background:var(--card);border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:8px;padding:14px 18px;margin:16px 0;font-size:14px;line-height:1.62;max-width:82ch}
.abstract b{font-weight:700}
.toc{margin:18px 0 6px}.toc .toctitle{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:700;margin-bottom:7px}
.toc ul{list-style:none;padding:0;margin:0;columns:2;column-gap:30px}@media(max-width:640px){.toc ul{columns:1}}
.toc li{margin:4px 0;font-size:13px;break-inside:avoid}.note{color:var(--muted);font-size:13px;margin:9px 0 0;max-width:76ch}p{max-width:76ch}
code{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:12px;background:var(--card2);border:1px solid var(--border);border-radius:4px;padding:1px 5px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}sup a{font-weight:600;font-size:.7em}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:10px;margin:18px 0 4px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:13px 15px}
.card .big{font-size:22px;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.12}
.card .cl{font-size:12.5px;font-weight:600;margin-top:4px}.card .cs{font-size:11px;color:var(--muted);margin-top:3px;line-height:1.35}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:820px){.grid2{grid-template-columns:1fr}.cards{grid-template-columns:1fr 1fr}}
.chart{width:100%;height:auto;background:var(--card);border:1px solid var(--border);border-radius:12px;margin-top:8px}
.ct{fill:var(--fg);font-size:13px;font-weight:600}.ax{fill:var(--muted);font-size:10px}.bl{fill:var(--fg);font-size:9px;font-variant-numeric:tabular-nums}.lg{fill:var(--fg);font-size:11px}
.grid{stroke:var(--border);stroke-width:1}
.tw{overflow-x:auto;margin-top:10px;border:1px solid var(--border);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{padding:6px 11px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}
thead th{color:var(--muted);font-weight:600;background:var(--card);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
td.r,th.r{text-align:right;font-variant-numeric:tabular-nums}
tbody tr:last-child td{border-bottom:none}tbody tr:hover{background:var(--card)}
.tw.wrap table{min-width:620px}.tw.wrap td{white-space:normal;vertical-align:top;line-height:1.4}.tw.wrap td:first-child{font-weight:600;color:var(--fg)}
h3.h3{font-size:14px;margin:24px 0 6px;font-weight:650;letter-spacing:-.01em}
pre.code{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 14px;overflow-x:auto;margin:6px 0}
pre.code code{background:none;border:none;padding:0;font-size:11.5px;line-height:1.5;white-space:pre;display:block}
.cl2{font-size:12px;font-weight:600;color:var(--muted);margin:6px 0 2px;text-transform:uppercase;letter-spacing:.03em}
.qsql{margin:3px 0;font-size:12.5px;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.qsql .ql{color:var(--muted);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;min-width:30px}
.qsql code{white-space:normal;word-break:break-word}
.qnote{margin:5px 0;font-size:12px;color:var(--err);font-weight:600}
.quote{margin:10px 0;padding:10px 16px;border-left:3px solid var(--accent);background:var(--card);border-radius:6px;font-size:13.5px;line-height:1.55;font-style:italic;max-width:82ch}
.quote cite{display:block;margin-top:7px;font-style:normal;font-size:12px;color:var(--muted)}
.err{color:var(--err);font-size:11px;font-weight:600}
.ev{margin:10px 0;padding-left:18px}.ev li{margin:4px 0}
.refs{font-size:13px;padding-left:20px}.refs li{margin:8px 0}.refs .u{color:var(--muted);font-size:11px;word-break:break-all;font-family:ui-monospace,monospace}
"""

htmlout = "<title>Cosmos DB HPK — Go vs .NET deep analysis</title>\n" + f"<style>{CSS}</style>\n" + "\n".join(parts)
OUT.write_text(htmlout)
print(f"Wrote {OUT} ({len(htmlout):,} bytes)")
