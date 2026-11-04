import asyncio
import tempfile
import unittest
from pathlib import Path

from starlette.responses import Response

from app.api.mcp import BridgeAuth, bridge_app
from app.config.bridge import prepare_token


class DockerAuthTests(unittest.TestCase):
    def test_launcher_creates_and_reuses_token_without_accepting_config_injection(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env.docker"
            token = prepare_token(path)
            self.assertEqual(len(token), 43)
            self.assertEqual(prepare_token(path), token)
            path.write_text(
                "WECHAT_BRIDGE_TOKEN=" + token + "\nOTHER=value", encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                prepare_token(path)

    def test_bridge_fails_closed_without_a_strong_token(self):
        for token in ("", "short", "x" * 42, "x" * 129, '"' * 43):
            with self.assertRaises(ValueError):
                bridge_app(token)

    def test_only_matching_bearer_token_reaches_mcp(self):
        async def check():
            forwarded = []

            async def downstream(scope, receive, send):
                forwarded.append(scope)
                await Response(status_code=204)(scope, receive, send)

            token = "a" * 43
            app = BridgeAuth(downstream, token)
            output = []
            for authorization, expected in (
                (b"", 401),
                (b"Bearer invalid", 401),
                (b"Bearer \xff", 401),
                (b"Bearer " + token.encode(), 204),
            ):
                output.clear()

                async def receive():
                    return {"type": "http.request", "body": b""}

                async def send(event):
                    output.append(event)

                await app(
                    {
                        "type": "http",
                        "method": "POST",
                        "headers": [(b"authorization", authorization)],
                    },
                    receive,
                    send,
                )
                self.assertEqual(output[0]["status"], expected)
                self.assertNotIn(token, str(output))
            self.assertEqual(len(forwarded), 1)

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
