//! Panos desktop shell (ADR-015): window, tray, single instance, autostart, global shortcut
//! and notification plugins, and the node sidecar lifecycle with token handoff.

pub mod node;

use std::process::Child;
use std::sync::Mutex;
use std::time::Duration;

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, State};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};

use node::Connection;

#[derive(Default)]
struct NodeState {
    connection: Mutex<Option<Connection>>,
    child: Mutex<Option<Child>>, // set when this app started the node
}

/// The WebView's only privileged call: where the node is and the session token.
#[tauri::command]
fn connection(state: State<'_, NodeState>) -> Result<Connection, String> {
    if let Some(conn) = state.connection.lock().unwrap().clone() {
        return Ok(conn);
    }
    let exe_dir = std::env::current_exe().ok().and_then(|p| p.parent().map(|d| d.to_path_buf()));
    let env = |k: &str| std::env::var(k).ok();
    let (conn, child) = node::ensure_node(exe_dir.as_deref(), &env, Duration::from_secs(90))?;
    *state.connection.lock().unwrap() = Some(conn.clone());
    if child.is_some() {
        *state.child.lock().unwrap() = child;
    }
    Ok(conn)
}

#[tauri::command]
fn autostart_enabled(app: AppHandle) -> bool {
    app.autolaunch().is_enabled().unwrap_or(false)
}

#[tauri::command]
fn set_autostart(app: AppHandle, enabled: bool) -> Result<(), String> {
    let launcher = app.autolaunch();
    if enabled { launcher.enable() } else { launcher.disable() }.map_err(|e| e.to_string())
}

fn show_main(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn stop_started_node(app: &AppHandle) {
    if let Some(mut child) = app.state::<NodeState>().child.lock().unwrap().take() {
        // Jobs that were running are recovered as PAUSED (interrupted) on the next start.
        let _ = child.kill();
        let _ = child.wait();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_main(app)))
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, None))
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_notification::init())
        .manage(NodeState::default())
        .invoke_handler(tauri::generate_handler![connection, autostart_enabled, set_autostart])
        .setup(|app| {
            let open = MenuItem::with_id(app, "open", "Open Panos", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &quit])?;
            let mut tray = TrayIconBuilder::new().menu(&menu).tooltip("Panos");
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.on_menu_event(|app, event| match event.id.as_ref() {
                "open" => show_main(app),
                "quit" => {
                    stop_started_node(app);
                    app.exit(0);
                }
                _ => {}
            })
            .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the window keeps Panos running in the tray (jobs keep going).
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building the Panos desktop app")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                stop_started_node(app);
            }
        });
}
