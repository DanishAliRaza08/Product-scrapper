//! Desktop shell for the Product Intelligence platform.
//!
//! The UI is the same static web build used by the cloud dashboard. This shell adds
//! what a browser cannot do safely:
//! * the refresh token lives in the OS credential store (Windows Credential Manager),
//!   never in web storage or the frontend bundle;
//! * exports are saved straight to the user's Downloads folder;
//! * native notifications when background jobs finish.
//!
//! The desktop never talks to the database: every call goes to the versioned HTTPS API.

use std::path::PathBuf;

use futures_util::StreamExt;
use serde::Deserialize;
use tauri::{AppHandle, Manager, State};
use tauri_plugin_notification::NotificationExt;
use tokio::io::AsyncWriteExt;

const KEYRING_SERVICE: &str = "com.productintelligence.desktop";
const KEYRING_USER: &str = "refresh-token";
const DEFAULT_API_URL: &str = "http://localhost:8000";

#[derive(Debug, Deserialize, Default)]
struct DesktopConfig {
    api_url: Option<String>,
}

struct AppConfig {
    api_url: String,
}

/// API base URL: `MPI_API_URL` env var, else `config.json` in the app config dir
/// (e.g. `%APPDATA%\com.productintelligence.desktop\config.json`), else the default.
fn load_api_url(app: &AppHandle) -> String {
    if let Ok(v) = std::env::var("MPI_API_URL") {
        return v.trim_end_matches('/').to_string();
    }
    let from_file = app
        .path()
        .app_config_dir()
        .ok()
        .map(|dir| dir.join("config.json"))
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|s| serde_json::from_str::<DesktopConfig>(&s).ok())
        .and_then(|c| c.api_url);
    from_file
        .unwrap_or_else(|| DEFAULT_API_URL.to_string())
        .trim_end_matches('/')
        .to_string()
}

fn keyring_entry() -> Result<keyring::Entry, String> {
    keyring::Entry::new(KEYRING_SERVICE, KEYRING_USER).map_err(|e| e.to_string())
}

#[tauri::command]
fn store_refresh_token(token: String) -> Result<(), String> {
    keyring_entry()?.set_password(&token).map_err(|e| e.to_string())
}

#[tauri::command]
fn load_refresh_token() -> Result<Option<String>, String> {
    match keyring_entry()?.get_password() {
        Ok(token) => Ok(Some(token)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}

#[tauri::command]
fn clear_refresh_token() -> Result<(), String> {
    match keyring_entry()?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(e.to_string()),
    }
}

#[tauri::command]
fn api_base_url(config: State<'_, AppConfig>) -> String {
    config.api_url.clone()
}

#[tauri::command]
fn notify(app: AppHandle, title: String, body: String) -> Result<(), String> {
    app.notification()
        .builder()
        .title(title)
        .body(body)
        .show()
        .map_err(|e| e.to_string())
}

/// Reduce a server-suggested filename to a safe basename.
fn safe_filename(name: &str) -> String {
    let cleaned: String = name
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.') { c } else { '_' })
        .collect();
    let trimmed = cleaned.trim_start_matches('.');
    if trimmed.is_empty() { "export.bin".to_string() } else { trimmed.chars().take(120).collect() }
}

fn unique_path(dir: PathBuf, name: &str) -> PathBuf {
    let candidate = dir.join(name);
    if !candidate.exists() {
        return candidate;
    }
    let (stem, ext) = match name.rsplit_once('.') {
        Some((s, e)) => (s.to_string(), format!(".{e}")),
        None => (name.to_string(), String::new()),
    };
    (1..1000)
        .map(|i| dir.join(format!("{stem} ({i}){ext}")))
        .find(|p| !p.exists())
        .unwrap_or(candidate)
}

/// Download a signed export link into the user's Downloads folder. Only URLs on the
/// configured API origin are accepted, so the page cannot use this to fetch arbitrary hosts.
#[tauri::command]
async fn download_file(url: String, filename: String, config: State<'_, AppConfig>) -> Result<String, String> {
    let target = url::Url::parse(&url).map_err(|_| "Invalid download URL".to_string())?;
    let api = url::Url::parse(&config.api_url).map_err(|_| "Invalid API URL".to_string())?;
    if target.origin() != api.origin() {
        return Err("Downloads are only allowed from the configured API".into());
    }
    let dir = dirs::download_dir().ok_or("No Downloads folder found")?;
    let path = unique_path(dir, &safe_filename(&filename));
    let response = reqwest::get(target).await.map_err(|e| e.to_string())?;
    if !response.status().is_success() {
        return Err(format!("Download failed: HTTP {}", response.status()));
    }
    let mut file = tokio::fs::File::create(&path).await.map_err(|e| e.to_string())?;
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        file.write_all(&chunk.map_err(|e| e.to_string())?).await.map_err(|e| e.to_string())?;
    }
    file.flush().await.map_err(|e| e.to_string())?;
    Ok(path.to_string_lossy().into_owned())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .setup(|app| {
            let api_url = load_api_url(app.handle());
            app.manage(AppConfig { api_url });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            store_refresh_token,
            load_refresh_token,
            clear_refresh_token,
            api_base_url,
            notify,
            download_file
        ])
        .run(tauri::generate_context!())
        .expect("error while running the Product Intelligence desktop app");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn filenames_are_sanitised() {
        assert_eq!(safe_filename("../../etc/passwd"), "_.._etc_passwd");
        assert_eq!(safe_filename("product-export-2026.xlsx"), "product-export-2026.xlsx");
        assert_eq!(safe_filename(""), "export.bin");
        assert_eq!(safe_filename("..."), "export.bin");
    }
}
