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

use serde::{Deserialize, Serialize};

#[cfg(target_os = "windows")]
use windows::Win32::Graphics::Dxgi::{
    CreateDXGIFactory1, IDXGIAdapter1, IDXGIFactory1, DXGI_ADAPTER_DESC1,
    DXGI_ADAPTER_FLAG_SOFTWARE, DXGI_ERROR_NOT_FOUND,
};

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

struct ModelDownloadState(Mutex<ModelDownloadGuard>);

struct ModelDownloadGuard {
    state: String,
    message: String,
    error: Option<String>,
    downloaded_bytes: u64,
    total_bytes: u64,
    active: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ModelStatus {
    ready: bool,
    path: String,
    missing_files: Vec<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ModelDownloadSnapshot {
    state: String,
    message: String,
    error: Option<String>,
    downloaded_bytes: u64,
    total_bytes: u64,
    progress: f64,
}

#[derive(Deserialize)]
struct ModelDownloadEvent {
    event: String,
    message: Option<String>,
    error: Option<String>,
    downloaded_bytes: Option<u64>,
    total_bytes: Option<u64>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct GraphicsCapability {
    checked: bool,
    has_dedicated_gpu: Option<bool>,
    adapters: Vec<String>,
    reason: String,
    dedicated_video_memory_mb: Option<u64>,
    threshold_mb: u64,
}

const LIQUID_MATERIAL_MIN_DEDICATED_MB: u64 = 4 * 1024;
const MODEL_DIR_NAME: &str = "paraphrase-multilingual-MiniLM-L12-v2";
const REQUIRED_MODEL_FILES: &[&str] = &[
    "1_Pooling/config.json",
    "config.json",
    "config_sentence_transformers.json",
    "model.safetensors",
    "modules.json",
    "sentence_bert_config.json",
    "tokenizer.json",
];

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

#[tauri::command]
fn get_model_status() -> Result<ModelStatus, String> {
    let model_dir = resolve_model_dir().ok_or_else(|| "pyserver/server.py not found".to_string())?;
    let missing_files = missing_model_files(&model_dir);
    Ok(ModelStatus {
        ready: missing_files.is_empty(),
        path: model_dir.display().to_string(),
        missing_files,
    })
}

#[tauri::command]
fn get_model_download_status(state: State<ModelDownloadState>) -> ModelDownloadSnapshot {
    let guard = state.0.lock().unwrap();
    let progress = if guard.total_bytes > 0 {
        (guard.downloaded_bytes as f64 / guard.total_bytes as f64).clamp(0.0, 1.0)
    } else if guard.state == "completed" {
        1.0
    } else {
        0.0
    };

    ModelDownloadSnapshot {
        state: guard.state.clone(),
        message: guard.message.clone(),
        error: guard.error.clone(),
        downloaded_bytes: guard.downloaded_bytes,
        total_bytes: guard.total_bytes,
        progress,
    }
}

#[tauri::command]
fn ensure_backend_started(app: tauri::AppHandle, state: State<BackendState>) -> Result<Option<u16>, String> {
    {
        let guard = state.0.lock().unwrap();
        if let Some(port) = guard.port {
            return Ok(Some(port));
        }
    }

    let model_dir = resolve_model_dir().ok_or_else(|| "pyserver/server.py not found".to_string())?;
    let missing_files = missing_model_files(&model_dir);
    if !missing_files.is_empty() {
        return Err(format!("model missing: {}", missing_files.join(", ")));
    }

    let data_dir = app.path().app_data_dir().ok();
    let debug_mode = data_dir.as_ref().map(read_debug_mode).unwrap_or(false);
    let Some((child, port)) = start_backend(data_dir, debug_mode) else {
        return Err("failed to start backend".to_string());
    };

    let mut guard = state.0.lock().unwrap();
    if guard.port.is_none() {
        guard.child = Some(child);
        guard.port = Some(port);
    }
    Ok(guard.port)
}

#[tauri::command]
fn start_model_download(
    app: tauri::AppHandle,
    download_state: State<ModelDownloadState>,
) -> Result<(), String> {
    let model_dir = resolve_model_dir().ok_or_else(|| "pyserver/server.py not found".to_string())?;
    if missing_model_files(&model_dir).is_empty() {
        {
            let mut guard = download_state.0.lock().unwrap();
            guard.state = "completed".to_string();
            guard.message = "模型已就绪".to_string();
            guard.error = None;
            guard.active = false;
        }
        return Ok(());
    }

    {
        let mut guard = download_state.0.lock().unwrap();
        if guard.active {
            return Ok(());
        }
        guard.state = "starting".to_string();
        guard.message = "正在启动模型下载...".to_string();
        guard.error = None;
        guard.downloaded_bytes = 0;
        guard.total_bytes = 0;
        guard.active = true;
    }

    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|v| v.to_path_buf()))
        .ok_or_else(|| "failed to resolve executable directory".to_string())?;
    let server = resolve_backend_server(&exe_dir).ok_or_else(|| "pyserver/server.py not found".to_string())?;
    let server_root = server
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.to_path_buf())
        .ok_or_else(|| "failed to resolve server root".to_string())?;
    let downloader = server
        .parent()
        .map(|p| p.join("download_model.py"))
        .ok_or_else(|| "failed to resolve download_model.py".to_string())?;
    if !downloader.exists() {
        return Err(format!("download script not found: {}", downloader.display()));
    }

