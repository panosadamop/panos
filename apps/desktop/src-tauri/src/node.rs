//! Finding, attaching to and starting the local Panos node (ADR-002, ADR-015).
//!
//! The node writes `node.json` (host, port, token, pid) to its runtime directory. The shell
//! resolves the same directory as the Python side (`platformdirs.user_runtime_dir("Panos")`,
//! or `PANOS_RUNTIME_DIR`), attaches if that node answers `/api/v1/health`, and otherwise
//! starts the sidecar and waits for the runtime file.

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

pub const APP_NAME: &str = "Panos";
const RUNTIME_FILE: &str = "node.json";

#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct RuntimeInfo {
    pub pid: u32,
    pub host: String,
    pub port: u16,
    pub token: String,
    pub version: String,
}

/// What the WebView receives: never persisted on the frontend side.
#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Connection {
    pub base_url: String,
    pub token: String,
}

impl RuntimeInfo {
    pub fn connection(&self) -> Connection {
        Connection { base_url: format!("http://{}:{}", self.host, self.port), token: self.token.clone() }
    }
}

/// Mirror of `platformdirs.user_runtime_dir("Panos", appauthor=False)`.
pub fn runtime_dir(env: &dyn Fn(&str) -> Option<String>) -> Option<PathBuf> {
    if let Some(dir) = env("PANOS_RUNTIME_DIR").filter(|d| !d.is_empty()) {
        return Some(PathBuf::from(dir));
    }
    if cfg!(windows) {
        env("LOCALAPPDATA").map(|base| Path::new(&base).join("Temp").join(APP_NAME))
    } else if cfg!(target_os = "macos") {
        env("HOME").map(|home| Path::new(&home).join("Library/Caches/TemporaryItems").join(APP_NAME))
    } else {
        env("XDG_RUNTIME_DIR")
            .filter(|d| !d.is_empty())
            .map(|d| Path::new(&d).join(APP_NAME))
            .or_else(|| env("UID").map(|uid| PathBuf::from(format!("/run/user/{uid}/{APP_NAME}"))))
    }
}

pub fn runtime_file(env: &dyn Fn(&str) -> Option<String>) -> Option<PathBuf> {
    runtime_dir(env).map(|d| d.join(RUNTIME_FILE))
}

pub fn read_runtime(path: &Path) -> Option<RuntimeInfo> {
    let raw = std::fs::read_to_string(path).ok()?;
    let info: RuntimeInfo = serde_json::from_str(&raw).ok()?;
    // Loopback only (ADR-003): never hand a token to a non-local address.
    (info.host == "127.0.0.1" || info.host == "localhost").then_some(info)
}

pub fn healthy(conn: &Connection) -> bool {
    let agent = ureq::AgentBuilder::new().timeout(Duration::from_secs(3)).build();
    agent
        .get(&format!("{}/api/v1/health", conn.base_url))
        .set("Authorization", &format!("Bearer {}", conn.token))
        .call()
        .map(|r| r.status() == 200)
        .unwrap_or(false)
}

/// How to start the node: a bundled `panos-node` next to the app, `PANOS_NODE_CMD`, or
/// `panos` on PATH (developer installs).
pub fn node_command(exe_dir: Option<&Path>, env: &dyn Fn(&str) -> Option<String>) -> Vec<String> {
    if let Some(dir) = exe_dir {
        let name = if cfg!(windows) { "panos-node.exe" } else { "panos-node" };
        // Installed layout: <app>/panos-node/panos-node(.exe) (one-folder PyInstaller build).
        for bundled in [dir.join("panos-node").join(name), dir.join(name)] {
            if bundled.is_file() {
                return vec![bundled.to_string_lossy().into_owned(), "serve".into()];
            }
        }
    }
    if let Some(cmd) = env("PANOS_NODE_CMD").filter(|c| !c.trim().is_empty()) {
        return cmd.split_whitespace().map(String::from).collect();
    }
    vec!["panos".into(), "serve".into()]
}

pub fn spawn(command: &[String]) -> std::io::Result<Child> {
    let mut cmd = Command::new(&command[0]);
    cmd.args(&command[1..]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        cmd.creation_flags(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP);
    }
    cmd.spawn()
}

