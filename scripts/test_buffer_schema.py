"""Buffer schema check tests: introspection queries only, key never printed. No network."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buffer_schema as bs  # noqa: E402

KEY = "secret-key-123"


def T(kind, name=None, of=None):
    return {"kind": kind, "name": name, "ofType": of}


TYPES = {
    "CreatePostInput": {"name": "CreatePostInput", "kind": "INPUT_OBJECT", "description": None, "fields": None,
                        "enumValues": None, "possibleTypes": None, "inputFields": [
                            {"name": "saveToDraft", "description": "Save as draft", "defaultValue": "false",
                             "type": T("SCALAR", "Boolean")},
                            {"name": "mode", "description": None, "defaultValue": None, "type": T("ENUM", "ShareMode")}]},
    "ShareMode": {"name": "ShareMode", "kind": "ENUM", "description": None, "fields": None, "inputFields": None,
                  "possibleTypes": None, "enumValues": [{"name": "addToQueue", "description": None}]},
}


class Fake:
    def __init__(self, error=None):
        self.requests, self.error = [], error

    def __call__(self, payload):
        self.requests.append(payload)
        if self.error:
            return {"errors": [{"message": self.error}]}
        if "__schema" in payload["query"]:
            return {"data": {"__schema": {
                "queryType": {"name": "Query", "fields": []},
                "mutationType": {"name": "Mutation", "fields": [
                    {"name": "createPost", "description": None, "type": T("UNION", "PostActionPayload"),
                     "args": [{"name": "input", "type": T("NON_NULL", None, T("INPUT_OBJECT", "CreatePostInput"))}]},
                    {"name": "deleteOrganization", "description": None, "type": T("SCALAR", "Boolean"), "args": []}]}}}}
        return {"data": {"__type": TYPES.get(payload["variables"]["name"])}}


class SchemaTests(unittest.TestCase):
    def run_main(self, fake):
        lines = []
        code = bs.main({"BUFFER_API_KEY": KEY}, fake, lines.append)
        return code, "\n".join(lines)

    def test_renders_create_post_input_and_enums(self):
        fake = Fake()
        code, text = self.run_main(fake)
        self.assertEqual(code, 0)
        self.assertIn("`createPost(input: CreatePostInput!): PostActionPayload`", text)
        self.assertIn("`saveToDraft: Boolean = false`", text)
        self.assertIn("`addToQueue`", text)

    def test_only_introspection_queries_are_sent(self):
        fake = Fake()
        self.run_main(fake)
        for req in fake.requests:
            self.assertTrue(req["query"].lstrip().startswith("query"))
            self.assertIn("__", req["query"])
            self.assertNotIn("mutation ", req["query"].lower().replace("mutationtype", ""))

    def test_key_scrubbed_from_errors(self):
        code, text = self.run_main(Fake(error=f"denied {KEY}"))
        self.assertEqual(code, 1)
        self.assertNotIn(KEY, text)


if __name__ == "__main__":
    unittest.main()
