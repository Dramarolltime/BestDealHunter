"""Alpha image handoff (issue #59): Drive file -> checks -> signed Cloudinary upload -> ledger. No network."""
import io
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import alpha_images as ai  # noqa: E402

NOW = datetime(2026, 10, 16, 7, 0, tzinfo=timezone.utc)
CREDS = ("democloud", "key123", "secret456")
FILE_A = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
FILE_B = "1ZyXwVuTsRqPoNmLkJiHgFeDcBa987654"
HEADER = "Deal ID,Store,Product,AI Image Drive URL,Screenshot Drive URL,Status"


def sheet(*rows):
    return "\n".join([HEADER, *rows]) + "\n"


def row(deal_id, file_id=FILE_A, status="Draft"):
    return f'{deal_id},Target,Test,https://drive.google.com/file/d/{file_id}/view?usp=sharing,,{status}'


def image(fmt="PNG", size=(1080, 1350), color="#123456"):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    return buf.getvalue()


class Resp:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, n=-1): return self.data if n < 0 else self.data[:n]


class Fake:
    """Drive downloads + Cloudinary API in memory."""

    def __init__(self, files):
        self.files, self.uploads = files, []

    def opener(self, req, timeout=60):
        url = req.full_url
        return Resp(self.files[url if url.startswith("https://res.cloudinary.com/") else url.rsplit("id=", 1)[-1]])

    def transport(self, method, url, body=None, headers=None):
        if method == "POST":
            self.uploads.append(body)
            public_id = body.split(b'name="public_id"\r\n\r\n', 1)[1].split(b"\r\n", 1)[0].decode()
            assert b"secret456" not in body  # the secret signs the request but is never sent
            return 200, {}, json.dumps({"secure_url": f"https://res.cloudinary.com/democloud/image/upload/v1/"
                                                      f"{public_id}.png"}).encode()
        return 200, {"Content-Type": "image/png"}, b""


class HandoffTests(unittest.TestCase):
    def run_sheet(self, text, files, ledger=None):
        fake = Fake(files)
        ledger = [] if ledger is None else ledger
        new = ai.run(text, ledger, CREDS, fake.opener, fake.transport, NOW, log=lambda *_: None)
        return new, ledger, fake

    def test_drive_image_is_uploaded_and_recorded(self):
        new, ledger, fake = self.run_sheet(sheet(row("target-94918812")), {FILE_A: image()})
        e = new[0]
        self.assertEqual(e["result"], "uploaded")
        self.assertRegex(e["cloudinary_url"], r"^https://res\.cloudinary\.com/democloud/image/upload/v1/"
                                              r"bestdealhunter/alpha/target-94918812-[0-9a-f]{10}\.png$")
        self.assertEqual((e["width"], e["height"], e["mime"]), (1080, 1350, "image/png"))
        self.assertEqual(ai.image_for("target-94918812", f"https://drive.google.com/file/d/{FILE_A}/view", ledger),
                         e["cloudinary_url"])
        self.assertIsNone(ai.image_for("target-94918812", f"https://drive.google.com/file/d/{FILE_B}/view", ledger))

    def test_test_rows_go_to_a_separate_folder(self):
        new, _, _ = self.run_sheet(sheet(row("TEST-001")), {FILE_A: image("JPEG")})
        self.assertTrue(new[0]["test"])
        self.assertIn("/bestdealhunter/alpha-test/test-001-", new[0]["cloudinary_url"])

    def test_each_image_is_handled_once(self):
        _, ledger, _ = self.run_sheet(sheet(row("d-1")), {FILE_A: image()})
        new, ledger, fake = self.run_sheet(sheet(row("d-1")), {FILE_A: image()}, ledger)
        self.assertEqual((new, fake.uploads), ([], []))

    def test_invalid_files_are_rejected_with_a_reason(self):
        cases = {
            "not a JPEG or PNG": b"<!DOCTYPE html><html>Drive sign-in</html>",
            "could not be decoded": b"\x89PNG\r\n\x1a\n" + b"\x00" * 50,
            "at least 600 px": image(size=(400, 400)),
            "larger than 10 MB": b"\xff\xd8\xff" + b"\x00" * (ai.MAX_BYTES + 1),
        }
        for reason, data in cases.items():
            with self.subTest(reason):
                new, _, fake = self.run_sheet(sheet(row("d-1")), {FILE_A: data})
                self.assertEqual(new[0]["result"], "rejected")
                self.assertIn(reason, new[0]["reason"])
                self.assertEqual(fake.uploads, [])

    def test_same_image_for_another_deal_is_refused(self):
        same = image()
        new, _, _ = self.run_sheet(sheet(row("d-1", FILE_A), row("d-2", FILE_B)), {FILE_A: same, FILE_B: same})
        self.assertEqual([e["result"] for e in new], ["uploaded", "rejected"])
        self.assertIn("already used for deal d-1", new[1]["reason"])

    def test_only_drive_links_and_safe_deal_ids(self):
        new, _, _ = self.run_sheet(sheet("d-1,Target,Test,https://example.com/x.png,,Draft",
                                         row("bad id!", FILE_B)), {FILE_B: image()})
        self.assertEqual([e["result"] for e in new], ["rejected", "rejected"])

    def test_no_sheet_link_or_secrets_means_nothing_happens(self):
        def boom(*a, **k):
            raise AssertionError("no network")
        self.assertEqual(ai.main([], env={}, opener=boom), 0)
        self.assertEqual(ai.main([], env={"DEALS_SHEET_CSV_URL": "https://docs.google.com/spreadsheets/d/"
                                          "1OExDVOjUYZP5eDucyDv-tsJAMXeHZexmzVePnkKmuMw/edit"}, opener=boom), 1)