/// Attach to a running node or start one. Returns the connection and the child we started.
pub fn ensure_node(
    exe_dir: Option<&Path>,
    env: &dyn Fn(&str) -> Option<String>,
    timeout: Duration,
) -> Result<(Connection, Option<Child>), String> {
    let file = runtime_file(env).ok_or("cannot locate the Panos runtime directory")?;
    if let Some(info) = read_runtime(&file) {
        let conn = info.connection();
        if healthy(&conn) {
            return Ok((conn, None));
        }
    }
    let command = node_command(exe_dir, env);
    let mut child = spawn(&command).map_err(|e| format!("could not start `{}`: {e}", command.join(" ")))?;
    let started = Instant::now();
    while started.elapsed() < timeout {
        if let Ok(Some(status)) = child.try_wait() {
            return Err(format!("the Panos node exited during start-up ({status})"));
        }
        if let Some(info) = read_runtime(&file) {
            let conn = info.connection();
            if healthy(&conn) {
                return Ok((conn, Some(child)));
            }
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    let _ = child.kill();
    Err(format!("the Panos node did not start within {}s", timeout.as_secs()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn env_of(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let map: HashMap<String, String> = pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        move |k: &str| map.get(k).cloned()
    }

    #[test]
    fn runtime_dir_matches_platformdirs_and_honours_override() {
        let env = env_of(&[("PANOS_RUNTIME_DIR", "X:/custom"), ("LOCALAPPDATA", "C:/Users/me/AppData/Local")]);
        assert_eq!(runtime_dir(&env), Some(PathBuf::from("X:/custom")));
        let env = env_of(&[("LOCALAPPDATA", "C:/Users/me/AppData/Local"), ("HOME", "/home/me"),
                           ("XDG_RUNTIME_DIR", "/run/user/1000")]);
        let dir = runtime_dir(&env).unwrap();
        if cfg!(windows) {
            assert_eq!(dir, Path::new("C:/Users/me/AppData/Local").join("Temp").join("Panos"));
        } else if cfg!(target_os = "macos") {
            assert_eq!(dir, PathBuf::from("/home/me/Library/Caches/TemporaryItems/Panos"));
        } else {
            assert_eq!(dir, PathBuf::from("/run/user/1000/Panos"));
        }
        assert!(runtime_file(&env).unwrap().ends_with("node.json"));
    }

    #[test]
    fn runtime_file_is_parsed_and_only_loopback_is_accepted() {
        let dir = std::env::temp_dir().join(format!("panos-rt-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("node.json");
        std::fs::write(&file, r#"{"pid":7,"host":"127.0.0.1","port":48123,"token":"abc","version":"0.1.0","started_at":"2026-10-09T10:00:00Z"}"#).unwrap();
        let info = read_runtime(&file).unwrap();
        assert_eq!(info.connection(), Connection { base_url: "http://127.0.0.1:48123".into(), token: "abc".into() });
        std::fs::write(&file, r#"{"pid":7,"host":"10.0.0.5","port":1,"token":"abc","version":"x"}"#).unwrap();
        assert!(read_runtime(&file).is_none(), "a non-loopback node must be ignored");
        std::fs::write(&file, "not json").unwrap();
        assert!(read_runtime(&file).is_none());
        assert!(read_runtime(&dir.join("missing.json")).is_none());
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn node_command_prefers_bundled_sidecar_then_env_then_path() {
        let dir = std::env::temp_dir().join(format!("panos-bin-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let none = env_of(&[]);
        assert_eq!(node_command(Some(&dir), &none), vec!["panos", "serve"]);
        let env = env_of(&[("PANOS_NODE_CMD", "uv run panos serve")]);
        assert_eq!(node_command(Some(&dir), &env), vec!["uv", "run", "panos", "serve"]);
        std::fs::create_dir_all(dir.join("panos-node")).unwrap();
        let exe = dir.join("panos-node").join(if cfg!(windows) { "panos-node.exe" } else { "panos-node" });
        std::fs::write(&exe, b"").unwrap();
        assert_eq!(node_command(Some(&dir), &env)[1], "serve");
        assert!(node_command(Some(&dir), &env)[0].ends_with(exe.file_name().unwrap().to_str().unwrap()));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn unhealthy_when_nothing_listens() {
        let conn = Connection { base_url: "http://127.0.0.1:9".into(), token: "x".into() };
        assert!(!healthy(&conn));
    }
}
