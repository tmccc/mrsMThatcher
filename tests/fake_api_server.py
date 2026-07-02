from __future__ import annotations

import json
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


def load_scenario(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class FakeApiServer:
    def __init__(self, scenario: dict[str, Any]) -> None:
        self.scenario = scenario
        self.posts: list[dict[str, Any]] = []
        self.uploads: list[dict[str, Any]] = []
        self.xai_requests: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.path_counts: dict[str, int] = {}
        self._next_post_id = int(scenario.get("next_post_id", 900000))

        handler = self._handler_class()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.fake = self  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def start(self) -> "FakeApiServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()

    def _handler_class(self):
        class Handler(BaseHTTPRequestHandler):
            server_version = "MrsFakeApi/1.0"

            def log_message(self, format: str, *args: object) -> None:
                return

            @property
            def fake(self) -> "FakeApiServer":
                return self.server.fake  # type: ignore[attr-defined]

            def _json_response(self, status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> None:
                payload = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(payload)

            def _text_response(self, status: int, text: str, content_type: str = "text/plain") -> None:
                payload = text.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0") or 0)
                if not length:
                    return {}
                data = self.rfile.read(length)
                try:
                    return json.loads(data.decode("utf-8"))
                except Exception:
                    return {"_raw": data.decode("utf-8", errors="replace")}

            def _record(self, method: str, path: str, query: dict[str, list[str]], body: Any = None) -> None:
                self.fake.path_counts[path] = self.fake.path_counts.get(path, 0) + 1
                self.fake.requests.append(
                    {
                        "method": method,
                        "path": path,
                        "query": query,
                        "body": body,
                    }
                )

            def _maybe_network_failure(self, path: str) -> bool:
                failures = self.fake.scenario.get("network_failures", {})
                failure = failures.get(path)
                if not failure:
                    return False
                if failure == "timeout":
                    time.sleep(float(self.fake.scenario.get("network_timeout_sleep_seconds", 2)))
                    return True
                if failure == "closed":
                    self.close_connection = True
                    self.connection.close()
                    return True
                return False

            def _maybe_non_json(self, path: str) -> bool:
                non_json_paths = set(self.fake.scenario.get("non_json_paths", []))
                if path not in non_json_paths:
                    return False
                self._text_response(200, "not json")
                return True

            def _maybe_rate_limit(self, path: str) -> bool:
                rate_limit_paths = set(self.fake.scenario.get("rate_limit_paths", []))
                if path not in rate_limit_paths:
                    return False
                reset = str(self.fake.scenario.get("rate_limit_reset_epoch", 4102444800))
                self._json_response(
                    429,
                    {"errors": [{"title": "Too Many Requests"}]},
                    {
                        "x-rate-limit-limit": "1",
                        "x-rate-limit-remaining": "0",
                        "x-rate-limit-reset": reset,
                    },
                )
                return True

            def _maybe_error_status(self, path: str) -> bool:
                error_paths = self.fake.scenario.get("error_paths", {})
                if path not in error_paths:
                    return False
                spec = error_paths[path]
                if isinstance(spec, int):
                    status = spec
                    body = {"errors": [{"detail": f"configured {status} failure"}]}
                else:
                    status = int(spec.get("status", 500))
                    body = spec.get("body", {"errors": [{"detail": f"configured {status} failure"}]})
                self._json_response(status, body)
                return True

            def _filter_since(self, items: list[dict[str, Any]], query: dict[str, list[str]]) -> list[dict[str, Any]]:
                since_values = query.get("since_id") or []
                if not since_values:
                    return items
                try:
                    since_id = int(since_values[0])
                except ValueError:
                    return items
                return [item for item in items if int(str(item.get("id", "0"))) > since_id]

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                path = parsed.path
                query = parse_qs(parsed.query)
                self._record("GET", path, query)

                if self._maybe_network_failure(path):
                    return
                if self._maybe_rate_limit(path):
                    return
                if self._maybe_error_status(path):
                    return
                if self._maybe_non_json(path):
                    return

                if path.startswith("/2/users/") and path.endswith("/mentions"):
                    mentions = list(self.fake.scenario.get("mentions", []))
                    self._json_response(200, {"data": self._filter_since(mentions, query)} if mentions else {})
                    return

                if path == "/2/tweets/search/recent":
                    replies = list(self.fake.scenario.get("search_recent", []))
                    self._json_response(200, {"data": self._filter_since(replies, query)} if replies else {})
                    return

                if path.startswith("/2/tweets/") and path.endswith("/quote_tweets"):
                    post_id = path.split("/")[3]
                    data = self.fake.scenario.get("quote_tweets", {}).get(post_id, {})
                    self._json_response(200, data)
                    return

                if path.startswith("/2/tweets/"):
                    tweet_id = path.split("/")[3]
                    tweet = self.fake.scenario.get("tweets", {}).get(tweet_id)
                    self._json_response(200, {"data": tweet} if tweet else {})
                    return

                self._json_response(404, {"error": f"Unhandled GET {path}"})

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                path = parsed.path
                query = parse_qs(parsed.query)

                if self._maybe_network_failure(path):
                    self._record("POST", path, query, "<network_failure>")
                    return

                if path == "/2/tweets":
                    body = self._read_json()
                    self._record("POST", path, query, body)

                    post_responses = self.fake.scenario.setdefault("tweet_post_responses", [])
                    if post_responses:
                        response = post_responses.pop(0)
                        status = int(response.get("status", 201))
                        if status >= 400:
                            self._json_response(status, response.get("body", {"error": "configured post failure"}))
                            return
                        self.fake.posts.append(body)
                        self._json_response(status, response.get("body", {}))
                        return

                    if body.get("made_with_ai") and self.fake.scenario.get("fail_made_with_ai_once"):
                        self.fake.scenario["fail_made_with_ai_once"] = False
                        self._json_response(400, {"errors": [{"detail": "made_with_ai is not accepted here"}]})
                        return

                    self.fake.posts.append(body)
                    post_id = str(self.fake._next_post_id)
                    self.fake._next_post_id += 1
                    self._json_response(201, {"data": {"id": post_id, "text": body.get("text", "")}})
                    return

                if path == "/2/media/upload":
                    self._record("POST", path, query, "<multipart>")
                    status = int(self.fake.scenario.get("v2_media_status", 200))
                    if status >= 400:
                        self._json_response(status, {"errors": [{"detail": "configured v2 media failure"}]})
                        return
                    media_id = str(self.fake.scenario.get("v2_media_id", "fake-media-v2"))
                    self.fake.uploads.append({"path": path, "media_id": media_id})
                    self._json_response(200, {"data": {"id": media_id}})
                    return

                if path == "/1.1/media/upload.json":
                    self._record("POST", path, query, "<multipart>")
                    status = int(self.fake.scenario.get("v1_media_status", 200))
                    if status >= 400:
                        self._json_response(status, {"errors": [{"detail": "configured v1 media failure"}]})
                        return
                    media_id = str(self.fake.scenario.get("v1_media_id", "fake-media-v1"))
                    self.fake.uploads.append({"path": path, "media_id": media_id})
                    self._json_response(200, {"media_id_string": media_id})
                    return

                if path == "/v1/chat/completions":
                    body = self._read_json()
                    self._record("POST", path, query, body)
                    if self.fake.scenario.get("xai_non_json"):
                        self._text_response(200, "not json")
                        return
                    if "xai_success_body" in self.fake.scenario:
                        self._json_response(200, self.fake.scenario["xai_success_body"])
                        return
                    xai_status = int(self.fake.scenario.get("xai_status", 200))
                    if xai_status >= 400:
                        self._json_response(xai_status, {"error": "configured xai failure"})
                        return
                    self.fake.xai_requests.append(body)
                    replies = self.fake.scenario.setdefault("grok_replies", [])
                    reply = replies.pop(0) if replies else self.fake.scenario.get("grok_reply", "A measured reply is usually the sharpest one.")
                    self._json_response(
                        200,
                        {
                            "choices": [
                                {
                                    "message": {
                                        "content": reply,
                                    }
                                }
                            ],
                            "usage": {"total_tokens": 12},
                        },
                    )
                    return

                self._record("POST", path, query, "<unhandled>")
                self._json_response(404, {"error": f"Unhandled POST {path}"})

        return Handler


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    args = parser.parse_args()

    server = FakeApiServer(load_scenario(args.scenario)).start()
    print(server.url, flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
