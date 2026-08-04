"""Tests for the shared auth helpers (bearer tokens + HMAC cookies)."""

from __future__ import annotations

from app.auth import bearer_from_request_headers, bearer_matches, load_api_tokens, make_token


def test_make_token_is_stable_and_hex():
    tok = make_token("hello", "secret")
    assert tok == make_token("hello", "secret")
    assert len(tok) == 64
    int(tok, 16)  # valid hex


def test_make_token_differs_by_passphrase_and_secret():
    assert make_token("a", "s") != make_token("b", "s")
    assert make_token("a", "s") != make_token("a", "t")


def test_load_api_tokens_empty_env(monkeypatch):
    monkeypatch.delenv("ARENA_API_TOKENS", raising=False)
    assert load_api_tokens() == []


def test_load_api_tokens_parses_comma_separated(monkeypatch):
    monkeypatch.setenv("ARENA_API_TOKENS", " abc, def ,ghi,")
    assert load_api_tokens() == ["abc", "def", "ghi"]


def test_bearer_from_request_prefers_x_api_token():
    assert bearer_from_request_headers("Bearer AAA", "BBB") == "BBB"


def test_bearer_from_request_reads_authorization_header():
    assert bearer_from_request_headers("Bearer AAA", None) == "AAA"
    assert bearer_from_request_headers("bearer aaa", None) == "aaa"


def test_bearer_from_request_ignores_non_bearer_scheme():
    assert bearer_from_request_headers("Basic dXNlcjpwYXNz", None) is None


def test_bearer_from_request_returns_none_when_missing():
    assert bearer_from_request_headers(None, None) is None
    assert bearer_from_request_headers("", None) is None


def test_bearer_matches_true_for_allowed():
    assert bearer_matches("AAA", ["BBB", "AAA", "CCC"]) is True


def test_bearer_matches_false_for_disallowed():
    assert bearer_matches("ZZZ", ["AAA", "BBB"]) is False


def test_bearer_matches_empty_inputs():
    assert bearer_matches("", ["AAA"]) is False
    assert bearer_matches("AAA", []) is False
    assert bearer_matches("", []) is False
