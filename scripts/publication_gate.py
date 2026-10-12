"""The single publication gate every route onto the site goes through.

validate() decides whether a deal record is well-formed, fresh and >= 50% off. This gate
decides whether it may be *published*: a verified price is not enough if a reviewer's
concern is still open or the deal has no image. Used by verify_deals.py (automatic),
approve_deal.py (manual) and refresh_deals.py (feed merge).

A deal is blocked while any of these hold:
  * its queue candidate is not explicitly approved: `publication_eligible` must be exactly
    true (missing counts as "not yet"), it must not be rejected, and its notes must not say
    "do not publish". A reviewer (or a discovery adapter that attaches no review notes) sets it;
  * any entry in the candidate's `blockers` lacks a resolution in `resolved_blockers`
    ({"<exact blocker text>": {"by": "<person>", "note": "<how it was resolved>"}}).
    Automation never writes resolutions: a blocker is closed by a person in a reviewed PR;
  * `image` is missing, is not a local file under images/, does not exist, or is a
    generated card made for a different deal (cards are named after the deal URL).
"""
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = re.compile(r"images/[\w./-]+\.(?:jpg|jpeg|png|webp)", re.I)
CARD = re.compile(r"images/generated/deal-([0-9a-f]{12})-(?:feed|square)\.jpg")
AUTOMATION = {"verify_deals", "github-actions", "github-actions[bot]", "claude", "bot"}


def card_id(url):
    """Same id as deal_graphics.card_id (PR #5): the first 12 hex chars of sha256(url)."""
    return hashlib.sha256(str(url or "").encode()).hexdigest()[:12]


def unresolved_blockers(candidate):
    """Blocker texts without a valid human resolution."""
    blockers = candidate.get("blockers") or []
    if isinstance(blockers, str):
        blockers = [blockers]
    resolved = candidate.get("resolved_blockers") if isinstance(candidate.get("resolved_blockers"), dict) else {}
    open_ = []
    for blocker in blockers:
        res = resolved.get(blocker)
        by = str(res.get("by", "")).strip() if isinstance(res, dict) else ""
        note = str(res.get("note", "")).strip() if isinstance(res, dict) else ""
        if not by or not note or by.lower() in AUTOMATION:
            open_.append(str(blocker))
    return open_


def image_problem(deal, root=ROOT, card_pending=False):
    """card_pending: the deal's own card will be rendered before anything is written."""
    image = deal.get("image")
    if not image:
        return "no image: a branded card or authorized photo is required before publishing"
    if not isinstance(image, str) or not IMAGE.fullmatch(image) or ".." in image:
        return f"image {image!r} is not a local file under images/"
    match = CARD.fullmatch(image)
    if match and match.group(1) != card_id(deal.get("url")):
        return f"image {image} is a card for a different deal URL"
    if card_pending and match:
        return None
    path = (root / image).resolve()
    if not path.is_relative_to((root / "images").resolve()) or not path.is_file():
        return f"image {image} does not exist"
    return None


def publication_problems(deal, candidate=None, root=ROOT, card_pending=False):
    """Every reason this deal may not be published now (empty list = may publish)."""
    problems = []
    if candidate is not None:
        if candidate.get("publication_eligible") is not True:
            problems.append("held: candidate is not marked publication_eligible true "
                            "(a reviewer sets it after resolving blockers)")
        notes = " ".join(str(candidate.get(k) or "") for k in ("notes", "review_note")).lower()
        if "do not publish" in notes or "not recommended for publication" in notes:
            problems.append("held: candidate notes say do not publish")
        if candidate.get("status") == "rejected":
            problems.append("candidate was rejected")
        problems += [f"unresolved blocker: {b}" for b in unresolved_blockers(candidate)]
    issue = image_problem(deal, root, card_pending)
    if issue:
        problems.append(issue)
    return problems
