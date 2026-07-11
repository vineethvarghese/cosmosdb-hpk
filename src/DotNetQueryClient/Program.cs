using System.Collections.Concurrent;
using System.Diagnostics;
using System.Globalization;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Azure.Cosmos;
using Newtonsoft.Json.Linq;

// ---------------------------------------------------------------------------
// .NET HPK experiment client.
//   Matrix: depth {3,2,1,0 HPK} x variant {where, pk, where+pk} x form {select, count}
//   Per config: a RICH probe at concurrency 1 (full diagnostics: RU, bytes, retrieved/
//   output docs, index/load/runtime times, partitions contacted) + a CONCURRENCY SWEEP
//   1..N (deadline-boxed cells -> throughput, p50/p95/p99, RU/s).
//   Direct connection mode so per-partition store addresses appear in diagnostics.
// ---------------------------------------------------------------------------
LoadDotEnv();
string endpoint = Env("COSMOS_ENDPOINT", "");
string key = Env("COSMOS_KEY", "");
string databaseId = Env("COSMOS_DATABASE", "ordersdb");
string containerId = Env("COSMOS_CONTAINER", "orders");
int maxConc = int.Parse(Env("SWEEP_MAX_CONCURRENCY", "6"));
double cellSeconds = double.Parse(Env("CELL_SECONDS", "12"), CultureInfo.InvariantCulture);

if (string.IsNullOrWhiteSpace(endpoint) || string.IsNullOrWhiteSpace(key))
{
    Console.Error.WriteLine("ERROR: COSMOS_ENDPOINT / COSMOS_KEY not set.");
    return 1;
}

var manifestPath = ResolveRepoPath("config/seed-manifest.json");
using var manifest = JsonDocument.Parse(File.ReadAllText(manifestPath));
var qcfg = manifest.RootElement.GetProperty("query");
string year = qcfg.GetProperty("year").GetString()!;
string month = qcfg.GetProperty("month").GetProperty("month").GetString()!;
string day = qcfg.GetProperty("day").GetProperty("day").GetString()!;
Console.WriteLine($"Targets: year={year} month={month} day={day}  maxConc={maxConc} cell={cellSeconds}s");

var outDir = ResolveRepoPath("diagnostics/dotnet");
Directory.CreateDirectory(outDir);

using var client = new CosmosClient(endpoint, key, new CosmosClientOptions
{
    ApplicationName = "cosmos-hpk-dotnet",
    MaxRetryAttemptsOnRateLimitedRequests = 9,
    MaxRetryWaitTimeOnRateLimitedRequests = TimeSpan.FromSeconds(30),
});
var container = client.GetContainer(databaseId, containerId);
try
{
    var ranges = await container.GetFeedRangesAsync();
    Console.WriteLine($"Physical partitions (feed ranges): {ranges.Count}");
}
catch (Exception ex) { Console.Error.WriteLine($"(feed ranges unavailable: {ex.Message})"); }

var cells = new List<CellRow>();
var richRows = new List<RichRow>();

foreach (var cfg in BuildMatrix())
{
    // ---- RICH probe (concurrency 1, full diagnostics, incremental per page) ----
    try { await ExecuteLean(cfg); } catch { } // warmup
    try
    {
        var rich = await ExecuteRich(cfg);
        richRows.Add(rich);
        Console.WriteLine($"  [rich] {cfg.Depth,-6} {cfg.Variant,-9} {cfg.Form,-6} RU={rich.requestCharge,9:N1} parts={rich.partitionsContacted} retrievedDocs={rich.retrievedDocumentCount} bytes={rich.retrievedDocumentSize}");
    }
    catch (Exception ex)
    {
        richRows.Add(RichRow.Error(cfg, (ex as CosmosException)?.StatusCode.ToString() ?? ex.Message.Split('\n')[0]));
        Console.Error.WriteLine($"  [rich] {cfg.Depth}/{cfg.Variant}/{cfg.Form} ERROR: {ex.Message.Split('\n')[0]}");
    }

    // ---- CONCURRENCY sweep ----
    for (int c = 1; c <= maxConc; c++)
    {
        var cell = await RunCell(cfg, c, TimeSpan.FromSeconds(cellSeconds));
        cells.Add(cell);
        Console.WriteLine($"  [sweep] {cfg.Depth,-6} {cfg.Variant,-9} {cfg.Form,-6} c={c} qps={cell.qps,7:N2} p50={cell.p50,7:N1} p95={cell.p95,7:N1} ruPerSec={cell.ruPerSec,9:N0} n={cell.completions} err={cell.errors}");
    }
}

