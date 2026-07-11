package main

import (
	"net/http"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"

	"github.com/Azure/azure-sdk-for-go/sdk/azcore/policy"
)

// capturedResponse holds Cosmos diagnostics headers from one gateway response.
// azcosmos runs cross-partition queries in the gateway, so the per-response
// x-ms-documentdb-partitionkeyrangeid header is the only client-side signal of
// which partition-key range the gateway touched for that page.
type capturedResponse struct {
	Status     int    `json:"status"`
	PKRangeID  string `json:"partitionKeyRangeId"`
	ActivityID string `json:"activityId"`
	Charge     string `json:"requestCharge"`
}

// headerCollector accumulates captured responses. Capture is gated by `enabled`
// so the concurrency sweep (thousands of requests) doesn't grow it unbounded —
// only the single-threaded rich probe enables it.
type headerCollector struct {
	mu      sync.Mutex
	enabled atomic.Bool
	caps    []capturedResponse
}

func (c *headerCollector) reset()       { c.mu.Lock(); c.caps = nil; c.mu.Unlock() }
func (c *headerCollector) enable(b bool) { c.enabled.Store(b) }
func (c *headerCollector) add(cp capturedResponse) {
	c.mu.Lock()
	c.caps = append(c.caps, cp)
	c.mu.Unlock()
}
func (c *headerCollector) snapshot() []capturedResponse {
	c.mu.Lock()
	defer c.mu.Unlock()
	out := make([]capturedResponse, len(c.caps))
	copy(out, c.caps)
	return out
}

type capturePolicy struct{ c *headerCollector }

func (p capturePolicy) Do(req *policy.Request) (*http.Response, error) {
	resp, err := req.Next()
	if resp != nil && p.c.enabled.Load() {
		p.c.add(capturedResponse{
			Status:     resp.StatusCode,
			PKRangeID:  resp.Header.Get("x-ms-documentdb-partitionkeyrangeid"),
			ActivityID: resp.Header.Get("x-ms-activity-id"),
			Charge:     resp.Header.Get("x-ms-request-charge"),
		})
	}
	return resp, err
}

// distinctPKRanges returns the count and comma-joined ids of the distinct
// partition-key ranges the gateway reported (in first-seen order).
func distinctPKRanges(caps []capturedResponse) (int, string) {
	seen := map[string]struct{}{}
	var order []string
	for _, c := range caps {
		if c.PKRangeID == "" {
			continue
		}
		if _, ok := seen[c.PKRangeID]; !ok {
			order = append(order, c.PKRangeID)
		}
		seen[c.PKRangeID] = struct{}{}
	}
	return len(seen), strings.Join(order, ",")
}

// parseMetric sums a numeric field across the per-page query-metrics strings.
// Each string is a ';'-delimited k=v list (e.g. retrievedDocumentCount=500).
func parseMetric(metrics []string, key string) float64 {
	total := 0.0
	for _, qm := range metrics {
		for _, part := range strings.Split(qm, ";") {
			kv := strings.SplitN(part, "=", 2)
			if len(kv) == 2 && strings.EqualFold(strings.TrimSpace(kv[0]), key) {
				if n, err := strconv.ParseFloat(strings.TrimSpace(kv[1]), 64); err == nil {
					total += n
				}
			}
		}
	}
	return total
}
