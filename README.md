# Cosmos DB Hierarchical Partition Keys (HPK) — Go vs .NET SDK benchmark

A reproducible benchmark of Azure Cosmos DB **hierarchical partition keys** (HPK,
`/year/month/day`) that measures how partition-key **prefix depth** affects routing and
request cost, and how the **Go** (`azcosmos`) and **.NET** (`Microsoft.Azure.Cosmos`) SDKs
differ when running the same queries.

### 📊 [Read the full report online →](https://vineethvarghese.github.io/cosmosdb-hpk/)

Hosted on GitHub Pages — charts, per-query tables, and an SDK source-code walkthrough, no setup
required.

> **Disclaimer:** this is a benchmark/experiment, not production code. It provisions real,
> billable Azure resources. Read the **Cost & teardown** section before running it.

## Headline finding

On a **~1.27M-document** orders container spread across **10 physical partitions**:

- **Prefix depth prunes fan-out.** A full 3-level-key query (`year+month+day`) is served from a
  **single** partition; an unfiltered scan fans out to **all 10**. Request charge (RU) and bytes
  read rise with the fan-out.
- **.NET vs Go execution differs.** .NET (Direct/TCP mode) resolves partitions client-side and
  reads them **in parallel**; Go routes and serves everything through the Cosmos **gateway**
  (sequential paging), so it is markedly slower on large cross-partition scans and runs
  **~10–19% higher RU** for the same query.
- **Go cannot run cross-partition aggregates.** Any cross-partition `COUNT`/`DISTINCT`/`GROUP BY`
  fails with **BadRequest** from the gateway (in this matrix, the cross-partition `COUNT` configs).
  .NET runs them via its client-side pipeline. This capability gap is itself a result.
- **The `#5404` partial-`PartitionKey` over-read is reproduced** on .NET: attaching a *partial*
  `PartitionKeyBuilder` **without** a WHERE clause over-reads the whole scoped partition.

> **Absolute latency and throughput numbers will vary** with region, hardware, and time of day.
> The **RU costs** and the **partition-routing behaviour** (which query touches how many
> partitions) are the reproducible results — see the note at the end.

## Where the results live

