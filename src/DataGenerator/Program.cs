using System.Diagnostics;
using System.Globalization;
using System.Text.Json;
using DataGenerator.Models;
using Microsoft.Azure.Cosmos;

// ---------------------------------------------------------------------------
// Config resolution: real env var > .env file > appsettings.json > default
// ---------------------------------------------------------------------------
var appSettings = LoadAppSettings();
LoadDotEnv(); // populates env vars (without overwriting real ones)

string Cfg(string key, string def) =>
    Environment.GetEnvironmentVariable(key)
    ?? (appSettings.TryGetValue(key, out var v) ? v : null)
    ?? def;

string endpoint = Cfg("COSMOS_ENDPOINT", "");
string key = Cfg("COSMOS_KEY", "");
string databaseId = Cfg("COSMOS_DATABASE", "ordersdb");
string containerId = Cfg("COSMOS_CONTAINER", "orders");

if (string.IsNullOrWhiteSpace(endpoint) || string.IsNullOrWhiteSpace(key))
{
    Console.Error.WriteLine("ERROR: COSMOS_ENDPOINT / COSMOS_KEY not set. Run infra/deploy.sh (writes ../../.env) or export them.");
    return 1;
}

var startDate = DateOnly.ParseExact(Cfg("GEN_START_DATE", "2024-01-01"), "yyyy-MM-dd", CultureInfo.InvariantCulture);
int numDays = int.Parse(Cfg("GEN_NUM_DAYS", "1000"));
int ordersPerDay = int.Parse(Cfg("GEN_ORDERS_PER_DAY", "500"));
int batchSize = int.Parse(Cfg("GEN_BATCH_SIZE", "1000"));
bool busyMonthEnabled = Cfg("GEN_BUSY_MONTH", "true").Equals("true", StringComparison.OrdinalIgnoreCase);
int busyMonthMult = int.Parse(Cfg("GEN_BUSY_MONTH_MULT", "10"));
int autoscaleMax = int.Parse(Cfg("GEN_AUTOSCALE_MAX", "30000"));

// The "busy" month gets ~busyMonthMult x orders so its logical prefix straddles a
// physical-partition split (makes month-prefix routing visible in the diagnostics).
var busyYear = startDate.Year.ToString("D4");
var busyMonth = "06";

string[] statuses = { "Placed", "Paid", "Shipped", "Delivered", "Cancelled", "Returned" };
string[] currencies = { "USD", "EUR", "GBP", "INR", "AUD" };
const string baselineStatus = "Shipped";

Console.WriteLine($"Generating ~{(long)numDays * ordersPerDay:N0} base orders over {numDays} days from {startDate:yyyy-MM-dd}");
Console.WriteLine($"Busy month: {(busyMonthEnabled ? $"{busyYear}-{busyMonth} x{busyMonthMult}" : "disabled")}");

// ---------------------------------------------------------------------------
// Cosmos client (bulk execution) + ensure HPK container exists
// ---------------------------------------------------------------------------
using var client = new CosmosClient(endpoint, key, new CosmosClientOptions
{
    AllowBulkExecution = true,
    ApplicationName = "cosmos-hpk-datagen",
});

var db = await client.CreateDatabaseIfNotExistsAsync(databaseId);
var containerProps = new ContainerProperties(containerId, new List<string> { "/year", "/month", "/day" });
var containerResp = await db.Database.CreateContainerIfNotExistsAsync(
    containerProps, ThroughputProperties.CreateAutoscaleThroughput(autoscaleMax));
var container = containerResp.Container;
Console.WriteLine($"Container ready: {databaseId}/{containerId} (HPK /year/month/day)");

// ---------------------------------------------------------------------------
// Generate + bulk upsert
// ---------------------------------------------------------------------------
var faker = new Bogus.Faker();
var sw = Stopwatch.StartNew();
long totalDocs = 0, failures = 0;
double totalRu = 0;

// Precise counts for the sample query targets (written into seed-manifest.json).
// Query target month is a NORMAL month (March) so the deep-prefix query stays cheap;
// the busy month (June) is recorded separately for optional heavy fan-out testing.
string qYear = startDate.Year.ToString("D4");
string qMonth = "03";
string qDay = "15";
long cntYear = 0, cntMonth = 0, cntDay = 0;

var pending = new List<Task>(batchSize);

async Task FlushAsync()
{
    if (pending.Count == 0) return;
    await Task.WhenAll(pending);
    pending.Clear();
}

for (int d = 0; d < numDays; d++)
{
    var date = startDate.AddDays(d);
    string y = date.Year.ToString("D4");
    string m = date.Month.ToString("D2");
    string day = date.Day.ToString("D2");

    int count = ordersPerDay;
    if (busyMonthEnabled && y == busyYear && m == busyMonth)
        count *= busyMonthMult;

    var pk = new PartitionKeyBuilder().Add(y).Add(m).Add(day).Build();

    for (int i = 0; i < count; i++)
    {
        var order = new Order
        {
            Id = $"ord-{y}{m}{day}-{i:D6}",
            Year = y,
            Month = m,
            Day = day,
            OrderId = $"ord-{y}{m}{day}-{i:D6}",
            Timestamp = date.ToDateTime(new TimeOnly(faker.Random.Int(0, 23), faker.Random.Int(0, 59), faker.Random.Int(0, 59)))
                            .ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture),
            Status = faker.PickRandom(statuses),
            TotalAmount = Math.Round(faker.Random.Double(5, 2000), 2),
            ItemCount = faker.Random.Int(1, 12),
            Currency = faker.PickRandom(currencies),
        };

        if (y == qYear) cntYear++;
        if (y == qYear && m == qMonth) cntMonth++;
        if (y == qYear && m == qMonth && day == qDay) cntDay++;

        pending.Add(UpsertOne(container, order, pk));

        if (pending.Count >= batchSize)
            await FlushAsync();
    }

    if (d % 30 == 0)
    {
        double rate = totalDocs / Math.Max(sw.Elapsed.TotalSeconds, 0.001);
        Console.WriteLine($"  {date:yyyy-MM-dd}: {totalDocs:N0} docs, {rate:N0}/s, {totalRu:N0} RU, {failures} fail");
    }
}
await FlushAsync();

