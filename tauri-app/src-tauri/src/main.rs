#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;

use tauri::{Manager, WindowEvent};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

struct BackendGuard {
    child: Option<Child>,
}

impl Drop for BackendGuard {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.take() {
            stop_backend(&mut child);
        }
    }
}

struct BackendState(Mutex<BackendGuard>);

#[tauri::command]
fn restart_app(app: tauri::AppHandle) {
    app.restart();
}

fn read_debug_mode(data_dir: &PathBuf) -> bool {
    // 读取：{app_data_dir}/config.json 里的 debug_mode
    // 任何异常都默认 false（发布更稳）
    let cfg_path = data_dir.join("config.json");
    let Ok(s) = fs::read_to_string(cfg_path) else {
        return false;
    };
    let Ok(v) = serde_json::from_str::<serde_json::Value>(&s) else {
        return false;
    };
    v.get("debug_mode")
        .and_then(|x| x.as_bool())
        .unwrap_or(false)
}

fn backend_python_candidates(exe_dir: &PathBuf, debug_mode: bool) -> Vec<PathBuf> {
    let bundled_pythonw = exe_dir.join("python").join("pythonw.exe");
    let bundled_python = exe_dir.join("python").join("python.exe");

    let mut candidates = Vec::new();

    if debug_mode {
        if bundled_python.exists() {
            candidates.push(bundled_python);
        }
        candidates.push(PathBuf::from("python"));
    } else {
        if bundled_pythonw.exists() {
            candidates.push(bundled_pythonw);
        }
        if bundled_python.exists() {
            candidates.push(bundled_python);
        }

        // 项目内未携带 Python 时，回退到系统默认 Python 环境。
        candidates.push(PathBuf::from("pythonw"));
        candidates.push(PathBuf::from("python"));
    }

    candidates
}

fn start_backend(data_dir: Option<PathBuf>, debug_mode: bool) -> Option<Child> {
    let exe_dir = std::env::current_exe().ok()?.parent()?.to_path_buf();
    
    let server = exe_dir.join("pyserver").join("server.py");
    if !server.exists() {
        eprintln!("[backend] server.py not found: {:?}", server);
        return None;
    }

    for python_bin in backend_python_candidates(&exe_dir, debug_mode) {
        let mut cmd = Command::new(&python_bin);
        cmd.arg(&server)
            .current_dir(&exe_dir)
            .env("BACKEND_PORT", "8000");

        // 把 tauri 的 app_data_dir 传给 python（统一 config/logs 目录）
        if let Some(d) = data_dir.as_ref() {
            cmd.env("MWA_DATA_DIR", d);
        }

        // Windows 下非调试强制不弹窗；即使回退到 python.exe 也隐藏控制台
        #[cfg(target_os = "windows")]
        if !debug_mode {
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        match cmd.spawn() {
            Ok(child) => {
                eprintln!("[backend] started with {:?}", python_bin);
                return Some(child);
            }
            Err(err) => {
                eprintln!("[backend] failed to start with {:?}: {}", python_bin, err);
            }
        }
    }

    eprintln!("[backend] no usable python runtime found");
    None
}

fn stop_backend(child: &mut Child) {
    let pid = child.id().to_string();
    std::thread::spawn(move || {
        #[cfg(target_os = "windows")]
        {
            let _ = Command::new("taskkill")
                .args(["/T", "/F", "/PID", &pid])
                .status();
        }
        #[cfg(not(target_os = "windows"))]
        {
            let _ = Command::new("kill").arg(&pid).status();
        }
    });
}

fn main() {
    tauri::Builder::default()
        .manage(BackendState(Mutex::new(BackendGuard { child: None })))
        .invoke_handler(tauri::generate_handler![restart_app])
        .setup(|app| {
            let data_dir = app.path().app_data_dir().ok();

            // ✅ 从 app_data_dir/config.json 读取 debug_mode（没有就默认 false）
            let debug_mode = data_dir
                .as_ref()
                .map(read_debug_mode)
                .unwrap_or(false);

            let child = start_backend(data_dir, debug_mode);

            if child.is_none() {
                eprintln!("[backend] failed to start backend");
            }

            let state = app.state::<BackendState>();
            state.0.lock().unwrap().child = child;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { .. } = event {
                let state = window.state::<BackendState>();
                let mut guard = state.0.lock().unwrap();
                if let Some(mut child) = guard.child.take() {
                    stop_backend(&mut child);
                }
            }
        })
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init()) // ✅ 只 init 一次
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
