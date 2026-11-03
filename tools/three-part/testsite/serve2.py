#!/usr/bin/env python3
"""Range-capable static server for the publish2 (range transport) test."""
import http.server, os, re, sys

ROOT = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def send_head(self):
        rng = self.headers.get("Range")
        if not rng:
            return super().send_head()
        m = re.match(r"bytes=(\d+)-(\d*)$", rng.strip())
        if not m:
            return super().send_head()
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404); return None
        size = os.path.getsize(path)
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end:
            self.send_error(416); return None
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self._range = (f, end - start + 1)
        return f

    def copyfile(self, source, outputfile):
        rng = getattr(self, "_range", None)
        if rng and source is rng[0]:
            remaining = rng[1]
            while remaining > 0:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk: break
                outputfile.write(chunk)
                remaining -= len(chunk)
            self._range = None
        else:
            super().copyfile(source, outputfile)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", 8902), Handler).serve_forever()