sw.Stop();
Console.WriteLine($"\nDone: {totalDocs:N0} docs in {sw.Elapsed.TotalSeconds:N1}s ({totalRu:N0} RU, {failures} failures)");

// Footgun guard: the query targets (qMonth/qDay) are fixed constants; if GEN_START_DATE /
// GEN_NUM_DAYS put them outside the generated range, the sample day/month is empty and the
// shallow-prefix/baseline queries silently return 0 docs.
if (cntDay == 0 || cntMonth == 0)
    Console.Error.WriteLine($"WARNING: sample target {qYear}-{qMonth}-{qDay} has little/no data (day={cntDay}, month={cntMonth}). "
        + "It may be outside the generated date range — Q2/Q3 queries would return 0 docs. Adjust GEN_START_DATE/GEN_NUM_DAYS.");

// Local function capturing the counters (bulk upsert with RU/failure accounting).
async Task UpsertOne(Container c, Order o, PartitionKey pk)
{
    try
    {
        var resp = await c.UpsertItemAsync(o, pk);
        Interlocked.Increment(ref totalDocs);
        AddRu(resp.RequestCharge);
    }
    catch (CosmosException)
    {
        Interlocked.Increment(ref failures);
    }
}

void AddRu(double ru)
{
    // double isn't atomic; good enough for a coarse progress figure.
    totalRu += ru;
}

// ---------------------------------------------------------------------------
// Write seed-manifest.json for the query clients
// ---------------------------------------------------------------------------
var manifest = new
{
    database = databaseId,
    container = containerId,
    startDate = startDate.ToString("yyyy-MM-dd"),
    numDays,
    ordersPerDay,
    busyMonthEnabled,
    busyMonth = new { year = busyYear, month = busyMonth, multiplier = busyMonthMult },
    totalDocs,
    query = new
    {
        year = qYear,
        month = new { year = qYear, month = qMonth },
        day = new { year = qYear, month = qMonth, day = qDay },
        baselineStatus,
        // A concrete order that exists on the sample day. Used as a selective
        // (non-partition-key) predicate so the shallow-prefix and baseline queries
        // stay cheap AND are simple-filter queries the Go gateway can execute.
        sampleOrderId = $"ord-{qYear}{qMonth}{qDay}-000000",
    },
    expectedCounts = new { day = cntDay, month = cntMonth, year = cntYear },
    generatedFields = new[] { "id", "year", "month", "day", "orderId", "timestamp", "status", "totalAmount", "itemCount", "currency" },
};

var manifestPath = ResolveRepoPath("config/seed-manifest.json");
Directory.CreateDirectory(Path.GetDirectoryName(manifestPath)!);
await File.WriteAllTextAsync(manifestPath,
    JsonSerializer.Serialize(manifest, new JsonSerializerOptions { WriteIndented = true }));
Console.WriteLine($"Wrote manifest: {manifestPath}");
Console.WriteLine($"  sample day {qYear}-{qMonth}-{qDay}={cntDay:N0}, month {qYear}-{qMonth}={cntMonth:N0}, year {qYear}={cntYear:N0}");
return 0;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
static Dictionary<string, string> LoadAppSettings()
{
    var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
    var path = Path.Combine(AppContext.BaseDirectory, "appsettings.json");
    if (!File.Exists(path)) return result;
    using var doc = JsonDocument.Parse(File.ReadAllText(path));
    foreach (var prop in doc.RootElement.EnumerateObject())
        result[prop.Name] = prop.Value.ValueKind == JsonValueKind.String
            ? prop.Value.GetString() ?? ""
            : prop.Value.GetRawText();
    return result;
}

static void LoadDotEnv()
{
    var dir = new DirectoryInfo(Directory.GetCurrentDirectory());
    for (int i = 0; dir != null && i < 6; i++, dir = dir.Parent)
    {
        var path = Path.Combine(dir.FullName, ".env");
        if (!File.Exists(path)) continue;
        foreach (var raw in File.ReadAllLines(path))
        {
            var line = raw.Trim();
            if (line.Length == 0 || line.StartsWith('#')) continue;
            int eq = line.IndexOf('=');
            if (eq <= 0) continue;
            var k = line[..eq].Trim();
            var val = line[(eq + 1)..].Trim();
            if (Environment.GetEnvironmentVariable(k) == null)
                Environment.SetEnvironmentVariable(k, val);
        }
        return;
    }
}

static string ResolveRepoPath(string relative)
{
    var dir = new DirectoryInfo(Directory.GetCurrentDirectory());
    for (int i = 0; dir != null && i < 6; i++, dir = dir.Parent)
    {
        // Repo root is the dir that contains the "config" and "infra" folders.
        if (Directory.Exists(Path.Combine(dir.FullName, "infra")))
            return Path.Combine(dir.FullName, relative);
    }
    return Path.Combine(Directory.GetCurrentDirectory(), relative);
}
