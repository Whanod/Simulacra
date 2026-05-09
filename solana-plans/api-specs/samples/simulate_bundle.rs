// Minimal Rust sample for `POST /v1/simulate-bundle`.
//
// Single-file reqwest + serde_json example. Drop into a Cargo project
// with these deps:
//
//     [dependencies]
//     reqwest = { version = "0.12", features = ["json", "rustls-tls"] }
//     serde_json = "1"
//     tokio = { version = "1", features = ["full"] }
//
// Usage:
//     DEFI_SIM_API_KEY=... cargo run --example simulate_bundle

use std::env;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let api_url = env::var("DEFI_SIM_API_URL")
        .unwrap_or_else(|_| "http://localhost:8000".to_string());
    let api_key = env::var("DEFI_SIM_API_KEY")
        .map_err(|_| "DEFI_SIM_API_KEY not set")?;

    let body = serde_json::json!({
        "bundle": {
            "txs": ["base58encodedtx1", "base58encodedtx2"],
            "tip_lamports": 100_000_u64,
            "tip_recipient": "T1pestRecipientPubkey11111111111111111111111",
        },
        "context_slot": "latest",
    });

    let client = reqwest::Client::new();
    let resp = client
        .post(format!("{api_url}/v1/simulate-bundle"))
        .bearer_auth(&api_key)
        .json(&body)
        .send()
        .await?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("HTTP {status}: {text}").into());
    }

    let json: serde_json::Value = resp.json().await?;
    println!("{}", serde_json::to_string_pretty(&json)?);
    Ok(())
}