    let data_dir = app.path().app_data_dir().ok();
    let debug_mode = data_dir.as_ref().map(read_debug_mode).unwrap_or(false);
    let python_bin = backend_download_python_candidates(&exe_dir)
        .into_iter()
        .find(|candidate| Command::new(candidate).arg("--version").output().is_ok())
        .ok_or_else(|| "no usable python runtime found".to_string())?;

    let app_handle = app.clone();

    std::thread::spawn(move || {
        let mut cmd = Command::new(&python_bin);
        cmd.arg("-u")
            .arg(&downloader)
            .current_dir(&server_root)
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());

        if let Some(d) = data_dir.as_ref() {
            cmd.env("MWA_DATA_DIR", d);
        }

        #[cfg(target_os = "windows")]
        if !debug_mode {
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = match cmd.spawn() {
            Ok(child) => child,
            Err(err) => {
                let state_handle = app_handle.state::<ModelDownloadState>();
                let mut guard = state_handle.0.lock().unwrap();
                guard.state = "error".to_string();
                guard.message = "启动下载失败".to_string();
                guard.error = Some(err.to_string());
                guard.active = false;
                return;
            }
        };

        let stdout = match child.stdout.take() {
            Some(stdout) => stdout,
            None => {
                let state_handle = app_handle.state::<ModelDownloadState>();
                let mut guard = state_handle.0.lock().unwrap();
                guard.state = "error".to_string();
                guard.message = "无法读取下载进度".to_string();
                guard.error = Some("missing stdout".to_string());
                guard.active = false;
                let _ = child.kill();
                return;
            }
        };

        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            let Ok(line) = line else {
                continue;
            };
            if let Some(payload) = line.strip_prefix("MODEL_DOWNLOAD ") {
                if let Ok(event) = serde_json::from_str::<ModelDownloadEvent>(payload) {
                    let state_handle = app_handle.state::<ModelDownloadState>();
                    let mut guard = state_handle.0.lock().unwrap();
                    match event.event.as_str() {
                        "meta" => {
                            guard.state = "downloading".to_string();
                            guard.total_bytes = event.total_bytes.unwrap_or(guard.total_bytes);
                            guard.downloaded_bytes =
                                event.downloaded_bytes.unwrap_or(guard.downloaded_bytes);
                            guard.message = format!("正在下载模型... {}/{}", guard.downloaded_bytes, guard.total_bytes);
                            guard.error = None;
                        }
                        "status" => {
                            guard.state = "downloading".to_string();
                            if let Some(message) = event.message {
                                guard.message = message;
                            }
                        }
                        "progress" => {
                            guard.state = "downloading".to_string();
                            guard.total_bytes = event.total_bytes.unwrap_or(guard.total_bytes);
                            guard.downloaded_bytes =
                                event.downloaded_bytes.unwrap_or(guard.downloaded_bytes);
                        }
                        "done" => {
                            if let Some(total_bytes) = event.total_bytes {
                                guard.total_bytes = total_bytes;
                                guard.downloaded_bytes = total_bytes;
                            }
                            guard.state = "completed".to_string();
                            guard.message = "模型下载完成".to_string();
                            guard.error = None;
                            guard.active = false;
                        }
                        "error" => {
                            guard.state = "error".to_string();
                            guard.message = "模型下载失败".to_string();
                            guard.error = event.error;
                            guard.active = false;
                        }
                        _ => {}
                    }
                }
                continue;
            }
            if !line.trim().is_empty() {
                eprintln!("[model download] {}", line);
            }
        }

        match child.wait() {
            Ok(status) if status.success() => {
                let state_handle = app_handle.state::<ModelDownloadState>();
                let mut guard = state_handle.0.lock().unwrap();
                if guard.state != "completed" {
                    guard.state = "completed".to_string();
                    guard.message = "模型下载完成".to_string();
                    guard.error = None;
                }
                guard.active = false;
            }
            Ok(status) => {
                let state_handle = app_handle.state::<ModelDownloadState>();
                let mut guard = state_handle.0.lock().unwrap();
                if guard.state != "error" {
                    guard.state = "error".to_string();
                    guard.message = "模型下载失败".to_string();
                    guard.error = Some(format!("process exited with {}", status));
                }
                guard.active = false;
            }
            Err(err) => {
                let state_handle = app_handle.state::<ModelDownloadState>();
                let mut guard = state_handle.0.lock().unwrap();
                guard.state = "error".to_string();
                guard.message = "模型下载失败".to_string();
                guard.error = Some(err.to_string());
                guard.active = false;
            }
        }
    });

    Ok(())
}

