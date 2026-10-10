"""Safety checks for deal validation; no external services or secrets required."""
import json
import re
import unittest
from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_deals import validate, validate_feed, deal_status, RULES
from refresh_deals import merge

ROOT = Path(__file__).resolve().parents[1]

def sample():
    return {
        "store": "eBay",
        "title": "Sample verified product",
        "category": "Electronics",
        "source": "Authorized retailer data",
        "price_evidence": "Independent, like-condition comparison",
        "url": "https://www.ebay.com/itm/example",
        "price": 50,
        "original_price": 100,
        "verified_date": date.today().isoformat(),
    }

class DealValidationTests(unittest.TestCase):
    def test_exactly_fifty_percent(self):
        self.assertEqual(validate([sample()]), 1)

    def test_below_fifty_percent(self):
        d = sample()
        d["price"] = 50.01
        with self.assertRaises(ValueError):
            validate([d])

    def test_missing_evidence(self):
        d = sample()
        d["price_evidence"] = ""
        with self.assertRaises(ValueError):
            validate([d])

    def test_stale_verification(self):
        d = sample()
        d["verified_date"] = (date.today() - timedelta(days=3)).isoformat()
        with self.assertRaises(ValueError):
            validate([d])

    def test_insecure_url(self):
        d = sample()
        d["url"] = "http://example.com"
        with self.assertRaises(ValueError):
            validate([d])

    def test_invalid_store(self):
        d = sample()
        d["store"] = "Unverified Marketplace"
        with self.assertRaises(ValueError):
            validate([d])

    def test_empty_feed(self):
        self.assertEqual(validate([]), 0)

    def test_unknown_category(self):
        d = sample()
        d["category"] = "Home & Kitchen"
        with self.assertRaises(ValueError):
            validate([d])

    def test_macys_accepted(self):
        d = sample()
        d["store"], d["url"] = "Macy's", "https://www.macys.com/shop/product/x?ID=1"
        self.assertEqual(validate([d]), 1)

    def affiliate(self, **changes):
        d = sample()
        d.update({"price": 80, "section": "affiliate", "affiliate_disclosure": "We may earn a commission."})
        d.update(changes)
        return d

    def test_affiliate_section_allows_twenty_percent(self):
        self.assertEqual(validate([self.affiliate()]), 1)
        self.assertEqual(validate([self.affiliate(section=None, affiliate_section=True)]), 1)

    def test_affiliate_section_rejects_token_discounts(self):
        for price in (100, 99, 80.01):
            with self.assertRaises(ValueError):
                validate([self.affiliate(price=price)])

    def test_affiliate_section_requires_disclosure(self):
        for disclosure in (None, "", "   "):
            with self.assertRaises(ValueError):
                validate([self.affiliate(affiliate_disclosure=disclosure)])

    def test_affiliate_section_keeps_evidence_and_freshness_rules(self):
        stale = (date.today() - timedelta(days=3)).isoformat()
        for change in ({"price_evidence": ""}, {"source": ""}, {"verified_date": stale}, {"store": "Unknown"}):
            with self.assertRaises(ValueError):
                validate([self.affiliate(**change)])

    def test_affiliate_flag_must_be_exact(self):
        # Only section == "affiliate" or affiliate_section is True relax the 50% rule.
        for change in ({"section": "Affiliate"}, {"section": None, "affiliate_section": "true"}):
            with self.assertRaises(ValueError):
                validate([self.affiliate(**change)])

    def test_unknown_domain_needs_disclosure(self):
        d = sample()
        d["url"] = "https://deals.example.net/item"
        with self.assertRaises(ValueError):
            validate([d])
        d["affiliate_disclosure"] = "We may earn a commission."
        self.assertEqual(validate([d]), 1)

    def test_duplicate_url(self):
        a, b = sample(), sample()
        b["url"] = "https://ebay.com/itm/EXAMPLE"
        with self.assertRaises(ValueError):
            validate([a, b])

    def test_distinct_macys_ids_are_not_duplicates(self):
        a, b = sample(), sample()
        a["store"] = b["store"] = "Macy's"
        a["url"] = "https://www.macys.com/shop/product/x?ID=1"
        b["url"] = "https://www.macys.com/shop/product/x?ID=2"
        self.assertEqual(validate([a, b]), 2)

    def test_promo_code_requires_note(self):
        d = sample()
        d["promo_code"] = "SAVE"
        with self.assertRaises(ValueError):
            validate([d])

    def test_image_path(self):
        d = sample()
        d["image"] = "images/../secrets.png"
        with self.assertRaises(ValueError):
            validate([d])
        d["image"] = "images/generated/card.jpg"
        self.assertEqual(validate([d]), 1)

    def test_future_verification_date(self):
        d = sample()
        d["verified_date"] = (date.today() + timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError):
            validate([d])


