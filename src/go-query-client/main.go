package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/Azure/azure-sdk-for-go/sdk/azcore"
	"github.com/Azure/azure-sdk-for-go/sdk/azcore/policy"
	"github.com/Azure/azure-sdk-for-go/sdk/data/azcosmos"
)

// CellRow / RichRow mirror the .NET client's JSON so analysis merges both.
type CellRow struct {
	SDK         string  `json:"sdk"`
	Depth       string  `json:"depth"`
	Variant     string  `json:"variant"`
	Form        string  `json:"form"`
	Concurrency int     `json:"concurrency"`
	Completions int     `json:"completions"`
	Errors      int     `json:"errors"`
	ElapsedSec  float64 `json:"elapsedSec"`
	Qps         float64 `json:"qps"`
	RuPerSec    float64 `json:"ruPerSec"`
	P50         float64 `json:"p50"`
	P95         float64 `json:"p95"`
	P99         float64 `json:"p99"`
	Max         float64 `json:"max"`
	Avg         float64 `json:"avg"`
}

type RichRow struct {
	SDK                    string  `json:"sdk"`
	Depth                  string  `json:"depth"`
	Variant                string  `json:"variant"`
	Form                   string  `json:"form"`
	Error                  string  `json:"error"`
	RequestCharge          float64 `json:"requestCharge"`
	PartitionsContacted    int     `json:"partitionsContacted"`
	RetrievedDocumentCount int64   `json:"retrievedDocumentCount"`
	RetrievedDocumentSize  int64   `json:"retrievedDocumentSize"`
	OutputDocumentCount    int64   `json:"outputDocumentCount"`
	OutputDocumentSize     int64   `json:"outputDocumentSize"`
	IndexLookupMs          float64 `json:"indexLookupMs"`
	DocumentLoadMs         float64 `json:"documentLoadMs"`
	RuntimeExecMs          float64 `json:"runtimeExecMs"`
	TotalExecMs            float64 `json:"totalExecMs"`
	Pages                  int     `json:"pages"`
	PKRangeIDs             string  `json:"pkRangeIds"`
}

// QueryConfig: FullPK=true means attach the full 3-level partition key (the only
// PK the Go SDK can attach — no partial-prefix PK exists).
type QueryConfig struct {
	Depth, Variant, Form, SQL string
	FullPK                    bool
}

var (
	collector   = &headerCollector{}
	container   *azcosmos.ContainerClient
	year        string
	month       string
	day         string
	maxConc     int
	cellSeconds float64
	outDir      string
)

func main() {
	loadDotEnv()
	endpoint := env("COSMOS_ENDPOINT", "")
	key := env("COSMOS_KEY", "")
	dbID := env("COSMOS_DATABASE", "ordersdb")
	containerID := env("COSMOS_CONTAINER", "orders")
	maxConc = envInt("SWEEP_MAX_CONCURRENCY", 6)
	cellSeconds = envFloat("CELL_SECONDS", 12)
	if endpoint == "" || key == "" {
		fmt.Fprintln(os.Stderr, "ERROR: COSMOS_ENDPOINT / COSMOS_KEY not set.")
		os.Exit(1)
	}

	mf, err := loadManifest(repoPath("config/seed-manifest.json"))
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
		os.Exit(1)
	}
	year, month, day = mf.Query.Year, mf.Query.Month.Month, mf.Query.Day.Day
	fmt.Printf("Targets: year=%s month=%s day=%s  maxConc=%d cell=%.0fs\n", year, month, day, maxConc, cellSeconds)

	outDir = repoPath("diagnostics/go")
	os.MkdirAll(outDir, 0o755)

	cred, err := azcosmos.NewKeyCredential(key)
	if err != nil {
		fatal(err)
	}
	client, err := azcosmos.NewClientWithKey(endpoint, cred, &azcosmos.ClientOptions{
		ClientOptions: azcore.ClientOptions{PerCallPolicies: []policy.Policy{capturePolicy{collector}}},
	})
	if err != nil {
		fatal(err)
	}
	container, err = client.NewContainer(dbID, containerID)
	if err != nil {
		fatal(err)
	}

	var cells []CellRow
	var rich []RichRow
	for _, cfg := range buildMatrix() {
		_, _, _ = execLean(cfg) // warmup
		rr := execRich(cfg)
		rich = append(rich, rr)
		fmt.Printf("  [rich] %-6s %-9s %-6s RU=%9.1f parts=%d(gw) retrievedDocs=%d bytes=%d %s\n",
			cfg.Depth, cfg.Variant, cfg.Form, rr.RequestCharge, rr.PartitionsContacted,
			rr.RetrievedDocumentCount, rr.RetrievedDocumentSize, rr.Error)
		if rr.Error != "" { // e.g. cross-partition aggregate: not servable by the Go gateway
			fmt.Printf("  [skip sweep] %s/%s/%s — %s\n", cfg.Depth, cfg.Variant, cfg.Form, rr.Error)
			continue
		}
		for c := 1; c <= maxConc; c++ {
			cell := runCell(cfg, c)
			cells = append(cells, cell)
			fmt.Printf("  [sweep] %-6s %-9s %-6s c=%d qps=%7.2f p50=%7.1f p95=%7.1f ruPerSec=%9.0f n=%d err=%d\n",
				cfg.Depth, cfg.Variant, cfg.Form, c, cell.Qps, cell.P50, cell.P95, cell.RuPerSec, cell.Completions, cell.Errors)
		}
	}

	writeJSON(filepath.Join(outDir, "cells.json"), cells)
	writeJSON(filepath.Join(outDir, "metrics.json"), rich)
	fmt.Printf("\nWrote %d cells, %d rich rows -> %s\n", len(cells), len(rich), outDir)
}

