"""실제 전송 본문 검증 — anthropic SDK 가 무엇을 보내는지 직접 받아서 확인한다.

로컬 HTTP 서버를 띄우고 `base_url` 을 그쪽으로 돌린다. 네트워크도 API Key 도 필요 없지만,
SDK 의 직렬화·응답 파싱 경로는 실제와 동일하게 지난다.

`anthropic` 이 설치돼 있지 않으면 통째로 건너뛴다.
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from helpers import BaseTest

from aigitgen.client import ModelParams
from aigitgen.errors import ApiCallError

try:  # 설치돼 있을 때만 도는 테스트
    import anthropic  # noqa: F401

    HAS_SDK = True
except ImportError:  # pragma: no cover
    HAS_SDK = False

SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {"subject": {"type": "string"}},
        "required": ["subject"],
        "additionalProperties": False,
    },
}


class _Handler(BaseHTTPRequestHandler):
    captured = []
    reply = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": [{"type": "text", "text": '{"subject": "테스트 제목"}'}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 111, "output_tokens": 22},
    }
    status = 200

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler 규약
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length)
        type(self).captured.append(json.loads(body))
        payload = json.dumps(type(self).reply).encode()
        self.send_response(type(self).status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # 테스트 출력 조용히
        pass


@unittest.skipUnless(HAS_SDK, "anthropic 패키지가 설치돼 있지 않다")
class TestWirePayload(BaseTest):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Handler.captured = []
        _Handler.status = 200

    def make(self, **kw):
        from aigitgen.client import ClaudeGenerator

        gen = ClaudeGenerator(ModelParams(**kw), api_key="test-key-not-real")
        gen._client = gen._anthropic.Anthropic(
            api_key="test-key-not-real", base_url=self.base_url, max_retries=0
        )
        return gen

    def test_요청_본문에_모델_max_tokens_스키마가_실린다(self):
        gen = self.make(model="claude-sonnet-4-6", max_tokens=1234)
        data = gen.generate("SYS", "USER", SCHEMA)
        self.assertEqual(data, {"subject": "테스트 제목"})

        body = _Handler.captured[0]
        self.assertEqual(body["model"], "claude-sonnet-4-6")
        self.assertEqual(body["max_tokens"], 1234)
        self.assertEqual(body["system"], "SYS")
        self.assertEqual(body["messages"], [{"role": "user", "content": "USER"}])
        self.assertEqual(body["output_config"]["format"], SCHEMA)

    def test_sampling_지원_모델은_temperature_를_본문에_넣는다(self):
        gen = self.make(model="claude-sonnet-4-6", temperature=0.7)
        gen.generate("SYS", "USER", SCHEMA)
        self.assertEqual(_Handler.captured[0]["temperature"], 0.7)

    def test_sampling_미지원_모델은_temperature_를_빼고_보낸다(self):
        gen = self.make(model="claude-opus-5", temperature=0.7, temperature_explicit=True)
        gen.generate("SYS", "USER", SCHEMA)
        self.assertNotIn("temperature", _Handler.captured[0])
        self.assertTrue(any("temperature" in w for w in gen.warnings))

    def test_호출_횟수와_토큰_사용량을_집계한다(self):
        gen = self.make()
        gen.generate("SYS", "USER", SCHEMA)
        gen.generate("SYS", "USER2", SCHEMA)
        self.assertEqual(gen.calls, 2)
        self.assertEqual(gen.usage.input_tokens, 222)
        self.assertEqual(gen.usage.output_tokens, 44)

    def test_max_tokens_에서_잘리면_원인을_알려준다(self):
        _Handler.reply = {**_Handler.reply, "stop_reason": "max_tokens"}
        try:
            gen = self.make(max_tokens=500)
            with self.assertRaises(ApiCallError) as ctx:
                gen.generate("SYS", "USER", SCHEMA)
            self.assertIn("--max-tokens", str(ctx.exception))
        finally:
            _Handler.reply = {**_Handler.reply, "stop_reason": "end_turn"}

    def test_401_은_인증_오류로_번역된다(self):
        _Handler.status = 401
        gen = self.make()
        with self.assertRaises(ApiCallError) as ctx:
            gen.generate("SYS", "USER", SCHEMA)
        self.assertIn("인증", str(ctx.exception))

    def test_500_은_서버_오류로_번역된다(self):
        _Handler.status = 500
        gen = self.make()
        with self.assertRaises(ApiCallError) as ctx:
            gen.generate("SYS", "USER", SCHEMA)
        self.assertIn("서버 오류", str(ctx.exception))

    def test_연결_실패는_네트워크_안내가_된다(self):
        from aigitgen.client import ClaudeGenerator

        gen = ClaudeGenerator(ModelParams(), api_key="test-key-not-real")
        # 아무도 듣고 있지 않은 포트
        gen._client = gen._anthropic.Anthropic(
            api_key="test-key-not-real", base_url="http://127.0.0.1:1", max_retries=0
        )
        with self.assertRaises(ApiCallError) as ctx:
            gen.generate("SYS", "USER", SCHEMA)
        self.assertIn("네트워크", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