- **[Live report (GitHub Pages)](https://vineethvarghese.github.io/cosmosdb-hpk/)** — the published
  report, viewable in any browser with no download.
- **`analysis/report.html`** — the same report as a self-contained local file (inline SVG charts,
  per-query tables, an SDK source-code walkthrough, and references). **Open it in a browser:**
  `open analysis/report.html` (macOS) / `xdg-open analysis/report.html` (Linux). No server or
  assets required.
- **`diagnostics/dotnet/` and `diagnostics/go/`** — the raw test results the report is built from:
  - `metrics.json` — per query-config "rich probe" (RU, partitions contacted, docs/bytes,
    index/load/exec time). One row per config.
  - `cells.json` — the concurrency sweep (qps, p50/p95/p99, RU/s per concurrency level).
  - `<depth>-<variant>-<form>.diag.json` — the first-page raw diagnostics for one config
    (.NET `CosmosDiagnostics`; Go = captured gateway response headers).
- **`config/seed-manifest.json`** — the generated dataset descriptor (doc counts, query targets)
  that both query clients read.

## What the experiment does

- **Model:** `orders` documents keyed by order date. HPK = `/year/month/day` (`MultiHash`).
- **Data:** ~1.27M docs over 1000 days from `2024-01-01`, with one **busy month** (`2024-06`) at
  10x volume so a logical prefix straddles a physical-partition split.
- **Query matrix** (run by both SDKs where each can express it):
  - **prefix depth** — 3 / 2 / 1 / 0 HPK levels constrained (`year+month+day` → `year` → none)
  - **variant** — `where` (WHERE clause only), `pk` (explicit `PartitionKey` only, no WHERE),
    `where+pk` (both)
  - **form** — `SELECT *` and `SELECT VALUE COUNT(1)`
  - each config gets a **rich probe** at concurrency 1 plus a **concurrency sweep** (1..N).

### Two SDK facts the matrix isolates

1. **.NET** can express a partial-prefix query three ways and they don't perform the same
   ([azure-cosmos-dotnet-v3#5404](https://github.com/Azure/azure-cosmos-dotnet-v3/issues/5404)):
   WHERE-only (`where`), WHERE + partial `PartitionKeyBuilder` (`where+pk`), or partial
   `PartitionKeyBuilder` with no WHERE (`pk`) — the last over-reads.
2. **Go** (`azcosmos`) has **no partial-prefix partition key**: you pass either an **empty**
   `azcosmos.NewPartitionKey()` (and let the WHERE clause route) or the **full** key. It is
   **gateway-only** — it can only do "simple projections and filtering" cross-partition, so
   cross-partition aggregates return **BadRequest**, and its `partitionsContacted` is
   gateway-reported (reliable only for single-partition / full-key queries). **The .NET client is
   the authority on physical-partition routing; RU is trustworthy for both.**

## Prerequisites

| Tool | Needed for | Notes |
|---|---|---|
| **Azure subscription** + `az login` | provisioning | `brew install azure-cli`, then `az login && az account set -s <SUBSCRIPTION>` |
| **.NET SDK 10** | data generator + .NET query client | both `.csproj` target `net10.0`; `brew install --cask dotnet-sdk` |
| **Go 1.26+** | Go query client | `go.mod` declares `go 1.26` |
| **python3** | HTML report generator | standard library only; preinstalled on macOS |

Auth is **account-key** (`COSMOS_KEY`), loaded from `.env`.

> If a script can't find `dotnet`, it's a PATH issue — the SDK installs to
> `/usr/local/share/dotnet`. A zsh `az` *alias* (e.g. a Docker wrapper) is **not** visible inside
> the bash scripts; install a native `az` or export `AZ=...` (see `infra/deploy.sh` header).

## Layout

```
infra/                     Bicep template + deploy / scale-down / teardown scripts
src/DataGenerator/         .NET 10 — Bogus + bulk upsert; writes config/seed-manifest.json
src/DotNetQueryClient/     .NET 10 — query matrix + rich CosmosDiagnostics + concurrency sweep
src/go-query-client/       Go — query matrix + gateway header capture
config/seed-manifest.json  dataset descriptor + query targets (generated)
diagnostics/{dotnet,go}/   metrics.json, cells.json, *.diag.json (generated test results)
analysis/report.py         builds analysis/report.html from metrics.json + cells.json
analysis/report.html       the published report (open in a browser)
run-all.sh                 orchestrates deploy -> generate -> scale-down -> query x2 -> report
```

### Building the report

`analysis/report.py` builds `analysis/report.html` from `diagnostics/*/metrics.json` and
`cells.json` (Python standard library only — no third-party dependencies).

## Reproduce the published results

**One command.** The defaults reproduce the published ~1.27M-doc / 10-partition run:

```bash
az login && az account set -s <SUBSCRIPTION>
./run-all.sh
```

`run-all.sh` runs the whole pipeline — **deploy → generate ~1.27M docs → scale down → run the
.NET and Go query clients → build `analysis/report.html`** — then reminds you to tear down.
`deploy.sh` writes a ready-to-use `.env` (endpoint, key, and every knob pre-filled with the
published values), so **there is nothing to edit by hand**. It prompts once to confirm the spend
(provisioning bills real money — see [Cost & teardown](#cost--teardown)); pass `CONFIRM=1` to skip
the prompt in automation.

Needs `az login` (above) plus the tools in [Prerequisites](#prerequisites): .NET 10, Go 1.26+, python3.

### Cheaper / smaller run

Override any knob in the environment first — no file edits:

```bash
# small, cheap smoke run: ~2 partitions, ~200 orders/day
COSMOS_MAX_THROUGHPUT=20000 GEN_ORDERS_PER_DAY=200 ./run-all.sh
```

`COSMOS_MAX_THROUGHPUT` (RU/s at load; ≈10,000 per physical partition) is the main cost/partition
lever. Other knobs: `GEN_ORDERS_PER_DAY`, `GEN_NUM_DAYS`, `GEN_BUSY_MONTH_MULT`,
`SWEEP_MAX_CONCURRENCY`, `CELL_SECONDS`, `TARGET_MAX`, `LOCATION`. See `.env.example` for the full
list with the published values.

### Run stages individually

Skip stages with `SKIP_DEPLOY=1` / `SKIP_GEN=1` / `SKIP_SCALEDOWN=1`, or run them by hand:

```bash
./infra/deploy.sh                                            # provision + write .env
cd src/DataGenerator     && dotnet run -c Release && cd ../..  # generate ~1.27M docs + manifest
./infra/scale-down.sh                                       # drop to idle floor (10,000 RU/s)
cd src/DotNetQueryClient && dotnet run -c Release && cd ../..  # -> diagnostics/dotnet/
cd src/go-query-client   && go run .              && cd ../..  # -> diagnostics/go/
python3 analysis/report.py                                  # -> analysis/report.html
```

Query targets baked into `config/seed-manifest.json`: `year=2024, month=03, day=15`
(day ≈ 1,000 docs, month ≈ 31,000, year ≈ 636,000).

Published run parameters, for reference:

| Parameter | Published value | Env knob (override before deploy) |
|---|---|---|
| Region | `australiaeast` | `LOCATION` |
| Autoscale max during load | **100,000 RU/s** (→ ~10 partitions) | `COSMOS_MAX_THROUGHPUT` |
| Autoscale max after scale-down | **10,000 RU/s** | `TARGET_MAX` |
| Orders/day | **1,000** (busy month 2024-06 × 10) → ~1.27M docs | `GEN_ORDERS_PER_DAY`, `GEN_BUSY_MONTH_MULT` |
| Query targets | `year=2024, month=03, day=15` | generated into `config/seed-manifest.json` |
| Concurrency sweep | `maxConc=6`, `cell=10s` | `SWEEP_MAX_CONCURRENCY`, `CELL_SECONDS` |

## Cost & teardown

**This benchmark bills real money.** Autoscale is provisioned (not serverless):

- **During the load window** the container runs at autoscale max **100,000 RU/s** — roughly **10×**
  the idle rate — to force the 10-partition split. Keep this window short (generation of ~1.27M
  small docs).
- **After `scale-down.sh`** the container sits at **10,000 RU/s**, which bills **~US$29/day** while
  idle. Storage for ~1.27M small docs is **<1 GB**.
- The physical partitions created during the burst do **not** merge back after scale-down, so the
  routing gradient stays observable at the lower cost.

When you're done, delete everything:

```bash
./infra/teardown.sh          # deletes the whole resource group (irreversible, prompts y/N)
```

## Verify the container really is HPK

```bash
az cosmosdb sql container show -g rg-cosmos-hpk-test -a <account> -d ordersdb -n orders \
  --query 'resource.partitionKey'
# => { "kind": "MultiHash", "paths": ["/year","/month","/day"], "version": 2 }
```

The `.NET` query client also prints the container's physical-partition (feed-range) count at
startup; if it prints `1`, the routing gradient will be flat (raise `maxThroughput` and reload).

## Note on the published account identifier

The report and committed diagnostics contain the Cosmos **account name / endpoint**
(`cosmos-hpk-…-australiaeast.documents.azure.com`). That is a non-secret identifier, and the
account has been torn down. The account **key** is never committed — it lives only in the
git-ignored `.env`.

## License

MIT — see [LICENSE](LICENSE).
