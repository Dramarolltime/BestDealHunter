"""Direct product links: strip tracking, and tell affiliate links apart (owner policy 2026-10-14).

Public deals use ordinary product URLs. Affiliate programs stay available for later, so a link that
does carry affiliate tracking is still recognised and gets the #ad / commission disclosure.
"""
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Query parameters that only track a click or credit an affiliate; never needed to show the product.
TRACKING = re.compile(r"^(utm_.*|tag|linkcode|ascsubtag|ref|ref_|irclickid|irgwc|clickid|cjevent|cjdata|ranmid|raneaid|"
                      r"ransiteid|affid|aff_id|affiliate|affiliate_id|subid|sub_id|gclid|fbclid|msclkid|mc_cid|mc_eid|"
                      r"sharedid|partnerid|camp|creative|creativeasin|linkid|th|psc|smid|sr|qid|sprefix|crid|keywords|"
                      r"wmlspartner|veh|sourceid|lgeo|afsrc|clkid|afid|cpng|adgroup|lnk|pd_rd_.*|pf_rd_.*|content-id|"
                      r"campid|mkcid|mkrid|mkevt|toolid|customid)$", re.I)
AFFILIATE_PARAMS = re.compile(r"^(tag|ascsubtag|irclickid|cjevent|ranmid|raneaid|ransiteid|affid|aff_id|affiliate|"
                              r"affiliate_id|wmlspartner|afsrc|campid)$", re.I)
# eBay Partner Network (owner's account, campaign "BestDealHunter", 2026-10-15). Campaign IDs are public: every
# EPN link carries one. EBAY_CAMPAIGN_ID (GitHub variable) overrides the one in deal_rules.json.
EBAY_ITEM = re.compile(r"https://www\.ebay\.com/itm/(\d{6,20})")
# Hosts that exist only to redirect through an affiliate network (no direct product URL to keep).
AFFILIATE_HOSTS = re.compile(r"(^|\.)(link\.amazon|amzn\.to|goto\.target\.com|goto\.walmart\.com|sjv\.io|pxf\.io|"
                             r"click\.linksynergy\.com|anrdoezrs\.net|dpbolvw\.net|jdoqocy\.com|kqzyfj\.com|"
                             r"tkqlhce\.com|shareasale\.com|rstyle\.me|howl\.me|bestbuy\.7tiv\.net|"
                             r"macys\.[a-z]+\.net)$", re.I)


def is_affiliate_link(url):
    """True when the link credits an affiliate (tracking parameter or affiliate redirect host)."""
    parsed = urlparse(str(url or ""))
    if AFFILIATE_HOSTS.search(parsed.hostname or ""):
        return True
    return any(AFFILIATE_PARAMS.match(k) for k, _ in parse_qsl(parsed.query, keep_blank_values=True))


def ebay_campaign_id(env=os.environ):
    rules = json.loads((Path(__file__).resolve().parent / "deal_rules.json").read_text(encoding="utf-8"))
    cid = str(env.get("EBAY_CAMPAIGN_ID") or rules.get("ebay_campaign_id") or "")
    return cid if re.fullmatch(r"\d{10}", cid) else None


def ebay_affiliate_url(url, campaign_id):
    """eBay Partner Network link for a plain eBay item URL (EPN's standard link format), or None."""
    m = EBAY_ITEM.fullmatch(str(url or ""))
    if not m or not campaign_id:
        return None
    return (f"https://www.ebay.com/itm/{m.group(1)}?mkcid=1&mkrid=711-53200-19255-0&siteid=0"
            f"&campid={campaign_id}&toolid=10001&mkevt=1")


def link(deal):
    """The link people follow for a deal: its affiliate link when it has one, else the product URL."""
    return deal.get("affiliate_url") or deal.get("url")


def direct_url(url):
    """The same product URL without tracking parameters or fragment; None for affiliate redirect hosts."""
    parsed = urlparse(str(url or ""))
    if AFFILIATE_HOSTS.search(parsed.hostname or ""):
        return None
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not TRACKING.match(k)]
    return urlunparse(parsed._replace(query=urlencode(query), fragment=""))


def disclosure(deal):
    """(short line for cards, caption lines) matching whether this deal's link is an affiliate link."""
    if is_affiliate_link(link(deal)):
        statement = ("As an Amazon Associate I earn from qualifying purchases." if deal.get("store") == "Amazon"
                     else "BestDealHunter may earn a commission from qualifying purchases.")
        return "#ad · We may earn a commission · Link in bio", ["#ad #affiliate #deals #bestdealhunter", statement]
    return ("Independent deal pick · Not sponsored · Link in bio",
            ["#deals #bestdealhunter", "Independent deal pick: not sponsored, and we earn nothing from this link."])