class ArchiveTests(unittest.TestCase):
    """Archive policy (#1): expired deals stay published as Past Deals; never deleted or refreshed."""

    def stale(self, **changes):
        d = sample()
        d["verified_date"] = (date.today() - timedelta(days=5)).isoformat()
        d.update(changes)
        return d

    def test_status_rules(self):
        today = date(2026, 10, 12)
        self.assertEqual(deal_status({"verified_date": "2026-10-10"}, today), "active")
        self.assertEqual(deal_status({"verified_date": "2026-10-09"}, today), "archived")
        self.assertEqual(deal_status({"verified_date": "2026-10-12", "expires_date": "2026-10-11"}, today), "archived")
        self.assertEqual(deal_status({"verified_date": "2026-10-12", "status": "archived"}, today), "archived")
        self.assertEqual(deal_status({}, today), "archived")

    def test_feed_keeps_archived_deals(self):
        fresh = sample()
        old = self.stale(url="https://www.ebay.com/itm/old")
        self.assertEqual(validate_feed([fresh, old]), (1, 1))
        with self.assertRaises(ValueError):
            validate([fresh, old])  # strict check (approvals, cards, posts) still rejects it

    def test_archived_deals_still_need_structure_and_evidence(self):
        for change in ({"price_evidence": ""}, {"price": 99}, {"store": "Unknown"}, {"url": "http://x.com"},
                       {"category": "Gadgets"}, {"verified_date": "not-a-date"}, {"expires_date": "soon"},
                       {"status": "hidden"}):
            with self.assertRaises(ValueError):
                validate_feed([self.stale(**change)])

    def test_future_verification_never_allowed(self):
        with self.assertRaises(ValueError):
            validate_feed([dict(sample(), verified_date=(date.today() + timedelta(days=1)).isoformat())])

    def test_live_feed_passes_after_it_goes_stale(self):
        deals = json.loads((ROOT / "deals.json").read_text(encoding="utf-8"))
        later = date.today() + timedelta(days=30)
        if all(date.fromisoformat(d["verified_date"]) <= later for d in deals):
            active, archived = validate_feed(deals, today=later)
            self.assertEqual((active, archived), (0, len(deals)))


class ConsistencyTests(unittest.TestCase):
    def test_site_freshness_matches_rules(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn(f"const maxVerificationAgeDays={RULES['max_verification_age_days']};", html)

    def test_site_filters_match_rules(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        stores = json.loads(re.search(r"const allowedStores=(\[[^\]]*\])", html).group(1))
        categories = json.loads(re.search(r"const categories=(\[[^\]]*\])", html).group(1))
        self.assertEqual(stores, RULES["stores"])
        self.assertEqual(categories, ["All"] + RULES["categories"])

    def test_live_feed_valid_on_its_verification_date(self):
        deals = json.loads((ROOT / "deals.json").read_text(encoding="utf-8"))
        if deals:
            newest = max(date.fromisoformat(d["verified_date"]) for d in deals)
            self.assertEqual(validate(deals, today=newest), len(deals))

    def test_queued_categories_are_known(self):
        pending = json.loads((ROOT / "pending_deals.json").read_text(encoding="utf-8"))
        for d in pending:
            self.assertIn(d.get("category"), RULES["categories"])


class RefreshTests(unittest.TestCase):
    def test_refresh_accepts_macys(self):
        d = sample()
        d["store"], d["url"] = "Macy's", "https://www.macys.com/shop/product/x?ID=1"
        merged, updates = merge([], [d])
        self.assertEqual((len(merged), updates), (1, 1))

    def test_refresh_rejects_invalid_and_keeps_existing(self):
        existing = [sample()]
        bad = sample()
        bad["url"], bad["category"] = "https://www.ebay.com/itm/other", "Gadgets"
        merged, updates = merge(existing, [bad])
        self.assertEqual((merged, updates), (existing, 0))

    def test_refresh_keeps_existing_deals_that_became_stale(self):
        # Known policy gap (#1): refresh never removes published deals, even stale ones.
        # The hourly validator and the maintenance monitor flag them instead.
        old = sample()
        old["verified_date"] = (date.today() - timedelta(days=5)).isoformat()
        fresh = sample()
        fresh["url"] = "https://www.ebay.com/itm/other"
        merged, updates = merge([old], [fresh])
        self.assertEqual((len(merged), updates), (2, 1))
        self.assertEqual(merged[0]["verified_date"], old["verified_date"])
        with self.assertRaises(ValueError):
            validate(merged)

    def test_refresh_ignores_stale_update_for_existing_deal(self):
        old = sample()
        old["verified_date"] = (date.today() - timedelta(days=5)).isoformat()
        stale_update = dict(old, price=40)
        merged, updates = merge([old], [stale_update])
        self.assertEqual((merged, updates), ([old], 0))

    def test_refresh_updates_matching_url_and_keeps_image(self):
        old = sample()
        old["image"] = "images/generated/card.jpg"
        new = sample()
        new["price"] = 40
        merged, updates = merge([old], [new])
        self.assertEqual((merged[0]["price"], merged[0]["image"], updates), (40, "images/generated/card.jpg", 1))

if __name__ == "__main__":
    unittest.main()