await File.WriteAllTextAsync(Path.Combine(outDir, "cells.json"), JsonSerializer.Serialize(cells, Json));
await File.WriteAllTextAsync(Path.Combine(outDir, "metrics.json"), JsonSerializer.Serialize(richRows, Json));
Console.WriteLine($"\nWrote {cells.Count} cells, {richRows.Count} rich rows -> {outDir}");
return 0;

// ---------------------------------------------------------------------------
QueryRequestOptions Opts(QueryConfig cfg)
{
    var o = new QueryRequestOptions();
    if (cfg.PkLevels > 0)
    {
        var b = new PartitionKeyBuilder();
        if (cfg.PkLevels >= 1) b.Add(year);
        if (cfg.PkLevels >= 2) b.Add(month);
        if (cfg.PkLevels >= 3) b.Add(day);
        o.PartitionKey = b.Build();
    }
    return o;
}
QueryDefinition Def(QueryConfig cfg)
{
    var qd = new QueryDefinition(cfg.Sql);
    if (cfg.Sql.Contains("@y")) qd = qd.WithParameter("@y", year);
    if (cfg.Sql.Contains("@m")) qd = qd.WithParameter("@m", month);
    if (cfg.Sql.Contains("@d")) qd = qd.WithParameter("@d", day);
    return qd;
}

// Lean execution: drain, return (latencyMs, ru). Used by the sweep.
async Task<(double lat, double ru)> ExecuteLean(QueryConfig cfg)
{
    var sw = Stopwatch.StartNew();
    using var it = container.GetItemQueryIterator<JToken>(Def(cfg), requestOptions: Opts(cfg));
    double ru = 0;
    while (it.HasMoreResults) { var p = await it.ReadNextAsync(); ru += p.RequestCharge; }
    sw.Stop();
    return (sw.Elapsed.TotalMilliseconds, ru);
}

// Rich execution: drain, accumulating query metrics per page (bounded memory), dump 1st page diag.
async Task<RichRow> ExecuteRich(QueryConfig cfg)
{
    var parts = new HashSet<string>();
    long rDocs = 0, rSize = 0, oDocs = 0, oSize = 0;
    double idx = 0, load = 0, rt = 0, tot = 0, ru = 0;
    int pages = 0; string? firstDiag = null;
    using var it = container.GetItemQueryIterator<JToken>(Def(cfg), requestOptions: Opts(cfg));
    while (it.HasMoreResults)
    {
        var page = await it.ReadNextAsync();
        pages++; ru += page.RequestCharge;
        var d = page.Diagnostics.ToString();
        firstDiag ??= d;
        foreach (Match m in Regex.Matches(d, @"partitions/([0-9a-fA-F-]{8,})")) parts.Add(m.Groups[1].Value);
        rDocs += SumCount(d, "Retrieved Document Count"); rSize += SumCount(d, "Retrieved Document Size");
        oDocs += SumCount(d, "Output Document Count"); oSize += SumCount(d, "Output Document Size");
        idx += SumTime(d, "Index Lookup Time"); load += SumTime(d, "Document Load Time");
        rt += SumTime(d, "Runtime Execution Times"); tot += SumTime(d, "Total Query Execution Time");
    }
    if (firstDiag != null)
        await File.WriteAllTextAsync(Path.Combine(outDir, $"{cfg.Depth}-{cfg.Variant}-{cfg.Form}.diag.json"), firstDiag);
    return new RichRow("dotnet", cfg.Depth, cfg.Variant, cfg.Form, "",
        Math.Round(ru, 3), parts.Count, rDocs, rSize, oDocs, oSize,
        Math.Round(idx, 3), Math.Round(load, 3), Math.Round(rt, 3), Math.Round(tot, 3),
        pages, string.Join(",", parts));
}

async Task<CellRow> RunCell(QueryConfig cfg, int concurrency, TimeSpan deadline)
{
    var lats = new ConcurrentBag<double>();
    double totalRu = 0; int completions = 0, errors = 0; object ruLock = new();
    var deadlineAt = DateTime.UtcNow + deadline;
    var sw = Stopwatch.StartNew();

    async Task Worker()
    {
        while (DateTime.UtcNow < deadlineAt)
        {
            try
            {
                var (ms, ru) = await ExecuteLean(cfg);
                lats.Add(ms);
                Interlocked.Increment(ref completions);
                lock (ruLock) totalRu += ru;
            }
            catch { Interlocked.Increment(ref errors); }
        }
    }
    await Task.WhenAll(Enumerable.Range(0, concurrency).Select(_ => Worker()));
    sw.Stop();

    var sorted = lats.OrderBy(x => x).ToArray();
    double elapsed = sw.Elapsed.TotalSeconds;
    return new CellRow("dotnet", cfg.Depth, cfg.Variant, cfg.Form, concurrency,
        completions, errors, Math.Round(elapsed, 3),
        Math.Round(completions / Math.Max(elapsed, 1e-6), 3),
        Math.Round(totalRu / Math.Max(elapsed, 1e-6), 1),
        Pct(sorted, 50), Pct(sorted, 95), Pct(sorted, 99),
        sorted.Length > 0 ? Math.Round(sorted[^1], 2) : 0,
        sorted.Length > 0 ? Math.Round(sorted.Average(), 2) : 0);
}