func pk(cfg QueryConfig) azcosmos.PartitionKey {
	if cfg.FullPK {
		return azcosmos.NewPartitionKeyString(year).AppendString(month).AppendString(day)
	}
	return azcosmos.NewPartitionKey() // empty -> gateway routes via WHERE clause
}

func params(sql string) []azcosmos.QueryParameter {
	var p []azcosmos.QueryParameter
	if strings.Contains(sql, "@y") {
		p = append(p, azcosmos.QueryParameter{Name: "@y", Value: year})
	}
	if strings.Contains(sql, "@m") {
		p = append(p, azcosmos.QueryParameter{Name: "@m", Value: month})
	}
	if strings.Contains(sql, "@d") {
		p = append(p, azcosmos.QueryParameter{Name: "@d", Value: day})
	}
	return p
}

// execLean drains a query, returns (latencyMs, ru, error).
func execLean(cfg QueryConfig) (float64, float64, error) {
	start := time.Now()
	pager := container.NewQueryItemsPager(cfg.SQL, pk(cfg), &azcosmos.QueryOptions{QueryParameters: params(cfg.SQL)})
	var ru float64
	for pager.More() {
		page, err := pager.NextPage(context.Background())
		if err != nil {
			return float64(time.Since(start).Microseconds()) / 1000.0, ru, err
		}
		ru += float64(page.RequestCharge)
	}
	return float64(time.Since(start).Microseconds()) / 1000.0, ru, nil
}

// execRich drains once with capture enabled, accumulating query metrics + gateway pkranges.
func execRich(cfg QueryConfig) RichRow {
	collector.reset()
	collector.enable(true)
	defer collector.enable(false)

	start := time.Now()
	pager := container.NewQueryItemsPager(cfg.SQL, pk(cfg), &azcosmos.QueryOptions{
		QueryParameters: params(cfg.SQL), PopulateIndexMetrics: true,
	})
	var ru float64
	var qms []string
	pages := 0
	var qerr error
	for pager.More() {
		page, err := pager.NextPage(context.Background())
		if err != nil {
			qerr = err
			break
		}
		pages++
		ru += float64(page.RequestCharge)
		if page.QueryMetrics != nil {
			qms = append(qms, *page.QueryMetrics)
		}
	}
	_ = time.Since(start)
	caps := collector.snapshot()
	nparts, ids := distinctPKRanges(caps)
	writeJSON(filepath.Join(outDir, fmt.Sprintf("%s-%s-%s.diag.json", cfg.Depth, cfg.Variant, cfg.Form)), caps)

	rr := RichRow{
		SDK: "go", Depth: cfg.Depth, Variant: cfg.Variant, Form: cfg.Form,
		RequestCharge: round(ru, 3), PartitionsContacted: nparts, Pages: pages, PKRangeIDs: ids,
		RetrievedDocumentCount: int64(parseMetric(qms, "retrievedDocumentCount")),
		RetrievedDocumentSize:  int64(parseMetric(qms, "retrievedDocumentSize")),
		OutputDocumentCount:    int64(parseMetric(qms, "outputDocumentCount")),
		OutputDocumentSize:     int64(parseMetric(qms, "outputDocumentSize")),
		IndexLookupMs:          round(parseMetric(qms, "indexLookupTimeInMs"), 3),
		DocumentLoadMs:         round(parseMetric(qms, "documentLoadTimeInMs"), 3),
		RuntimeExecMs:          round(parseMetric(qms, "VMExecutionTimeInMs"), 3),
		TotalExecMs:            round(parseMetric(qms, "totalExecutionTimeInMs"), 3),
	}
	if qerr != nil {
		rr.Error = firstLine(qerr.Error())
	}
	return rr
}