#[cfg(target_os = "windows")]
fn detect_graphics_capability_impl() -> GraphicsCapability {
    let adapters = match enumerate_dxgi_adapters() {
        Ok(adapters) => adapters,
        Err(err) => {
            return GraphicsCapability {
                checked: false,
                has_dedicated_gpu: None,
                adapters: Vec::new(),
                reason: format!("DXGI query failed: {err}"),
                dedicated_video_memory_mb: None,
                threshold_mb: LIQUID_MATERIAL_MIN_DEDICATED_MB,
            };
        }
    };

    if adapters.is_empty() {
        return GraphicsCapability {
            checked: true,
            has_dedicated_gpu: None,
            adapters: Vec::new(),
            reason: "DXGI reported no physical adapters".to_string(),
            dedicated_video_memory_mb: None,
            threshold_mb: LIQUID_MATERIAL_MIN_DEDICATED_MB,
        };
    }

    let adapter_labels = adapters
        .iter()
        .map(|adapter| format!("{} ({} MB)", adapter.name, adapter.dedicated_video_memory_mb))
        .collect::<Vec<_>>();
    let max_dedicated_video_memory_mb = adapters
        .iter()
        .map(|adapter| adapter.dedicated_video_memory_mb)
        .max();

    let has_dedicated_gpu = max_dedicated_video_memory_mb
        .map(|memory_mb| memory_mb > LIQUID_MATERIAL_MIN_DEDICATED_MB);

    GraphicsCapability {
        checked: true,
        has_dedicated_gpu,
        adapters: adapter_labels,
        reason: match max_dedicated_video_memory_mb {
            Some(memory_mb) if memory_mb > LIQUID_MATERIAL_MIN_DEDICATED_MB => format!(
                "max DedicatedVideoMemory = {memory_mb} MB, above {} MB threshold",
                LIQUID_MATERIAL_MIN_DEDICATED_MB
            ),
            Some(memory_mb) => format!(
                "max DedicatedVideoMemory = {memory_mb} MB, not above {} MB threshold",
                LIQUID_MATERIAL_MIN_DEDICATED_MB
            ),
            None => "DedicatedVideoMemory unavailable".to_string(),
        },
        dedicated_video_memory_mb: max_dedicated_video_memory_mb,
        threshold_mb: LIQUID_MATERIAL_MIN_DEDICATED_MB,
    }
}

#[cfg(not(target_os = "windows"))]
fn detect_graphics_capability_impl() -> GraphicsCapability {
    GraphicsCapability {
        checked: false,
        has_dedicated_gpu: None,
        adapters: Vec::new(),
        reason: "GPU detection is only implemented on Windows".to_string(),
        dedicated_video_memory_mb: None,
        threshold_mb: LIQUID_MATERIAL_MIN_DEDICATED_MB,
    }
}

