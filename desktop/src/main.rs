#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs;
use std::io::{Cursor, Read};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::Duration;

use tauri::http::{Request, Response};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_deep_link::DeepLinkExt;
use tauri_plugin_opener::OpenerExt;

const SCHEME: &str = "report-bot-app";
const LOCAL: &str = "http://127.0.0.1:8780";
const BACKEND: &[u8] = include_bytes!("../backend.zip");
const BACKEND_ID: &str = env!("BACKEND_ID");
#[cfg(windows)]
const NO_WINDOW: u32 = 0x0800_0000;

static QUITTING: AtomicBool = AtomicBool::new(false);
static BACKEND_CHILD: Mutex<Option<Child>> = Mutex::new(None);
static START_ERROR: Mutex<Option<String>> = Mutex::new(None);
// the shell holds the bot's local session, so the page never deals with one-time links
static SESSION: Mutex<Option<String>> = Mutex::new(None);
// a key from a report-bot-app link, used on the page's next call to the bot
static PENDING: Mutex<Option<(String, Option<String>)>> = Mutex::new(None);

fn data_dir(app: &AppHandle) -> PathBuf {
    // the same folder the bot program keeps its settings in
    match std::env::var_os("REPORT_BOT_DATA") {
        Some(dir) => PathBuf::from(dir),
        None => app.path().config_dir().expect("no config folder").join("report-bot-app"),
    }
}

fn connected(app: &AppHandle) -> bool {
    fs::read_to_string(data_dir(app).join(".env")).map_or(false, |env| env.lines().any(|l| l.starts_with("LOCAL_TEAM=")))
}

fn unpack(app: &AppHandle) -> std::io::Result<PathBuf> {
    // done once per version, later launches start straight from disk
    let dir = data_dir(app).join("program").join(BACKEND_ID);
    if dir.join("ready").is_file() {
        return Ok(dir);
    }
    let parent = dir.parent().unwrap().to_path_buf();
    if parent.is_dir() {
        for old in fs::read_dir(&parent)?.flatten() {
            let _ = fs::remove_dir_all(old.path());
        }
    }
    let mut archive = zip::ZipArchive::new(Cursor::new(BACKEND)).map_err(std::io::Error::other)?;
    archive.extract(&dir).map_err(std::io::Error::other)?;
    fs::write(dir.join("ready"), BACKEND_ID)?;
    Ok(dir)
}

fn start_backend(app: &AppHandle) -> std::io::Result<()> {
    let dir = unpack(app)?;
    let exe = dir.join(if cfg!(windows) { "report-bot-app.exe" } else { "report-bot-app" });
    let log = fs::OpenOptions::new().create(true).append(true).open(data_dir(app).join("app.log"))?;
    let mut command = Command::new(exe);
    command.arg("--desktop").stdin(Stdio::null()).stdout(log.try_clone()?).stderr(log);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(NO_WINDOW);
    }
    *BACKEND_CHILD.lock().unwrap() = Some(command.spawn()?);
    Ok(())
}

fn run_backend(app: AppHandle) {
    // unpacked and started in the background, and started again if it ever stops on its own
    thread::spawn(move || loop {
        if QUITTING.load(Ordering::SeqCst) {
            return;
        }
        let exited = BACKEND_CHILD.lock().unwrap().as_mut().map_or(true, |c| matches!(c.try_wait(), Ok(Some(_))));
        if exited {
            *START_ERROR.lock().unwrap() = start_backend(&app).err().map(|e| format!("the report bot could not start: {e}"));
        }
        thread::sleep(Duration::from_secs(2));
    });
}

fn stop_backend() {
    if let Some(mut child) = BACKEND_CHILD.lock().unwrap().take() {
        let pid = child.id().to_string();
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            // takes the team's bot with it, windows does not pass a stop signal down to children
            let _ = Command::new("taskkill").args(["/T", "/F", "/PID", &pid]).creation_flags(NO_WINDOW).status();
        }
        #[cfg(not(windows))]
        let _ = Command::new("kill").args(["-TERM", &pid]).status();
        let _ = child.wait();
    }
}

fn wait_for_backend() -> Result<(), String> {
    for _ in 0..480 {
        if ureq::get(&format!("{LOCAL}/setup/static/style.css")).timeout(Duration::from_secs(1)).call().is_ok() {
            return Ok(());
        }
        if let Some(error) = START_ERROR.lock().unwrap().clone() {
            return Err(error);
        }
        thread::sleep(Duration::from_millis(250));
    }
    Err("the report bot did not start, see app.log in its folder".into())
}