OURS = "https://res.cloudinary.com/democloud/image/upload/v1792200000/alpha/target-1.png"
THEIRS = "https://res.cloudinary.com/othercloud/image/upload/v1792200000/target-1.jpg"
CLD_HEADER = "Deal ID,Store,Product,AI Image Drive URL,AI Image Cloudinary URL,Status"


class AlphaUploadsToCloudinaryTests(unittest.TestCase):
    """Alpha uploads with its own Cloudinary connection and pastes the link; we still download and check it."""

    def go(self, *rows, files, ledger=None):
        fake = Fake(files)
        ledger = [] if ledger is None else ledger
        text = "\n".join([CLD_HEADER, *rows]) + "\n"
        return ai.run(text, ledger, CREDS, fake.opener, fake.transport, NOW, log=lambda *_: None), ledger, fake

    def test_link_in_our_account_is_checked_and_used_without_reupload(self):
        new, ledger, fake = self.go(f"target-1,Target,Test,,{OURS},Draft", files={OURS: image()})
        self.assertEqual((new[0]["result"], new[0]["cloudinary_url"], new[0]["source_url"]), ("uploaded", OURS, OURS))
        self.assertEqual(fake.uploads, [])
        self.assertEqual(ai.image_for("target-1", OURS, ledger), OURS)
        again, _, _ = self.go(f"target-1,Target,Test,,{OURS},Draft", files={OURS: image()}, ledger=ledger)
        self.assertEqual(again, [])

    def test_link_in_another_account_is_copied_into_ours(self):
        new, _, fake = self.go(f"target-1,Target,Test,,{THEIRS},Draft", files={THEIRS: image("JPEG")})
        self.assertEqual(len(fake.uploads), 1)
        self.assertRegex(new[0]["cloudinary_url"], r"^https://res\.cloudinary\.com/democloud/.*/bestdealhunter/alpha/")

    def test_bad_cloudinary_images_and_other_hosts_are_rejected(self):
        new, _, fake = self.go(f"target-1,Target,Test,,{OURS},Draft",
                               "target-2,Target,Test,,https://example.com/x.png,Draft",
                               files={OURS: image(size=(300, 300))})
        self.assertEqual([e["result"] for e in new], ["rejected", "rejected"])
        self.assertIn("at least 600 px", new[0]["reason"])
        self.assertEqual(fake.uploads, [])

    def test_drive_link_wins_when_both_are_filled(self):
        drive = f"https://drive.google.com/file/d/{FILE_A}/view"
        new, _, fake = self.go(f"target-1,Target,Test,{drive},{OURS},Draft", files={FILE_A: image()})
        self.assertEqual(new[0]["drive_file_id"], FILE_A)
        self.assertEqual(len(fake.uploads), 1)

    def test_unchecked_cloudinary_link_is_never_published(self):
        import sheet_intake as si
        saved, tmp = ai.LEDGER, Path(__file__).resolve().parent / "_alpha_ledger_cld.json"
        tmp.write_text("[]")
        try:
            ai.LEDGER = tmp
            self.assertIsNone(si.alpha_image({"deal_id": "target-1", "hosted_image": OURS}))
            tmp.write_text(json.dumps([{"deal_id": "target-1", "source_url": OURS, "result": "uploaded",
                                        "cloudinary_url": OURS}]))
            self.assertEqual(si.alpha_image({"deal_id": "target-1", "hosted_image": OURS}), OURS)
            self.assertIsNone(si.alpha_image({"deal_id": "target-2", "hosted_image": OURS}))
        finally:
            ai.LEDGER = saved
            tmp.unlink()


class PublishingUsesTheHostedImageTests(unittest.TestCase):
    def test_sheet_intake_picks_the_verified_cloudinary_copy(self):
        import sheet_intake as si
        hosted = "https://res.cloudinary.com/democloud/image/upload/v1/bestdealhunter/alpha/d-1-abc.png"
        ledger = [{"deal_id": "d-1", "drive_file_id": FILE_A, "result": "uploaded", "cloudinary_url": hosted}]
        saved = ai.LEDGER
        tmp = Path(__file__).resolve().parent / "_alpha_ledger_test.json"
        tmp.write_text(json.dumps(ledger))
        try:
            ai.LEDGER = tmp
            r = {"deal_id": "d-1", "image": f"https://drive.google.com/file/d/{FILE_A}/view"}
            self.assertEqual(si.alpha_image(r), hosted)
            other = {"deal_id": "d-1", "image": f"https://drive.google.com/file/d/{FILE_B}/view"}
            self.assertEqual(si.alpha_image(other), f"https://drive.google.com/uc?export=download&id={FILE_B}")
        finally:
            ai.LEDGER = saved
            tmp.unlink()

    def test_test_rows_are_never_published(self):
        import sheet_intake as si
        text = ("Deal ID,Store,Product,Status\nTEST-001,Target,Test,Needs Claude review\n")
        self.assertEqual(si.run(text, [], [], NOW, log=lambda *_: None), [])


if __name__ == "__main__":
    unittest.main()
