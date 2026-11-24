import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "10000"))
EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
PING_INTERVAL = 10 * 60  # ১০ মিনিট
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
    if not EXTERNAL_URL:
        log.warning("RENDER_EXTERNAL_URL পাওয়া যায়নি, keep-alive ping বন্ধ")
        return
    while True:
        time.sleep(PING_INTERVAL)
        try:
            with urllib.request.urlopen(EXTERNAL_URL, timeout=30) as r:
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