static long SumCount(string s, string label)
{
    long sum = 0;
    foreach (Match m in Regex.Matches(s, Regex.Escape(label) + @"\s*:\s*([\d,]+)"))
        sum += long.Parse(m.Groups[1].Value.Replace(",", ""));
    return sum;
}
static double SumTime(string s, string label)
{
    double sum = 0;
    foreach (Match m in Regex.Matches(s, Regex.Escape(label) + @"\s*:\s*([\d.]+)\s*milliseconds"))
        sum += double.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture);
    return sum;
}
static double Pct(double[] sorted, int p)
{
    if (sorted.Length == 0) return 0;
    int idx = (int)Math.Ceiling(p / 100.0 * sorted.Length) - 1;
    return Math.Round(sorted[Math.Clamp(idx, 0, sorted.Length - 1)], 2);
}

List<QueryConfig> BuildMatrix()
{
    var list = new List<QueryConfig>();
    var depths = new (string label, string preds, int pk)[]
    {
        ("3-hpk", "c.year=@y AND c.month=@m AND c.day=@d", 3),
        ("2-hpk", "c.year=@y AND c.month=@m", 2),
        ("1-hpk", "c.year=@y", 1),
    };
    foreach (var (label, preds, pk) in depths)
        foreach (var form in new[] { "select", "count" })
        {
            string sel = form == "count" ? "SELECT VALUE COUNT(1)" : "SELECT *";
            list.Add(new QueryConfig(label, "where", form, $"{sel} FROM c WHERE {preds}", 0));
            list.Add(new QueryConfig(label, "pk", form, $"{sel} FROM c", pk));
            list.Add(new QueryConfig(label, "where+pk", form, $"{sel} FROM c WHERE {preds}", pk));
        }
    foreach (var form in new[] { "select", "count" })
    {
        string sel = form == "count" ? "SELECT VALUE COUNT(1)" : "SELECT *";
        list.Add(new QueryConfig("0-hpk", "none", form, $"{sel} FROM c", 0));
    }
    return list;
}

static void LoadDotEnv()
{
    var dir = new DirectoryInfo(Directory.GetCurrentDirectory());
    for (int i = 0; dir != null && i < 6; i++, dir = dir.Parent)
    {
        var p = Path.Combine(dir.FullName, ".env");
        if (!File.Exists(p)) continue;
        foreach (var raw in File.ReadAllLines(p))
        {
            var line = raw.Trim();
            if (line.Length == 0 || line.StartsWith('#')) continue;
            int eq = line.IndexOf('=');
            if (eq <= 0) continue;
            if (Environment.GetEnvironmentVariable(line[..eq].Trim()) == null)
                Environment.SetEnvironmentVariable(line[..eq].Trim(), line[(eq + 1)..].Trim());
        }
        return;
    }
}
static string Env(string k, string def) => Environment.GetEnvironmentVariable(k) ?? def;
static string ResolveRepoPath(string rel)
{
    var dir = new DirectoryInfo(Directory.GetCurrentDirectory());
    for (int i = 0; dir != null && i < 6; i++, dir = dir.Parent)
        if (Directory.Exists(Path.Combine(dir.FullName, "infra"))) return Path.Combine(dir.FullName, rel);
    return Path.Combine(Directory.GetCurrentDirectory(), rel);
}

partial class Program
{
    static readonly JsonSerializerOptions Json = new() { WriteIndented = true };
}

record QueryConfig(string Depth, string Variant, string Form, string Sql, int PkLevels);

record CellRow(string sdk, string depth, string variant, string form, int concurrency,
    int completions, int errors, double elapsedSec, double qps, double ruPerSec,
    double p50, double p95, double p99, double max, double avg);

record RichRow(string sdk, string depth, string variant, string form, string error,
    double requestCharge, int partitionsContacted,
    long retrievedDocumentCount, long retrievedDocumentSize,
    long outputDocumentCount, long outputDocumentSize,
    double indexLookupMs, double documentLoadMs, double runtimeExecMs, double totalExecMs,
    int pages, string pkRangeIds)
{
    public static RichRow Error(QueryConfig c, string err) =>
        new("dotnet", c.Depth, c.Variant, c.Form, err, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "");
}
