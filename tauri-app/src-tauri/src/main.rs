#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, RunEvent, State, WindowEvent};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

use serde::Serialize;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

struct BackendGuard {
    child: Option<Child>,
    port: Option<u16>,
}

impl Drop for BackendGuard {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.take() {
            stop_backend(&mut child);
        }
    }
}

struct BackendState(Mutex<BackendGuard>);

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct GraphicsCapability {
    checked: bool,
    has_dedicated_gpu: Option<bool>,
    adapters: Vec<String>,
    reason: String,
}

#[tauri::command]
fn restart_app(app: tauri::AppHandle, state: State<BackendState>) {
    kill_python_backend(&state);
    app.restart();
}

#[tauri::command]
fn get_backend_port(state: State<BackendState>) -> Option<u16> {
    state.0.lock().unwrap().port
}

#[tauri::command]
fn detect_graphics_capability() -> GraphicsCapability {
    detect_graphics_capability_impl()
}

#[cfg(target_os = "windows")]
fn detect_graphics_capability_impl() -> GraphicsCapability {
    let script = r#"
      $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
      Get-CimInstance Win32_VideoController |
        ForEach-Object { $_.Name } |
        ConvertTo-Json -Compress
    "#;

    let mut cmd = Command::new("powershell");
    cmd.args([
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]);
    cmd.creation_flags(CREATE_NO_WINDOW);

    let output = match cmd.output() {
        Ok(output) => output,
        Err(err) => {
            return GraphicsCapability {
                checked: false,
                has_dedicated_gpu: None,
                adapters: Vec::new(),
                reason: format!("failed to query video controllers: {}", err),
            };
        }
    };

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return GraphicsCapability {
            checked: false,
            has_dedicated_gpu: None,
            adapters: Vec::new(),
            reason: if stderr.is_empty() {
                "powershell query failed".to_string()
            } else {
                stderr
            },
        };
    }

    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let adapters = parse_graphics_adapter_names(&stdout);
    if adapters.is_empty() {
        return GraphicsCapability {
            checked: true,
            has_dedicated_gpu: None,
            adapters,
            reason: "no video adapters reported".to_string(),
        };
    }

    let hardware_adapters: Vec<String> = adapters
        .iter()
        .filter(|name| !is_virtual_or_software_adapter(name))
        .cloned()
        .collect();

    if hardware_adapters.iter().any(|name| is_dedicated_gpu_name(name)) {
        return GraphicsCapability {
            checked: true,
            has_dedicated_gpu: Some(true),
            adapters,
            reason: "dedicated GPU detected".to_string(),
        };
    }

    if !hardware_adapters.is_empty()
        && hardware_adapters
            .iter()
            .all(|name| is_integrated_gpu_name(name))
    {
        return GraphicsCapability {
            checked: true,
            has_dedicated_gpu: Some(false),
            adapters,
            reason: "only integrated GPU detected".to_string(),
        };
    }

    GraphicsCapability {
        checked: true,
        has_dedicated_gpu: None,
        adapters,
        reason: "unable to confidently classify GPU type".to_string(),
    }
}

#[cfg(not(target_os = "windows"))]
fn detect_graphics_capability_impl() -> GraphicsCapability {
    GraphicsCapability {
        checked: false,
        has_dedicated_gpu: None,
        adapters: Vec::new(),
        reason: "GPU detection is only implemented on Windows".to_string(),
    }
}

fn parse_graphics_adapter_names(raw: &str) -> Vec<String> {
    if raw.is_empty() {
        return Vec::new();
    }

    match serde_json::from_str::<serde_json::Value>(raw) {
        Ok(serde_json::Value::String(name)) => vec![name],
        Ok(serde_json::Value::Array(items)) => items
            .into_iter()
            .filter_map(|item| item.as_str().map(|s| s.trim().to_string()))
            .filter(|name| !name.is_empty())
            .collect(),
        _ => raw
            .lines()
            .map(str::trim)
            .filter(|line| !line.is_empty())
            .map(ToOwned::to_owned)
            .collect(),
    }
}

fn is_virtual_or_software_adapter(name: &str) -> bool {
    let n = name.to_ascii_lowercase();
    [
        "microsoft basic display",
        "remote display",
        "vmware",
        "virtualbox",
        "parallels",
        "hyper-v",
        "citrix",
    ]
    .iter()
    .any(|keyword| n.contains(keyword))
}

fn is_dedicated_gpu_name(name: &str) -> bool {
    let n = name.to_ascii_lowercase();

    if n.contains("nvidia")
        || n.contains("geforce")
        || n.contains("quadro")
        || n.contains("rtx ")
        || n.contains("gtx ")
        || n.contains("tesla")
        || n.contains("titan")
        || n.contains("intel arc")
    {
        return true;
    }

    if n.contains("radeon") {
        return n.contains(" rx ")
            || n.ends_with(" rx")
            || n.contains("pro")
            || n.contains("wx")
            || n.contains("firepro")
            || n.contains("vii");
    }

    false
}