func runCell(cfg QueryConfig, concurrency int) CellRow {
	deadline := time.Now().Add(time.Duration(cellSeconds * float64(time.Second)))
	var mu sync.Mutex
	var lats []float64
	var totalRu float64
	completions, errCount := 0, 0
	start := time.Now()

	var wg sync.WaitGroup
	for i := 0; i < concurrency; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for time.Now().Before(deadline) {
				lat, ru, err := execLean(cfg)
				mu.Lock()
				if err != nil {
					errCount++
				} else {
					lats = append(lats, lat)
					totalRu += ru
					completions++
				}
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	elapsed := time.Since(start).Seconds()
	sort.Float64s(lats)

	avg := 0.0
	if len(lats) > 0 {
		for _, v := range lats {
			avg += v
		}
		avg /= float64(len(lats))
	}
	maxv := 0.0
	if len(lats) > 0 {
		maxv = lats[len(lats)-1]
	}
	return CellRow{
		SDK: "go", Depth: cfg.Depth, Variant: cfg.Variant, Form: cfg.Form, Concurrency: concurrency,
		Completions: completions, Errors: errCount, ElapsedSec: round(elapsed, 3),
		Qps:      round(float64(completions)/math.Max(elapsed, 1e-6), 3),
		RuPerSec: round(totalRu/math.Max(elapsed, 1e-6), 1),
		P50:      pct(lats, 50), P95: pct(lats, 95), P99: pct(lats, 99),
		Max: round(maxv, 2), Avg: round(avg, 2),
	}
}

func buildMatrix() []QueryConfig {
	var list []QueryConfig
	depths := []struct {
		label, preds string
		full         bool
	}{
		{"3-hpk", "c.year=@y AND c.month=@m AND c.day=@d", true},
		{"2-hpk", "c.year=@y AND c.month=@m", false},
		{"1-hpk", "c.year=@y", false},
	}
	for _, dp := range depths {
		for _, form := range []string{"select", "count"} {
			sel := selectClause(form)
			list = append(list, QueryConfig{dp.label, "where", form, sel + " FROM c WHERE " + dp.preds, false})
			if dp.full { // Go can only attach a FULL partition key
				list = append(list, QueryConfig{dp.label, "pk", form, sel + " FROM c", true})
				list = append(list, QueryConfig{dp.label, "where+pk", form, sel + " FROM c WHERE " + dp.preds, true})
			}
		}
	}
	for _, form := range []string{"select", "count"} {
		list = append(list, QueryConfig{"0-hpk", "none", form, selectClause(form) + " FROM c", false})
	}
	return list
}

func selectClause(form string) string {
	if form == "count" {
		return "SELECT VALUE COUNT(1)"
	}
	return "SELECT *"
}

// ---- helpers ----
func pct(sorted []float64, p int) float64 {
	if len(sorted) == 0 {
		return 0
	}
	idx := int(math.Ceil(float64(p)/100*float64(len(sorted)))) - 1
	if idx < 0 {
		idx = 0
	}
	if idx >= len(sorted) {
		idx = len(sorted) - 1
	}
	return round(sorted[idx], 2)
}
func round(v float64, places int) float64 {
	p := math.Pow(10, float64(places))
	return math.Round(v*p) / p
}
func firstLine(s string) string {
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		return s[:i]
	}
	if len(s) > 160 {
		return s[:160]
	}
	return s
}

type manifest struct {
	Query struct {
		Year  string `json:"year"`
		Month struct {
			Month string `json:"month"`
		} `json:"month"`
		Day struct {
			Day string `json:"day"`
		} `json:"day"`
	} `json:"query"`
}

func loadManifest(path string) (manifest, error) {
	var mf manifest
	b, err := os.ReadFile(path)
	if err != nil {
		return mf, err
	}
	return mf, json.Unmarshal(b, &mf)
}
func writeJSON(path string, v any) {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		fatal(err)
	}
	if err := os.WriteFile(path, b, 0o644); err != nil {
		fatal(err)
	}
}
func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
func envInt(k string, def int) int {
	if v := os.Getenv(k); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}
func envFloat(k string, def float64) float64 {
	if v := os.Getenv(k); v != "" {
		if n, err := strconv.ParseFloat(v, 64); err == nil {
			return n
		}
	}
	return def
}
func loadDotEnv() {
	dir, _ := os.Getwd()
	for i := 0; i < 6 && dir != ""; i++ {
		if f, err := os.Open(filepath.Join(dir, ".env")); err == nil {
			sc := bufio.NewScanner(f)
			for sc.Scan() {
				line := strings.TrimSpace(sc.Text())
				if line == "" || strings.HasPrefix(line, "#") {
					continue
				}
				eq := strings.Index(line, "=")
				if eq <= 0 {
					continue
				}
				k := strings.TrimSpace(line[:eq])
				if os.Getenv(k) == "" {
					os.Setenv(k, strings.TrimSpace(line[eq+1:]))
				}
			}
			f.Close()
			return
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
}
func repoPath(rel string) string {
	dir, _ := os.Getwd()
	for i := 0; i < 6 && dir != ""; i++ {
		if st, err := os.Stat(filepath.Join(dir, "infra")); err == nil && st.IsDir() {
			return filepath.Join(dir, rel)
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	cwd, _ := os.Getwd()
	return filepath.Join(cwd, rel)
}
func fatal(err error) { fmt.Fprintln(os.Stderr, "FATAL:", err); os.Exit(1) }
