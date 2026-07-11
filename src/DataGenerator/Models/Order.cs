using Newtonsoft.Json;

namespace DataGenerator.Models;

/// <summary>
/// Minimal order document. The three HPK path fields (year, month, day) MUST be
/// top-level properties whose JSON names exactly match the container's partition key
/// paths (/year, /month, /day). Stored as zero-padded strings for clean prefix matching.
/// </summary>
public class Order
{
    [JsonProperty("id")] public string Id { get; set; } = "";

    // --- Hierarchical partition key levels ---
    [JsonProperty("year")] public string Year { get; set; } = "";
    [JsonProperty("month")] public string Month { get; set; } = "";
    [JsonProperty("day")] public string Day { get; set; } = "";

    // --- Payload ---
    [JsonProperty("orderId")] public string OrderId { get; set; } = "";
    [JsonProperty("timestamp")] public string Timestamp { get; set; } = "";
    [JsonProperty("status")] public string Status { get; set; } = "";
    [JsonProperty("totalAmount")] public double TotalAmount { get; set; }
    [JsonProperty("itemCount")] public int ItemCount { get; set; }
    [JsonProperty("currency")] public string Currency { get; set; } = "";
}
