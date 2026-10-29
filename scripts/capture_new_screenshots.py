import os
import sys
import time
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from PIL import Image, ImageEnhance
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(WORKSPACE_DIR, "frontend")
OUTPUT_DIR = os.path.join(WORKSPACE_DIR, "docs", "screenshots")
os.makedirs(OUTPUT_DIR, exist_ok=True)

class QuietHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)
    def log_message(self, format, *args):
        pass

def start_server(port=8089):
    server = ThreadingHTTPServer(('127.0.0.1', port), QuietHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server

def remove_blue_borders_and_polish(image_path: str, output_path: str):
    img = Image.open(image_path).convert("RGB")
    width, height = img.size
    pixels = img.load()
    
    def is_blue_pixel(r, g, b):
        return (b > 120 and b > r * 1.2 and b > g * 1.1) or (b > 160 and r < 100)
    
    crop_left = 6
    crop_top = 6
    crop_right = width - 6
    crop_bottom = height - 6
    
    for x in range(min(25, width)):
        if any(is_blue_pixel(*pixels[x, y]) for y in range(0, height, 4)):
            crop_left = max(crop_left, x + 3)
            
    for x in range(width - 1, max(width - 25, 0), -1):
        if any(is_blue_pixel(*pixels[x, y]) for y in range(0, height, 4)):
            crop_right = min(crop_right, x - 3)
            
    for y in range(min(25, height)):
        if any(is_blue_pixel(*pixels[x, y]) for x in range(0, width, 4)):
            crop_top = max(crop_top, y + 3)
            
    for y in range(height - 1, max(height - 25, 0), -1):
        if any(is_blue_pixel(*pixels[x, y]) for x in range(0, width, 4)):
            crop_bottom = min(crop_bottom, y - 3)

    if crop_right > crop_left + 100 and crop_bottom > crop_top + 100:
        img = img.crop((crop_left, crop_top, crop_right, crop_bottom))
    
    bright_enhancer = ImageEnhance.Brightness(img)
    img = bright_enhancer.enhance(1.03)
    contrast_enhancer = ImageEnhance.Contrast(img)
    img = contrast_enhancer.enhance(1.05)
    
    img.save(output_path, "PNG", quality=95, optimize=True)
    print(f"[OK] Polished: {os.path.basename(output_path)} ({img.width}x{img.height})")

def main():
    port = 8089
    server = start_server(port)
    base_url = f"http://127.0.0.1:{port}"
    print(f"Server started at {base_url}")

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1440,940")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--force-device-scale-factor=1")
    chrome_options.add_argument("--hide-scrollbars")

    driver = webdriver.Chrome(options=chrome_options)
    temp_raw = os.path.join(OUTPUT_DIR, "temp_target_raw.png")

    try:
        print("Loading page...")
        driver.get(base_url)
        time.sleep(2.5)

        driver.execute_script("""
            const style = document.createElement('style');
            style.innerHTML = `
                *:focus { outline: none !important; box-shadow: none !important; }
                ::-webkit-scrollbar { display: none !important; }
            `;
            document.head.appendChild(style);
        """)

        # -------------------------------------------------------------
        # 1. Capture 09_deepfake_scan_result.png (Forensic Verdict Card)
        # -------------------------------------------------------------
        print("Capturing 09_deepfake_scan_result.png...")
        driver.execute_script("""
            if (window.openWorkspaceModal) {
                window.openWorkspaceModal('deepfakeModal');
            } else {
                const m = document.getElementById('deepfakeModal');
                if (m) m.style.display = 'flex';
            }
        """)
        time.sleep(1)

        # Stage file and trigger scan
        driver.execute_script("""
            const sampleBtn = document.getElementById('btn-deepfake-demo-sample');
            if (sampleBtn) sampleBtn.click();
        """)
        time.sleep(1)

        driver.execute_script("""
            const scanBtn = document.getElementById('btn-scan-deepfake');
            if (scanBtn) scanBtn.click();
        """)
        time.sleep(1.5)

        # Scroll to verdict card for crisp framing
        driver.execute_script("""
            const vc = document.getElementById('deepfake-verdict-card');
            if (vc) vc.scrollIntoView({ behavior: 'instant', block: 'center' });
        """)
        time.sleep(0.5)

        driver.save_screenshot(temp_raw)
        remove_blue_borders_and_polish(temp_raw, os.path.join(OUTPUT_DIR, "09_deepfake_scan_result.png"))

        # Close deepfake modal
        driver.execute_script("""
            if (window.closeWorkspaceModal) {
                window.closeWorkspaceModal('deepfakeModal');
            } else {
                const m = document.getElementById('deepfakeModal');
                if (m) m.style.display = 'none';
            }
        """)
        time.sleep(1)

        # -------------------------------------------------------------
        # 2. Capture 19_fake_escape_call_simulator.png (Fake Escape Call Screen)
        # -------------------------------------------------------------
        print("Capturing 19_fake_escape_call_simulator.png...")
        # Open Fake Call Modal directly
        driver.execute_script("""
            const fakeModal = document.getElementById('fake-call-modal');
            if (fakeModal) {
                fakeModal.classList.add('active');
                fakeModal.style.display = 'flex';
                fakeModal.style.alignItems = 'center';
                fakeModal.style.justifyContent = 'center';
            }
            const status = document.getElementById('call-status');
            if (status) {
                status.textContent = 'Mobile • Incoming Ringtone...';
            }
        """)
        time.sleep(1)

        driver.save_screenshot(temp_raw)
        remove_blue_borders_and_polish(temp_raw, os.path.join(OUTPUT_DIR, "19_fake_escape_call_simulator.png"))

        # Close fake call modal
        driver.execute_script("""
            const fakeModal = document.getElementById('fake-call-modal');
            if (fakeModal) {
                fakeModal.classList.remove('active');
                fakeModal.style.display = 'none';
            }
        """)
        time.sleep(0.5)

        # -------------------------------------------------------------
        # 3. Capture 20_emergency_sos_escape_option.png (SOS modal showing trigger button)
        # -------------------------------------------------------------
        print("Capturing 20_emergency_sos_escape_option.png...")
        driver.execute_script("""
            if (window.openWorkspaceModal) {
                window.openWorkspaceModal('sosModal');
            } else {
                const m = document.getElementById('sosModal');
                if (m) m.style.display = 'flex';
            }
        """)
        time.sleep(1)
        driver.save_screenshot(temp_raw)
        remove_blue_borders_and_polish(temp_raw, os.path.join(OUTPUT_DIR, "20_emergency_sos_escape_option.png"))

        print("[SUCCESS] All requested screenshots generated and polished.")

    finally:
        if os.path.exists(temp_raw):
            try:
                os.remove(temp_raw)
            except Exception:
                pass
        driver.quit()
        server.shutdown()

if __name__ == "__main__":
    main()
