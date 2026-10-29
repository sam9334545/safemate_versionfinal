import os
import sys
import time
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from PIL import Image, ImageEnhance
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

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
    chrome_options.add_argument("--window-size=1440,1050")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--force-device-scale-factor=1")
    chrome_options.add_argument("--hide-scrollbars")

    driver = webdriver.Chrome(options=chrome_options)
    temp_raw = os.path.join(OUTPUT_DIR, "temp_target_raw2.png")

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
        # 1. Capture 21_chatbot_zero_fir_generator.png
        # -------------------------------------------------------------
        print("Capturing 21_chatbot_zero_fir_generator.png...")
        driver.execute_script("""
            const modal = document.getElementById('complaint-modal');
            if (modal) {
                modal.classList.add('active');
                modal.style.display = 'flex';
                modal.style.alignItems = 'center';
                modal.style.justifyContent = 'center';
            }

            const itype = document.getElementById('fir-incident-type');
            if (itype) itype.value = 'cyber_blackmail';

            const name = document.getElementById('fir-complainant-name');
            if (name) name.value = 'Concerned Citizen (Confidential Survivor)';

            const platform = document.getElementById('fir-platform');
            if (platform) platform.value = 'Instagram & WhatsApp Direct Messages';

            const city = document.getElementById('fir-city-state');
            if (city) city.value = 'New Delhi, India';

            const accused = document.getElementById('fir-accused');
            if (accused) accused.value = '@blackmailer_handle (+91-98765XXXXX)';

            const summary = document.getElementById('fir-summary');
            if (summary) summary.value = 'Suspect synthesized non-consensual morphed intimate media of complainant and is threatening public disclosure across social channels unless extortion demands are fulfilled.';

            const outContainer = document.getElementById('fir-output-container');
            const outLetter = document.getElementById('fir-output-letter');
            const outInstructions = document.getElementById('fir-instructions-list');

            if (outContainer && outLetter) {
                outContainer.style.display = 'block';
                outLetter.textContent = `TO THE STATION HOUSE OFFICER (SHO),
ANY POLICE STATION UNDER JURISDICTION OF ZERO FIR
PURSUANT TO SECTION 173 BHARATIYA NAGARIK SURAKSHA SANHITA (BNSS), 2023 / SEC 154 CrPC

SUBJECT: MANDATORY REGISTRATION OF ZERO FIR FOR CYBER EXTORTION & MORPHED IMAGE ABUSE

Respected Sir/Madam,

I am lodging this formal complaint regarding cognizable criminal offenses committed against me via electronic communications. In accordance with the binding ruling of the Hon'ble Supreme Court of India in Lalita Kumari v. Govt. of U.P. [(2014) 2 SCC 1] and Section 173(1) BNSS (2023), registration of an FIR is mandatory upon receiving information disclosing a cognizable offense, regardless of territorial jurisdiction.

1. INCIDENT PARTICULARS:
   - Medium / Channels: Instagram & WhatsApp Direct Messages
   - Suspect Handle / Contact: @blackmailer_handle (+91-98765XXXXX)
   - Summary: Perpetrator weaponized non-consensual morphed intimate media and demanded monetary extortion under threat of mass circulation.

2. STATUTORY PENAL PROVISIONS CITED:
   - IT Act, 2000 — Section 66E: Violation of bodily privacy by publishing/transmitting private images
   - IT Act, 2000 — Section 67A: Publishing sexually explicit materials electronically (non-bailable)
   - Bharatiya Nyaya Sanhita (BNS), 2023 — Section 308: Extortion and blackmail
   - Bharatiya Nyaya Sanhita (BNS), 2023 — Section 351: Criminal intimidation
   - BNSS, 2023 — Section 173: Mandatory registration of Zero FIR across any police station in India

3. PRESERVATION OF DIGITAL EVIDENCE (SECTION 94 BNSS):
   Law enforcement is respectfully requested to immediately serve Section 94 BNSS preservation notices to Meta Platforms, Inc. and telecommunications service providers for IP logs, MAC addresses, and account registration data.

PRAYER:
Kindly register this complaint as a Zero FIR forthwith, assign a General Diary (GD) number, and transfer the case diary to the jurisdictional cyber police station.

Yours faithfully,
Concerned Citizen (Confidential Survivor)
Dated: October 30, 2026`;

                if (outInstructions) {
                    outInstructions.innerHTML = `
                        <div style="background:#f8fafc; border:1px solid #cbd5e1; border-radius:4px; padding:10px 14px; margin-top:8px;">
                            <strong style="color:#0f172a;">Mandatory Police Station Action Protocol:</strong>
                            <ol style="margin:6px 0 0 18px; padding:0; line-height:1.5;">
                                <li><strong>Universal Station Jurisdiction:</strong> Under Section 173 BNSS 2023, no police officer can refuse to register this Zero FIR on territorial grounds.</li>
                                <li><strong>Immediate GD Entry:</strong> Demand your signed and stamped acknowledgment copy with the General Diary (GD) or Daily Diary (DD) reference number.</li>
                                <li><strong>Preserve Hash Evidence:</strong> Accompany this letter with your SafeMate Section 63 BSA electronic audit certificate and hash ledger.</li>
                                <li><strong>National Cyber Helpline:</strong> Parallelly call <strong>1930</strong> or report on <em>cybercrime.gov.in</em> to freeze suspect accounts.</li>
                            </ol>
                        </div>
                    `;
                }
            }
        """)
        time.sleep(1)

        # Scroll modal card into center view
        driver.execute_script("""
            const card = document.querySelector('.complaint-modal-card');
            if (card) card.scrollIntoView({ behavior: 'instant', block: 'center' });
        """)
        time.sleep(0.5)

        driver.save_screenshot(temp_raw)
        remove_blue_borders_and_polish(temp_raw, os.path.join(OUTPUT_DIR, "21_chatbot_zero_fir_generator.png"))

        # Close complaint modal
        driver.execute_script("""
            const modal = document.getElementById('complaint-modal');
            if (modal) {
                modal.classList.remove('active');
                modal.style.display = 'none';
            }
        """)
        time.sleep(0.5)

        print("[SUCCESS] Zero FIR Chatbot screenshot generated and polished.")

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
