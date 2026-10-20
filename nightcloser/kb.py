"""Safe page fetch for the Nightcloser knowledge base. fetch_page(url) -> (text, err). err is a short reason code, never the URL.
SSRF rules: http/https only, ports 80/443, every resolved address must be globally routable (no private/loopback/link-local/metadata),
the connection goes to the validated IP (no second DNS lookup), redirects (max 3) are re-validated, 6 s total, 400 KB, text/html only."""
import http.client, ipaddress, re, socket, ssl, time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

MAX_BYTES, MAX_CHARS, TIMEOUT, MAX_REDIRECTS = 400_000, 6000, 6.0, 3
URL_IN_TEXT = re.compile(r"https?://[^\s<>\"']+|(?<![\w@./-])www\.[^\s<>\"']+", re.I)
SKIP = {"script", "style", "nav", "noscript", "svg", "template", "iframe", "head"}


def find_url(text):
    m = URL_IN_TEXT.search(text or "")
    if not m:
        return None
    u = m.group().rstrip(".,;:!?)]}")
    return u if u.lower().startswith("http") else "https://" + u


def check_ips(addrs):
    """addrs: iterable of IP strings. Returns them if every one is globally routable, else None."""
    ips = []
    for a in addrs or ():
        ip = ipaddress.ip_address(a.split("%")[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            return None
        ips.append(str(ip))
    return ips or None


def _public_ips(host):
    try:
        return check_ips(i[4][0] for i in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM))
    except (OSError, ValueError):
        return None


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
        elif tag in ("p", "br", "li", "div", "h1", "h2", "h3", "h4", "tr", "section"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, d):
        if not self.skip:
            self.out.append(d)


def html_to_text(html):
    p = _Text()
    try:
        p.feed(html)
    except Exception:
        pass
    t = re.sub(r"[ \t\r\f\v ]+", " ", "".join(p.out))
    t = re.sub(r"\s*\n\s*", "\n", t).strip()
    return t[:MAX_CHARS]


def _get(url, deadline, resolver):
    sp = urlsplit(url)
    if sp.scheme not in ("http", "https") or not sp.hostname or sp.username or sp.password:
        return None, "scheme"
    port = sp.port or (443 if sp.scheme == "https" else 80)
    if port not in (80, 443):
        return None, "port"
    ips = resolver(sp.hostname)
    if not ips:
        return None, "blocked"
    ip = ips[0]
    left = deadline - time.monotonic()
    if left <= 0:
        return None, "timeout"
    if sp.scheme == "https":
        conn = http.client.HTTPSConnection(sp.hostname, port, timeout=left, context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(sp.hostname, port, timeout=left)

    def connect():
        s = socket.create_connection((ip, port), timeout=left)
        conn.sock = conn._context.wrap_socket(s, server_hostname=sp.hostname) if sp.scheme == "https" else s
    conn.connect = connect
    try:
        path = (sp.path or "/") + ("?" + sp.query if sp.query else "")
        conn.request("GET", path, headers={"Host": sp.netloc, "User-Agent": "Mozilla/5.0 (compatible; NightcloserDemo/1.0)",
                                           "Accept": "text/html", "Accept-Encoding": "identity"})
        r = conn.getresponse()
        if r.status in (301, 302, 303, 307, 308):
            return ("redirect", r.getheader("Location") or ""), None
        if r.status != 200:
            return None, "http"
        ctype = (r.getheader("Content-Type") or "").split(";")[0].strip().lower()
        if ctype not in ("text/html", "application/xhtml+xml"):
            return None, "type"
        buf = b""
        while len(buf) < MAX_BYTES:
            if time.monotonic() > deadline:
                return None, "timeout"
            chunk = r.read(min(16384, MAX_BYTES - len(buf)))
            if not chunk:
                break
            buf += chunk
        m = re.search(r"charset=([\w-]+)", r.getheader("Content-Type") or "", re.I)
        try:
            return ("ok", buf.decode(m.group(1) if m else "utf-8", "replace")), None
        except LookupError:
            return ("ok", buf.decode("utf-8", "replace")), None
    except (OSError, http.client.HTTPException, ssl.SSLError):
        return None, "timeout" if time.monotonic() > deadline else "network"
    finally:
        conn.close()


def fetch_page(url, resolver=_public_ips, now=time.monotonic):
    deadline = now() + TIMEOUT
    for _ in range(MAX_REDIRECTS + 1):
        res, err = _get(url, deadline, resolver)
        if err:
            return None, err
        kind, val = res
        if kind == "redirect":
            if not val:
                return None, "http"
            url = urljoin(url, val)
            continue
        text = html_to_text(val)
        return (text, None) if len(text) >= 40 else (None, "empty")
    return None, "redirects"
