"""About / Contact / Privacy / Terms pages (needed by affiliate networks) exist, are linked and stay accurate."""
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ["about.html", "contact.html", "privacy.html", "terms.html"]
EMAIL = "Admin@bestdealhunters.com"


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs, self.srcs = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("href"):
            self.hrefs.append(a["href"])
        if a.get("src"):
            self.srcs.append(a["src"])


def parse(name):
    p = Links()
    p.feed((ROOT / name).read_text(encoding="utf-8"))
    return p


class SitePagesTests(unittest.TestCase):
    def test_every_page_links_all_four_and_its_local_links_exist(self):
        for name in ["index.html", "upcoming.html", *PAGES]:
            with self.subTest(name):
                hrefs = parse(name).hrefs
                for page in PAGES:
                    self.assertIn(page, hrefs)
                for h in hrefs:
                    if not re.match(r"^(https?:|mailto:|#)", h):
                        target = h.split("#")[0] or "index.html"
                        target = "index.html" if target in ("./", "/") else target
                        self.assertTrue((ROOT / target).exists(), h)

    def test_pages_are_mobile_ready_and_titled(self):
        for name in PAGES:
            with self.subTest(name):
                text = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn('name="viewport" content="width=device-width,initial-scale=1"', text)
                self.assertRegex(text, r"<title>[^<]+ \| BestDealHunter</title>")
                self.assertIn('href="assets/pages.css"', text)
                self.assertNotIn("<script", text)

    def test_required_content(self):
        about = (ROOT / "about.html").read_text(encoding="utf-8")
        terms = (ROOT / "terms.html").read_text(encoding="utf-8")
        privacy = (ROOT / "privacy.html").read_text(encoding="utf-8")
        contact = (ROOT / "contact.html").read_text(encoding="utf-8")
        for text in (about, terms):
            self.assertIn("Prices and availability", text.replace("prices and availability", "Prices and availability"))
            self.assertIn("affiliate", text.lower())
        self.assertIn("State of California", terms)
        self.assertIn(f'href="mailto:{EMAIL}"', contact)
        for text in (privacy, terms):
            self.assertIn(f"mailto:{EMAIL}", text)

    def test_privacy_policy_names_every_third_party_the_site_loads(self):
        """If the site starts loading a new service (analytics, fonts, images), the policy must be updated first."""
        privacy = (ROOT / "privacy.html").read_text(encoding="utf-8")
        hosts = set()
        for name in ["index.html", "upcoming.html", *PAGES]:
            text = (ROOT / name).read_text(encoding="utf-8")
            loaded = re.findall(r'(?:src|href)="(https://[^"/]+)', text)
            links = {h for h in re.findall(r'<a [^>]*href="(https://[^"/]+)', text)}
            css = set(re.findall(r'<link[^>]*rel="(?:preconnect|stylesheet)"[^>]*href="(https://[^"/]+)', text))
            hosts |= (set(loaded) - links) | css
        hosts |= set(re.findall(r'"(https://res\.cloudinary\.com)/', (ROOT / "index.html").read_text()))
        hosts.discard("https://bestdealhunters.com")
        known = {"https://fonts.googleapis.com": "fonts.googleapis.com", "https://fonts.gstatic.com": "fonts.gstatic.com",
                 "https://res.cloudinary.com": "res.cloudinary.com"}
        for host in hosts:
            with self.subTest(host):
                self.assertIn(host, known, f"{host} is loaded by the site but not covered by privacy.html")
                self.assertIn(known[host], privacy)
        for text in ("index.html", *PAGES):
            src = (ROOT / text).read_text(encoding="utf-8")
            for tracker in ("googletagmanager", "google-analytics", "gtag(", "fbq(", "document.cookie"):
                self.assertNotIn(tracker, src, f"{text} uses {tracker}; update privacy.html")


if __name__ == "__main__":
    unittest.main()
