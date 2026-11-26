import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# সার্ভারের লাইভ লিঙ্ক (Render)। এটি সরাসরি এখানে বসানো আছে।
SERVER_URL = "https://sp-smm-bot.onrender.com"

PORT = int(os.environ.get("PORT", "10000"))
PING_INTERVAL = 5 * 60  # ৫ মিনিট (Render ১৫ মিনিট নিষ্ক্রিয় থাকলে ঘুমায়, তাই ৫ মিনিটে পিং)
BOT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("runner")


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def start_http():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Health)
    log.info("health server চালু, port %s", PORT)
    server.serve_forever()


def self_ping():
    while True:
        time.sleep(PING_INTERVAL)
        try:
            with urllib.request.urlopen(SERVER_URL, timeout=30) as r:
                log.info("keep-alive ping সফল: %s", r.status)
        except Exception as e:
            log.warning("keep-alive ping ব্যর্থ: %s", e)


def run_bot_forever():
    failures = 0
    while True:
        started = time.time()
        log.info("bot চালু হচ্ছে")
        code = subprocess.call([sys.executable, BOT_FILE])
        uptime = time.time() - started
        failures = 0 if uptime > 300 else failures + 1
        delay = min(60, 5 * failures) if failures else 5
        log.error("bot বন্ধ হয়েছে (exit %s)। %s সেকেন্ড পর আবার চালু হবে", code, delay)
        time.sleep(delay)


if __name__ == "__main__":
    threading.Thread(target=start_http, daemon=True).start()
    threading.Thread(target=self_ping, daemon=True).start()
    run_bot_forever()