fn is_integrated_gpu_name(name: &str) -> bool {
    let n = name.to_ascii_lowercase();

    if n.contains("intel")
        && (n.contains("uhd")
            || n.contains("iris")
            || n.contains("hd graphics")
            || n.contains("xe graphics"))
        && !n.contains("arc")
    {
        return true;
    }

    if n.contains("radeon(tm) graphics")
        || n.contains("radeon graphics")
        || n.contains("vega")
        || n.contains("680m")
        || n.contains("760m")
        || n.contains("780m")
        || n.contains("880m")
        || n.contains("890m")
    {
        return true;
    }

    false
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

fn backend_server_candidates(exe_dir: &PathBuf) -> Vec<PathBuf> {
    let mut candidates = Vec::new();

    // 开发态：固定回到仓库根目录使用源码版 pyserver/server.py。
    let dev_server = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("pyserver")
        .join("server.py");
    candidates.push(dev_server);

    // 打包态：使用可执行文件旁边携带的 pyserver/server.py。
    candidates.push(exe_dir.join("pyserver").join("server.py"));

    candidates
}

fn resolve_backend_server(exe_dir: &PathBuf) -> Option<PathBuf> {
    for candidate in backend_server_candidates(exe_dir) {
        if candidate.exists() {
            return Some(candidate);
        }
    }
    None
}

fn parse_backend_port_line(line: &str) -> Option<u16> {
    line.trim()
        .strip_prefix("PORT=")
        .and_then(|v| v.parse::<u16>().ok())
}

fn wait_backend_port(child: &mut Child) -> Option<u16> {
    let stdout = child.stdout.take()?;
    let (port_tx, port_rx) = std::sync::mpsc::channel::<u16>();

    std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        let mut port_sent = false;
        for line in reader.lines() {
            match line {
                Ok(line) => {
                    if !port_sent {
                        if let Some(port) = parse_backend_port_line(&line) {
                            let _ = port_tx.send(port);
                            port_sent = true;
                            continue;
                        }
                    }
                    if !line.trim().is_empty() {
                        eprintln!("[backend stdout] {}", line);
                    }
                }
                Err(err) => {
                    eprintln!("[backend] failed reading stdout: {}", err);
                    break;
                }
            }
        }
    });

    port_rx.recv_timeout(Duration::from_secs(10)).ok()
}

fn start_backend(data_dir: Option<PathBuf>, debug_mode: bool) -> Option<(Child, u16)> {
    let exe_dir = std::env::current_exe().ok()?.parent()?.to_path_buf();

    let Some(server) = resolve_backend_server(&exe_dir) else {
        eprintln!(
            "[backend] server.py not found. checked: {:?}",
            backend_server_candidates(&exe_dir)
        );
        return None;
    };
    let server_root = server
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| exe_dir.clone());

    for python_bin in backend_python_candidates(&exe_dir, debug_mode) {
        let mut cmd = Command::new(&python_bin);
        cmd.arg(&server)
            .current_dir(&server_root)
            .env("BACKEND_PORT", "0")
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());

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
            Ok(mut child) => {
                let Some(port) = wait_backend_port(&mut child) else {
                    eprintln!(
                        "[backend] started process but did not receive PORT line: python={:?}, server={:?}",
                        python_bin, server
                    );
                    stop_backend(&mut child);
                    continue;
                };
                eprintln!(
                    "[backend] started python backend: python={:?}, server={:?}, cwd={:?}, port={}",
                    python_bin, server, server_root, port
                );
                return Some((child, port));
            }
            Err(err) => {
                eprintln!(
                    "[backend] failed to start python backend: python={:?}, server={:?}, err={}",
                    python_bin, server, err
                );
            }
        }
    }

    eprintln!("[backend] no usable python runtime found for {:?}", server);
    None
}

fn stop_backend(child: &mut Child) {
    let pid = child.id().to_string();
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
}

fn kill_python_backend(state: &BackendState) {
    let mut guard = state.0.lock().unwrap();
    guard.port = None;
    if let Some(mut child) = guard.child.take() {
        stop_backend(&mut child);
    }
}

fn main() {
    tauri::Builder::default()
        .manage(BackendState(Mutex::new(BackendGuard {
            child: None,
            port: None,
        })))
        .invoke_handler(tauri::generate_handler![
            restart_app,
            get_backend_port,
            detect_graphics_capability
        ])
        .setup(|app| {
            let data_dir = app.path().app_data_dir().ok();

            // ✅ 从 app_data_dir/config.json 读取 debug_mode（没有就默认 false）
            let debug_mode = data_dir
                .as_ref()
                .map(read_debug_mode)
                .unwrap_or(false);

            let backend = start_backend(data_dir, debug_mode);

            if backend.is_none() {
                eprintln!("[backend] failed to start backend");
            }

            let state = app.state::<BackendState>();
            let mut guard = state.0.lock().unwrap();
            if let Some((child, port)) = backend {
                guard.child = Some(child);
                guard.port = Some(port);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { .. } = event {
                let state = window.state::<BackendState>();
                kill_python_backend(&state);
            }
        })
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init()) // ✅ 只 init 一次
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
                let state = app_handle.state::<BackendState>();
                kill_python_backend(&state);
            }
        });
}
