use sha2::{Digest, Sha256};

fn main() {
    // the bot program is unpacked into a folder named after its contents, so a new build never reuses an old one
    println!("cargo:rerun-if-changed=backend.zip");
    let packed = std::fs::read("backend.zip").expect("backend.zip is missing, build the bot program first");
    let id: String = Sha256::digest(&packed).iter().take(8).map(|b| format!("{b:02x}")).collect();
    println!("cargo:rustc-env=BACKEND_ID={id}");
    tauri_build::build()
}
