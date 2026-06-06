import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"Trading Bot Server is Online!")

    def log_message(self, format, *args):
        return  # This stops Render from spamming your logs with connection pings

def run_server():
    # Render automatically passes an active port through environment variables
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), DummyServer)
    server.serve_forever()

def start_keep_alive():
    # Runs the web server in a background thread so your bot can loop freely
    t = threading.Thread(target=run_server, daemon=True)
    t.start()