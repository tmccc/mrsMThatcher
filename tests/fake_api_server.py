from __future__ import annotations

from collections.abc import Iterable
import json
import re
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from reply_strategy import split_reply_sentences
from tests.helpers.single_call_fixtures import valid_png


def load_scenario(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class FakeApiServer:
    def __init__(self, scenario: dict[str, Any]) -> None:
        self.scenario = scenario
        self.posts: list[dict[str, Any]] = []
        self.uploads: list[dict[str, Any]] = []
        self.xai_requests: list[dict[str, Any]] = []
        self.openai_requests: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.path_counts: dict[str, int] = {}
        self._next_post_id = int(scenario.get("next_post_id", 900000))
        self._current_ai_reply_text = ""
        self._known_tweets: dict[str, dict[str, Any]] = {}
        self._known_tweets_lock = threading.Lock()

        configured_tweets = scenario.get("tweets", {})
        if isinstance(configured_tweets, dict):
            self._remember_tweets(configured_tweets.values())

        handler = self._handler_class()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.fake = self  # type: ignore[attr-defined]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )

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

    def _remember_tweets(self, tweets: object) -> None:
        """Index posts exposed by discovery endpoints for later ID lookups."""
        if not isinstance(tweets, Iterable) or isinstance(tweets, (str, bytes, dict)):
            return
        with self._known_tweets_lock:
            for tweet in tweets:
                if not isinstance(tweet, dict):
                    continue
                tweet_id = str(tweet.get("id") or "")
                if tweet_id:
                    self._known_tweets[tweet_id] = dict(tweet)

    def _known_tweet(self, tweet_id: str) -> dict[str, Any] | None:
        configured_tweets = self.scenario.get("tweets", {})
        if isinstance(configured_tweets, dict):
            configured = configured_tweets.get(tweet_id)
            if isinstance(configured, dict):
                return dict(configured)
        with self._known_tweets_lock:
            tweet = self._known_tweets.get(tweet_id)
            return dict(tweet) if tweet is not None else None

    def _handler_class(self):
        class Handler(BaseHTTPRequestHandler):
            server_version = "MrsFakeApi/1.0"

            def log_message(self, format: str, *args: object) -> None:
                return

            @property
            def fake(self) -> "FakeApiServer":
                return self.server.fake  # type: ignore[attr-defined]

            def _json_response(self, status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> None:
                if self.command == "GET" and urlparse(self.path).path.startswith("/2/"):
                    fields = set((parse_qs(urlparse(self.path).query).get("tweet.fields") or [""])[0].split(","))
                    def selected_fields(value):
                        if isinstance(value, list):
                            return [selected_fields(item) for item in value]
                        if isinstance(value, dict):
                            return {key: selected_fields(item) for key, item in value.items()
                                    if key != "note_tweet" or "note_tweet" in fields}
                        return value
                    body = selected_fields(body)
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

            def _bytes_response(self, status: int, payload: bytes, content_type: str) -> None:
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

            def _ai_first_payload(self, body: dict[str, Any]) -> dict[str, Any] | None:
                response_format = body.get("response_format")
                if not isinstance(response_format, dict):
                    return None
                schema = response_format.get("json_schema")
                name = str(schema.get("name") or "") if isinstance(schema, dict) else ""
                if not name.startswith("ai_reply_"):
                    return None
                stage = name.removeprefix("ai_reply_")

                messages = body.get("messages")
                user_content: object = ""
                if isinstance(messages, list) and messages and isinstance(messages[-1], dict):
                    user_content = messages[-1].get("content", "")
                if isinstance(user_content, list):
                    text_parts = [
                        str(item.get("text") or "")
                        for item in user_content
                        if isinstance(item, dict) and item.get("type") == "text"
                    ]
                    user_content = "\n".join(text_parts)
                try:
                    supplied = json.loads(str(user_content))
                except (TypeError, ValueError):
                    supplied = {}

                if stage in {"proposer", "revision_proposer"}:
                    replies = self.fake.scenario.setdefault("grok_replies", [])
                    reply = replies.pop(0) if replies else self.fake.scenario.get(
                        "grok_reply",
                        "Clarity matters.",
                    )
                    reply = str(reply)
                    self.fake._current_ai_reply_text = reply
                    if reply.strip().upper() == "SKIP":
                        return {
                            "mode": "no_reply",
                            "interpretation": "No useful and safe reply is warranted.",
                            "proposed_reply": "",
                            "direct_factual_question_present": False,
                            "requested_answer_type": "none",
                            "direct_answer_text": "",
                            "factual_claims": [],
                            "exact_thatcher_wording_used": False,
                            "exact_thatcher_wording": "",
                            "tone": "none",
                            "confidence": "high",
                            "no_reply_reason": "The integration fixture selected no_reply.",
                        }
                    return {
                        "mode": "opinion_or_principle",
                        "interpretation": "The contribution invites a concise general response.",
                        "proposed_reply": reply,
                        "direct_factual_question_present": False,
                        "requested_answer_type": "none",
                        "direct_answer_text": "",
                        "factual_claims": [],
                        "exact_thatcher_wording_used": False,
                        "exact_thatcher_wording": "",
                        "tone": "neutral",
                        "confidence": "high",
                        "no_reply_reason": "",
                    }

                if stage in {"no_reply_reviewer", "revision_no_reply_reviewer"}:
                    verdicts = self.fake.scenario.setdefault(
                        "no_reply_review_verdicts",
                        [],
                    )
                    verdict = str(
                        verdicts.pop(0) if verdicts else "confirm_no_reply"
                    )
                    return {
                        "verdict": verdict,
                        "reasons": [
                            "The deterministic integration fixture reviewed the silence decision."
                        ],
                        "revision_instructions": (
                            "Produce a safe, relevant acknowledgement."
                            if verdict == "require_reply"
                            else ""
                        ),
                    }

                if stage in {"evidence", "revision_evidence"}:
                    rows = []
                    claims = supplied.get("claims", []) if isinstance(supplied, dict) else []
                    candidates_by_claim = (
                        supplied.get("candidate_passages_by_claim", {})
                        if isinstance(supplied, dict)
                        else {}
                    )
                    for claim in claims if isinstance(claims, list) else []:
                        if not isinstance(claim, dict):
                            continue
                        claim_id = str(claim.get("claim_id") or "")
                        candidates = candidates_by_claim.get(claim_id, []) if isinstance(candidates_by_claim, dict) else []
                        candidate = candidates[0] if isinstance(candidates, list) and candidates else None
                        rows.append({
                            "claim_id": claim_id,
                            "claim_text": str(claim.get("claim_text") or ""),
                            "verdict": "supports" if isinstance(candidate, dict) else "insufficient",
                            "evidence": ([{
                                "evidence_id": str(candidate.get("evidence_id") or ""),
                                "exact_supporting_passage": str(candidate.get("passage") or ""),
                                "relation": "supports",
                            }] if isinstance(candidate, dict) else []),
                            "actor": str(claim.get("actor") or ""),
                            "action_or_relationship": str(claim.get("action_or_relationship") or ""),
                            "direction_or_polarity": str(claim.get("direction_or_polarity") or ""),
                            "date_or_period": str(claim.get("date_or_period") or ""),
                            "quantity": str(claim.get("quantity") or ""),
                            "explanation": "The first supplied fixture passage supports the claim." if isinstance(candidate, dict) else "No candidate passage was supplied.",
                        })
                    return {"claims": rows}

                if stage in {"claim_auditor", "revision_claim_auditor"}:
                    proposed_reply = str(supplied.get("proposed_reply_to_audit") or "")
                    sentences = split_reply_sentences(proposed_reply)
                    return {
                        "actual_factual_claims": [],
                        "sentence_assessments": [{
                            "sentence_text": sentence,
                            "factual_claims": [],
                            "world_claim_checks": {
                                "asserts_actor_state_or_action": False,
                                "asserts_causal_or_predictive_relation": False,
                                "asserts_comparison_or_outcome": False,
                                "asserts_historical_date_or_quantity": False,
                                "asserts_meaning_or_attribution": False,
                                "purely_non_factual": True,
                            },
                        } for sentence in sentences],
                    }

                if stage in {"reviewer", "revision_reviewer"}:
                    claims = (
                        supplied.get("proposer_listed_factual_claims_untrusted", [])
                        if isinstance(supplied, dict)
                        else []
                    )
                    actual_claims = [
                        str(claim.get("claim_text") or "")
                        for claim in claims
                        if isinstance(claim, dict)
                    ] if isinstance(claims, list) else []
                    proposed_reply = str(supplied.get("proposed_reply") or "")
                    direct_question = bool(
                        supplied.get("proposer_direct_factual_question_present", False)
                    )
                    requested_answer_type = str(
                        supplied.get("proposer_requested_answer_type") or "none"
                    )
                    sentences = split_reply_sentences(proposed_reply)
                    sentence_assessments = []
                    remaining_claims = list(actual_claims)
                    for sentence in sentences:
                        sentence_claims = [
                            claim_text
                            for claim_text in remaining_claims
                            if " ".join(claim_text.split()) in sentence
                        ]
                        remaining_claims = [
                            claim_text
                            for claim_text in remaining_claims
                            if claim_text not in sentence_claims
                        ]
                        sentence_assessments.append({
                            "sentence_text": sentence,
                            "classification": (
                                "factual_claim" if sentence_claims else "other_non_factual"
                            ),
                            "factual_claims": sentence_claims,
                            "non_factual_basis": (
                                "none" if sentence_claims else "rhetorical_question"
                            ),
                            "world_claim_checks": {
                                "asserts_actor_state_or_action": bool(sentence_claims),
                                "asserts_causal_or_predictive_relation": False,
                                "asserts_comparison_or_outcome": False,
                                "asserts_historical_date_or_quantity": False,
                                "asserts_meaning_or_attribution": False,
                                "purely_non_factual": not bool(sentence_claims),
                            },
                        })
                    self.fake._current_ai_reply_text = ""
                    return {
                        "verdict": "approve",
                        "summary": "The deterministic integration fixture approves this draft.",
                        "reasons": [],
                        "actual_factual_claims": actual_claims,
                        "unsupported_factual_claims": [],
                        "sentence_assessments": sentence_assessments,
                        "direct_factual_question_present": direct_question,
                        "requested_answer_type": requested_answer_type,
                        "direct_answer_complete": direct_question,
                        "direct_answer_text": sentences[0] if direct_question and sentences else "",
                        "topically_relevant": True,
                        "endorses_unsupported_allegation": False,
                        "contains_unsupported_factual_claims": False,
                        "actor_action_relationship_correct": True,
                        "direction_polarity_correct": True,
                        "dates_quantities_correct": True,
                        "quotation_attribution_correct": True,
                        "original_prose_clearly_not_historical_quotation": True,
                        "mode_and_tone_match": True,
                        "suitable_for_account": True,
                        "revision_instructions": "",
                    }
                return None

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

            def _single_call_response(self) -> dict[str, Any]:
                decisions = self.fake.scenario.setdefault(
                    "openai_reply_decisions", []
                )
                if decisions:
                    decision = decisions.pop(0)
                else:
                    replies = self.fake.scenario.setdefault("openai_replies", [])
                    if not replies:
                        replies = self.fake.scenario.setdefault("grok_replies", [])
                    reply = replies.pop(0) if replies else self.fake.scenario.get(
                        "openai_reply",
                        self.fake.scenario.get(
                            "grok_reply",
                            "Clarity matters.",
                        ),
                    )
                    if str(reply).strip().upper() == "SKIP":
                        decision = {
                            "decision": "no_reply",
                            "reply_kind": "no_reply",
                            "reply": "",
                            "used_fact_ids": [], "factual_claims": [],
                            "reason_code": "no_meaningful_content",
                        }
                    else:
                        decision = {
                            "decision": "reply",
                            "reply_kind": "social",
                            "reply": str(reply),
                            "used_fact_ids": [], "factual_claims": [],
                            "reason_code": "useful_reply",
                        }
                output_text = (
                    decision
                    if isinstance(decision, str)
                    else json.dumps(
                        decision,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                return {
                    "id": "resp_fixture",
                    "status": "completed",
                    "model": "gpt-5.6-sol",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {"type": "output_text", "text": output_text}
                            ],
                        }
                    ],
                    "usage": {
                        "input_tokens": 10,
                        "input_tokens_details": {"cached_tokens": 4},
                        "output_tokens": 8,
                        "output_tokens_details": {"reasoning_tokens": 3},
                        "total_tokens": 18,
                    },
                }

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

            def _pagination_enabled(self, path: str) -> bool:
                if self.fake.scenario.get("enable_pagination"):
                    return True
                return path in set(self.fake.scenario.get("paginated_paths", []))

            def _page_body(
                self,
                path: str,
                items: list[dict[str, Any]],
                query: dict[str, list[str]],
                *,
                extra: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                body: dict[str, Any] = dict(extra or {})
                if not self._pagination_enabled(path):
                    body["data"] = items
                    return body

                try:
                    max_results = int((query.get("max_results") or ["10"])[0])
                except ValueError:
                    max_results = 10
                max_results = max(1, max_results)

                try:
                    start = int((query.get("pagination_token") or ["0"])[0])
                except ValueError:
                    start = 0

                page = items[start : start + max_results]
                body["data"] = page

                next_start = start + max_results
                if next_start < len(items):
                    meta = dict(body.get("meta", {}))
                    meta["next_token"] = str(next_start)
                    body["meta"] = meta
                return body

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
                    account_id = path.split("/")[3]
                    mentions = []
                    if "mention_responses" in self.fake.scenario:
                        responses = self.fake.scenario["mention_responses"]
                        raw_mentions = responses.pop(0) if responses else []
                    else:
                        raw_mentions = self.fake.scenario.get("mentions", [])
                    for raw_mention in raw_mentions:
                        mention = dict(raw_mention)
                        if "entities" not in mention:
                            mention["entities"] = {
                                "mentions": [
                                    {"id": account_id, "username": "MrsMThatcher"}
                                ]
                            }
                        mentions.append(mention)
                    self.fake._remember_tweets(mentions)
                    self._json_response(
                        200,
                        self._page_body(
                            path,
                            self._filter_since(mentions, query),
                            query,
                            extra=self.fake.scenario.get("mentions_extra", {}),
                        ),
                    )
                    return

                if path == "/2/tweets/search/recent":
                    replies = list(self.fake.scenario.get("search_recent", []))
                    extra = self.fake.scenario.get("search_recent_extra", {})
                    search_query = (query.get("query") or [""])[0]
                    if "quotes_of_tweet_id:" in search_query:
                        replies, extra = [], {}
                        for post_id in re.findall(r"quotes_of_tweet_id:(\d+)", search_query):
                            source = self.fake.scenario.get("quote_tweets", {}).get(post_id, {})
                            replies.extend(source.get("data", []))
                            for key, value in source.items():
                                if key == "includes":
                                    for resource, items in value.items():
                                        extra.setdefault("includes", {}).setdefault(resource, []).extend(items)
                                elif key != "data":
                                    extra[key] = value
                        if query.get("sort_order") == ["recency"]:
                            replies.sort(
                                key=lambda item: int(str(item.get("id", "0")))
                                if str(item.get("id", "0")).isdecimal() else 0,
                                reverse=True,
                            )
                    self.fake._remember_tweets(replies)
                    self._json_response(
                        200,
                        self._page_body(
                            path,
                            self._filter_since(replies, query),
                            query,
                            extra=extra,
                        ),
                    )
                    return

                if path.startswith("/2/tweets/") and path.endswith("/quote_tweets"):
                    post_id = path.split("/")[3]
                    data = self.fake.scenario.get("quote_tweets", {}).get(post_id, {})
                    self.fake._remember_tweets(data.get("data", []))
                    body = self._page_body(
                        path,
                        list(data.get("data", [])),
                        query,
                        extra={k: v for k, v in data.items() if k != "data"},
                    )
                    self._json_response(200, body)
                    return

                if path.startswith("/2/tweets/"):
                    tweet_id = path.split("/")[3]
                    tweet = self.fake._known_tweet(tweet_id)
                    self._json_response(200, {"data": tweet} if tweet else {})
                    return

                if path.startswith("/media/"):
                    media = self.fake.scenario.get("media_responses", {}).get(
                        path, {}
                    )
                    status = int(media.get("status", 200))
                    mime_type = str(media.get("content_type", "image/png"))
                    raw = media.get("body", "")
                    if isinstance(raw, str) and raw:
                        payload = raw.encode("latin-1")
                    else:
                        payload = valid_png()
                    self._bytes_response(status, payload, mime_type)
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
                    xai_responses = self.fake.scenario.setdefault("xai_responses", [])
                    if xai_responses:
                        response = xai_responses.pop(0)
                        status = int(response.get("status", 200))
                        if status >= 400:
                            self._json_response(status, response.get("body", {"error": "configured xai failure"}))
                            return
                        self.fake.xai_requests.append(body)
                        self._json_response(status, response.get("body", {}))
                        return
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
                    structured = self._ai_first_payload(body)
                    if structured is not None:
                        self._json_response(
                            200,
                            {
                                "choices": [{"message": {"content": structured}}],
                                "usage": {"total_tokens": 12},
                            },
                        )
                        return
                    replies = self.fake.scenario.setdefault("grok_replies", [])
                    reply = replies.pop(0) if replies else self.fake.scenario.get("grok_reply", "Clarity matters.")
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

                if path == "/v1/responses":
                    body = self._read_json()
                    self._record("POST", path, query, body)
                    self.fake.openai_requests.append(body)
                    responses = self.fake.scenario.setdefault(
                        "openai_responses", []
                    )
                    if responses:
                        response = responses.pop(0)
                        status = int(response.get("status", 200))
                        if status >= 400:
                            self._json_response(
                                status,
                                response.get(
                                    "body",
                                    {"error": "configured OpenAI failure"},
                                ),
                                headers=response.get("headers"),
                            )
                            return
                        self._json_response(status, response.get("body", {}))
                        return
                    if self.fake.scenario.get("openai_non_json"):
                        self._text_response(200, "not json")
                        return
                    status = int(self.fake.scenario.get("openai_status", 200))
                    if status >= 400:
                        self._json_response(
                            status,
                            {"error": "configured OpenAI failure"},
                        )
                        return
                    self._json_response(200, self._single_call_response())
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