fn agent() -> &'static ureq::Agent {
    // redirects are not followed, the one-time link's redirect carries the session cookie
    static AGENT: OnceLock<ureq::Agent> = OnceLock::new();
    AGENT.get_or_init(|| ureq::AgentBuilder::new().redirects(0).build())
}

fn bot(method: &str, path: &str, content_type: Option<&str>, body: &[u8]) -> Result<(u16, String, Vec<u8>), String> {
    let mut request = agent().request(method, &format!("{LOCAL}{path}")).timeout(Duration::from_secs(120));
    if let Some(kind) = content_type {
        request = request.set("Content-Type", kind);
    }
    if let Some(session) = SESSION.lock().unwrap().clone() {
        request = request.set("Cookie", &format!("setup_session={session}"));
    }
    let sent = if body.is_empty() && method == "GET" { request.call() } else { request.send_bytes(body) };
    let response = match sent {
        Ok(response) | Err(ureq::Error::Status(_, response)) => response,
        Err(e) => return Err(e.to_string()),
    };
    if let Some(cookie) = response.header("Set-Cookie") {
        if let Some(value) = cookie.split(';').next().and_then(|c| c.strip_prefix("setup_session=")) {
            *SESSION.lock().unwrap() = Some(value.to_string());
        }
    }
    let status = response.status();
    let kind = response.content_type().to_string();
    let mut data = Vec::new();
    response.into_reader().read_to_end(&mut data).map_err(|e| e.to_string())?;
    Ok((status, kind, data))
}

fn connect(key: Option<(String, Option<String>)>) -> Result<(), String> {
    // a link's key connects the app, then a fresh one-time link becomes the shell's session
    let body = match key {
        Some((key, server)) => serde_json::json!({ "key": key, "server": server }),
        None => serde_json::json!({}),
    };
    let found = bot("POST", "/setup/api/connect", Some("application/json"), body.to_string().as_bytes())?;
    let reply: serde_json::Value = serde_json::from_slice(&found.2).unwrap_or_default();
    if found.0 != 200 {
        return Err(reply["error"].as_str().unwrap_or("could not connect").to_string());
    }
    let open = reply["open"].as_str().unwrap_or_default();
    let path = open.strip_prefix(LOCAL).unwrap_or(open);
    if path == "/setup/connect" {
        return Err("connect the app from your team's setup page first".into());
    }
    bot("GET", path, None, &[])?;
    Ok(())
}

fn reply(status: u16, kind: &str, body: Vec<u8>) -> Response<Vec<u8>> {
    Response::builder().status(status).header("Content-Type", kind).body(body).unwrap()
}

fn error_reply(status: u16, error: &str) -> Response<Vec<u8>> {
    reply(status, "application/json", serde_json::json!({ "error": error }).to_string().into_bytes())
}

fn serve(app: &AppHandle, request: Request<Vec<u8>>) -> Response<Vec<u8>> {
    let path = request.uri().path().to_string();
    let file = match path.as_str() {
        "/setup" | "/setup/" => Some("index.html".to_string()),
        "/setup/connect" => Some("connect.html".to_string()),
        _ => path.strip_prefix("/setup/static/").map(str::to_string),
    };
    if let Some(name) = file {
        // the page itself is built into the app, so it shows before the bot has started
        return match app.asset_resolver().get(name) {
            Some(asset) => reply(200, &asset.mime_type, asset.bytes),
            None => error_reply(404, "not found"),
        };
    }
    if !path.starts_with("/setup/api/") {
        return error_reply(404, "not found");
    }
    if let Err(error) = wait_for_backend() {
        return error_reply(503, &error);
    }
    let body = request.body().clone();
    if path == "/setup/api/connect" {
        let key = serde_json::from_slice::<serde_json::Value>(&body).ok().and_then(|b| b["key"].as_str().map(|k| (k.to_string(), None)));
        return match connect(key) {
            Ok(()) => reply(200, "application/json", br#"{"open": "/setup/"}"#.to_vec()),
            Err(error) => error_reply(400, &error),
        };
    }
    let pending = PENDING.lock().unwrap().take();
    if pending.is_some() || SESSION.lock().unwrap().is_none() {
        if let Err(error) = connect(pending) {
            return error_reply(400, &error);
        }
    }
    let target = match request.uri().query() {
        Some(query) => format!("{path}?{query}"),
        None => path,
    };
    let kind = request.headers().get("Content-Type").and_then(|v| v.to_str().ok()).map(str::to_string);
    match bot(request.method().as_str(), &target, kind.as_deref(), &body) {
        Ok((status, kind, data)) => reply(status, &kind, data),
        Err(error) => error_reply(502, &error),
    }
}

fn window_open(open: bool) {
    thread::spawn(move || {
        if wait_for_backend().is_ok() {
            let body = serde_json::json!({ "open": open }).to_string();
            let _ = bot("POST", "/setup/api/window", Some("application/json"), body.as_bytes());
        }
    });
}

fn page_url(app: &AppHandle) -> Url {
    let page = if PENDING.lock().unwrap().is_some() { "setup/#configure" } else if connected(app) { "setup/" } else { "setup/connect" };
    Url::parse(&format!("bot://localhost/{page}")).unwrap()
}

fn allowed(url: &Url) -> bool {
    url.scheme() == "bot" || url.host_str() == Some("bot.localhost")
}

fn main_window(app: &AppHandle) -> WebviewWindow {
    if let Some(window) = app.get_webview_window("main") {
        return window;
    }
    let outside = app.clone();
    let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::CustomProtocol(page_url(app)))
        .title("HAAG Bot")
        .inner_size(1000.0, 800.0)
        .on_navigation(move |url| {
            // only the app's own pages load in the window, anything else goes to the system browser
            if allowed(url) {
                return true;
            }
            if matches!(url.scheme(), "http" | "https") {
                let _ = outside.opener().open_url(url.as_str(), None::<&str>);
            }
            false
        })
        .build()
        .expect("could not open the window");
    let hidden = window.clone();
    window.on_window_event(move |event| {
        if let WindowEvent::CloseRequested { api, .. } = event {
            // closing only hides it, the bot keeps working from the tray
            api.prevent_close();
            let _ = hidden.hide();
            window_open(false);
        }
    });
    window_open(true);
    window
}

