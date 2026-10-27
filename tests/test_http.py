import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from app.server import make_server


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server("127.0.0.1", 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def get(self, path):
        return urllib.request.urlopen(self.url + path, timeout=3)

    def test_root(self):
        with patch.dict("os.environ", {"APP_VERSION": "test-version"}):
            with self.get("/") as response:
                body = json.load(response)
                self.assertEqual(response.status, 200)
                self.assertEqual(body["service"], "ops-lab")
                self.assertEqual(body["version"], "test-version")
                self.assertTrue(body["hostname"])

    def test_probes(self):
        for path in ("/healthz", "/readyz"):
            with self.subTest(path=path), self.get(path) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response), {"status": "ok"})

    def test_unknown_path(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/missing")
        with caught.exception as response:
            self.assertEqual(response.code, 404)
            self.assertEqual(json.load(response), {"error": "not_found"})

    def test_headers_and_content_length(self):
        with self.get("/") as response:
            self.assertEqual(response.headers["Content-Type"], "application/json; charset=utf-8")
            self.assertEqual(int(response.headers["Content-Length"]), len(response.read()))

    def test_query_string_does_not_break_probe(self):
        with self.get("/healthz?check=1") as response:
            self.assertEqual(json.load(response), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
