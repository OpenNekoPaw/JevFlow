import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jevflow import FlowError, JevClient


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = self.server
        server.seen.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                            "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
        self.send_response(server.response_status)
        self.send_header("Content-Type", "application/json")
        if server.response_status == 302:
            self.send_header("Location", server.endpoint + "/redirected")
        self.end_headers()
        self.wfile.write(server.response_body)

    def log_message(self, *args):
        pass


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.endpoint = "http://127.0.0.1:" + str(cls.server.server_port) + "/v1/systemone"
        cls.thread = threading.Thread(target=lambda: cls.server.serve_forever(poll_interval=0.01), daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.seen = []
        self.server.response_status = 200
        self.server.response_body = json.dumps({"model": "jev-test", "answers": {
            "ready": {"type": "noul", "noul": 0.9}}, "usage": {"input_tokens": 12, "output_tokens": 3}}).encode()
        self.client = JevClient(api_key="test-only-secret", endpoint=self.server.endpoint)
        self.questions = {"ready": {"type": "noul", "instructions": "Is the document ready?"}}

    def test_native_http_contract(self):
        result = self.client.evaluate({"text": "完成"}, self.questions, node_id="local-only")
        request = self.server.seen[0]
        self.assertEqual(request["authorization"], "Bearer test-only-secret")
        self.assertEqual(request["path"], "/v1/systemone")
        self.assertEqual(request["body"], {"model": "jev-latest", "state": {"text": "完成"}, "questions": self.questions})
        self.assertEqual(result["answers"]["ready"]["noul"], 0.9)
        self.assertEqual(result["usage"]["input_tokens"], 12)

    def test_error_is_sanitized_and_not_retried(self):
        self.server.response_status = 429
        self.server.response_body = b'test-only-secret'
        with self.assertRaisesRegex(FlowError, "429") as error:
            self.client.evaluate("input", self.questions)
        self.assertNotIn("test-only-secret", str(error.exception))
        self.assertEqual(len(self.server.seen), 1)

    def test_redirect_is_not_followed(self):
        self.server.response_status = 302
        with self.assertRaisesRegex(FlowError, "302"):
            self.client.evaluate("input", self.questions)
        self.assertEqual(len(self.server.seen), 1)

    def test_invalid_json(self):
        self.server.response_body = b'not-json'
        with self.assertRaisesRegex(FlowError, "invalid JSON"):
            self.client.evaluate("input", self.questions)

    def test_invalid_answer(self):
        self.server.response_body = b'{"answers":{"ready":{"type":"noul","noul":2}}}'
        with self.assertRaisesRegex(FlowError, "Invalid noul"):
            self.client.evaluate("input", self.questions)

    def test_missing_key_fails_before_network(self):
        client = JevClient(api_key="", endpoint=self.server.endpoint)
        with self.assertRaisesRegex(FlowError, "TYPESAFE_API_KEY"):
            client.evaluate("input", self.questions)
        self.assertEqual(self.server.seen, [])


if __name__ == "__main__":
    unittest.main()