fn open_settings(app: &AppHandle, link: Option<String>) {
    let key = link.as_deref().and_then(|l| Url::parse(l).ok()).and_then(|u| {
        let get = |name: &str| u.query_pairs().find(|(k, _)| k == name).map(|(_, v)| v.to_string());
        get("key").map(|key| (key, get("server")))
    });
    let fresh = key.is_some();
    if fresh {
        *PENDING.lock().unwrap() = key;
        *SESSION.lock().unwrap() = None;
    }
    let existed = app.get_webview_window("main").is_some();
    let window = main_window(app);
    if existed && fresh {
        // the shell connects with the new key itself, then the open page reloads to show the team
        let page = window.clone();
        thread::spawn(move || {
            if wait_for_backend().is_ok() {
                let key = PENDING.lock().unwrap().take();
                if key.is_some() && connect(key).is_ok() {
                    // connecting from the website is for configuring, so the page opens on its settings
                    let _ = page.eval("sessionStorage.setItem('view', 'settings'); location.replace('/setup/')");
                }
            }
        });
    }
    let _ = window.show();
    let _ = window.unminimize();
    let _ = window.set_focus();
    if existed {
        window_open(true);
    }
}

fn quit(app: &AppHandle) {
    QUITTING.store(true, Ordering::SeqCst);
    if wait_for_backend().is_ok() {
        let _ = bot("POST", "/setup/api/window", Some("application/json"), br#"{"open": false}"#);
    }
    stop_backend();
    app.exit(0);
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, argv, _| {
            // links reach the running app through the deep link plugin, a plain second launch only opens the window
            if !argv.iter().any(|a| a.starts_with(SCHEME)) {
                open_settings(app, None);
            }
        }))
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_opener::init())
        .register_asynchronous_uri_scheme_protocol("bot", |ctx, request, responder| {
            let app = ctx.app_handle().clone();
            thread::spawn(move || responder.respond(serve(&app, request)));
        })
        .setup(|app| {
            let handle = app.handle().clone();
            let first_link = app.deep_link().get_current()?.and_then(|urls| urls.first().map(|u| u.to_string()));
            // the window comes first, everything else happens behind it
            open_settings(&handle, first_link);
            run_backend(handle.clone());
            #[cfg(any(windows, target_os = "linux"))]
            app.deep_link().register(SCHEME)?;
            let on_link = handle.clone();
            app.deep_link().on_open_url(move |event| {
                open_settings(&on_link, event.urls().first().map(|u| u.to_string()));
            });
            let open = MenuItem::with_id(app, "open", "Open settings", true, None::<&str>)?;
            let leave = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("HAAG Bot")
                .menu(&Menu::with_items(app, &[&open, &leave])?)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => open_settings(app, None),
                    "quit" => quit(app),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                        open_settings(tray.app_handle(), None);
                    }
                })
                .build(app)?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("could not start the app")
        .run(|_, event| {
            if let RunEvent::ExitRequested { api, code, .. } = event {
                // the window hiding is not a reason to stop, only quit from the tray is
                if code.is_none() && !QUITTING.load(Ordering::SeqCst) {
                    api.prevent_exit();
                }
            }
        });
}