#[cfg(target_os = "windows")]
struct DxgiAdapterInfo {
    name: String,
    dedicated_video_memory_mb: u64,
}

#[cfg(target_os = "windows")]
fn enumerate_dxgi_adapters() -> Result<Vec<DxgiAdapterInfo>, String> {
    let factory: IDXGIFactory1 =
        unsafe { CreateDXGIFactory1() }.map_err(|err| err.to_string())?;
    let mut adapters = Vec::new();
    let mut index = 0;

    loop {
        let adapter = match unsafe { factory.EnumAdapters1(index) } {
            Ok(adapter) => adapter,
            Err(err) if err.code() == DXGI_ERROR_NOT_FOUND => break,
            Err(err) => return Err(err.to_string()),
        };

        if let Some(adapter_info) = read_dxgi_adapter(adapter)? {
            adapters.push(adapter_info);
        }
        index += 1;
    }

    Ok(adapters)
}

#[cfg(target_os = "windows")]
fn read_dxgi_adapter(adapter: IDXGIAdapter1) -> Result<Option<DxgiAdapterInfo>, String> {
    let desc: DXGI_ADAPTER_DESC1 = unsafe { adapter.GetDesc1() }.map_err(|err| err.to_string())?;
    if (desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE.0 as u32) != 0 {
        return Ok(None);
    }

    let name = utf16_to_string(&desc.Description);
    if name.is_empty() {
        return Ok(None);
    }

    Ok(Some(DxgiAdapterInfo {
        name,
        dedicated_video_memory_mb: bytes_to_mb(desc.DedicatedVideoMemory as u64),
    }))
}

fn bytes_to_mb(bytes: u64) -> u64 {
    bytes / 1024 / 1024
}

#[cfg(target_os = "windows")]
fn utf16_to_string(buf: &[u16]) -> String {
    let end = buf.iter().position(|c| *c == 0).unwrap_or(buf.len());
    String::from_utf16_lossy(&buf[..end]).trim().to_string()
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

fn backend_download_python_candidates(exe_dir: &PathBuf) -> Vec<PathBuf> {
    let bundled_python = exe_dir.join("python").join("python.exe");
    let mut candidates = Vec::new();
    if bundled_python.exists() {
        candidates.push(bundled_python);
    }
    candidates.push(PathBuf::from("python"));
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

fn resolve_model_dir() -> Option<PathBuf> {
    let exe_dir = std::env::current_exe().ok()?.parent()?.to_path_buf();
    let server = resolve_backend_server(&exe_dir)?;
    let pyserver_dir = server.parent()?;
    Some(pyserver_dir.join("models").join(MODEL_DIR_NAME))
}

fn missing_model_files(model_dir: &PathBuf) -> Vec<String> {
    REQUIRED_MODEL_FILES
        .iter()
        .filter_map(|relative| {
            let path = model_dir.join(relative);
            if path.exists() {
                None
            } else {
                Some((*relative).to_string())
            }
        })
        .collect()
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
        let mut cmd = Command::new("taskkill");
        cmd.args(["/T", "/F", "/PID", &pid]);
        cmd.creation_flags(CREATE_NO_WINDOW);
        let _ = cmd.status();
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
        .manage(ModelDownloadState(Mutex::new(ModelDownloadGuard {
            state: "idle".to_string(),
            message: "等待下载".to_string(),
            error: None,
            downloaded_bytes: 0,
            total_bytes: 0,
            active: false,
        })))
        .invoke_handler(tauri::generate_handler![
            restart_app,
            get_backend_port,
            detect_graphics_capability,
            get_model_status,
            get_model_download_status,
            start_model_download,
            ensure_backend_started
        ])
        .setup(|app| {
            let data_dir = app.path().app_data_dir().ok();

            // ✅ 从 app_data_dir/config.json 读取 debug_mode（没有就默认 false）
            let debug_mode = data_dir
                .as_ref()
                .map(read_debug_mode)
                .unwrap_or(false);

            let model_ready = resolve_model_dir()
                .map(|dir| missing_model_files(&dir).is_empty())
                .unwrap_or(false);
            let backend = if model_ready {
                start_backend(data_dir, debug_mode)
            } else {
                eprintln!("[backend] model missing, backend startup deferred");
                None
            };

            if model_ready && backend.is_none() {
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
