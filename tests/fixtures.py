"""Smallest session store each backend will accept, built in a temp directory.

Written from the real shapes rather than the documented ones, since these
formats are nobody's public interface and the only contract is what the agents
actually write.
"""

import json
import os
import sqlite3


def write(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")
    return path


def claude(root, session, cwd="/tmp/work"):
    return write(os.path.join(root, "projects", "-tmp-work", session + ".jsonl"), [
        {"type": "attachment", "cwd": cwd},
        {"type": "ai-title", "aiTitle": "a claude session", "sessionId": session},
        {"type": "assistant", "message": {
            "model": "claude-sonnet-5",
            "usage": {"input_tokens": 10, "output_tokens": 2,
                      "cache_read_input_tokens": 100,
                      "cache_creation_input_tokens": 5}}},
        # a nested count belongs to the subagent a tool ran, not to this turn
        {"type": "user", "toolUseResult": {"usage": {"input_tokens": 999}}},
        {"type": "assistant", "message": {
            "model": "<synthetic>",
            "usage": {"input_tokens": 7, "output_tokens": 3}}},
    ])


def pi(root, session, cwd="/tmp/work"):
    return write(os.path.join(root, "sessions", "--tmp-work--",
                              "2026-09-05T00-00-00-000Z_" + session + ".jsonl"), [
        {"type": "session", "cwd": cwd},
        {"type": "message", "message": {"role": "user",
                                        "content": [{"type": "text", "text": "a pi prompt"}]}},
        {"type": "model_change", "provider": "9router", "modelId": "mimo"},
        {"type": "turn", "usage": {"input": 4, "output": 1, "cost": 0.25}},
        {"type": "message", "message": {
            "role": "assistant", "content": [],
            "usage": {"input": 9, "output": 3, "cacheRead": 50, "cost": 0.5}}},
    ])


def commandcode(root, session, cwd="/tmp/work"):
    return write(os.path.join(root, "projects", "tmp-work", session + ".jsonl"), [
        {"type": "session", "cwd": cwd},
        {"type": "message", "message": {"role": "user",
                                        "content": [{"type": "text", "text": "a cmd prompt"}]}},
        {"type": "model_change", "model": "poolside/laguna"},
        {"type": "turn", "usage": {"inputTokens": 12, "outputTokens": 4,
                                   "cacheReadTokens": 60, "costUsd": 1.5}},
        {"type": "message", "message": {"role": "assistant", "content": []}},
    ])


def codex(root, session, cwd="/tmp/work"):
    name = "rollout-2026-09-05T00-00-00-" + session + ".jsonl"
    return write(os.path.join(root, "sessions", "2026", "09", "05", name), [
        {"type": "session_meta", "payload": {"cwd": cwd}},
        {"type": "response_item",
         "payload": {"role": "user", "content": [{"text": "<environment_context>ignore me"}]}},
        {"type": "response_item",
         "payload": {"role": "user", "content": [{"text": "a codex prompt"}]}},
        {"type": "turn_context", "payload": {"model": "gpt-5.6-terra"}},
        {"type": "event_msg", "payload": {"info": {"total_token_usage": {
            "input_tokens": 100, "output_tokens": 10,
            "cached_input_tokens": 80, "reasoning_output_tokens": 1}}}},
        {"type": "event_msg", "payload": {"info": {"total_token_usage": {
            "input_tokens": 250, "output_tokens": 25,
            "cached_input_tokens": 200, "reasoning_output_tokens": 4}}}},
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    ])


def opencode(root, session, cwd="/tmp/work"):
    path = os.path.join(root, "opencode.db")
    os.makedirs(root, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE session (id TEXT, directory TEXT, title TEXT, model TEXT,
                              time_updated INTEGER);
        CREATE TABLE message (id TEXT, session_id TEXT, data TEXT,
                              time_created INTEGER);
        CREATE TABLE part (id TEXT, session_id TEXT, data TEXT);
    """)
    db.execute("INSERT INTO session VALUES (?,?,?,?,?)",
               (session, cwd, "an opencode session",
                json.dumps({"id": "mimo", "providerID": "9router"}), 1788565000000))
    db.execute("INSERT INTO message VALUES (?,?,?,?)",
               ("m1", session, json.dumps({"role": "assistant", "finish": "stop"}), 1))
    db.commit()
    db.close()
    return path


def hermes(root, session, cwd="/tmp/work"):
    path = os.path.join(root, "state.db")
    os.makedirs(root, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE sessions (id TEXT, cwd TEXT, title TEXT, model TEXT,
                               last_activity_at REAL, ended_at REAL,
                               input_tokens INT, output_tokens INT,
                               reasoning_tokens INT, api_call_count INT,
                               estimated_cost_usd REAL, billing_provider TEXT,
                               message_count INT, tool_call_count INT);
        CREATE TABLE messages (session_id TEXT, role TEXT, finish_reason TEXT,
                               timestamp REAL, content TEXT, tool_calls TEXT);
    """)
    db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (session, cwd, "a hermes session", "nemotron-3.5", 1788565000.0,
                None, 100, 20, 5, 2, 0.01, "opencode-free", 4, 0))
    db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)",
               (session, "assistant", "stop", 1788565000.0, "hi", ""))
    db.commit()
    db.close()
    return path


def copilot(root, session, cwd="/tmp/work"):
    ws = os.path.join(root, "workspaceStorage", "abc123")
    os.makedirs(ws, exist_ok=True)
    with open(os.path.join(ws, "workspace.json"), "w") as handle:
        json.dump({"folder": "file://" + cwd}, handle)
    return write(os.path.join(ws, "chatSessions", session + ".jsonl"), [
        {"kind": 0, "v": {"version": 3, "sessionId": session,
                          "inputState": {"selectedModel":
                                         {"metadata": {"family": "claude-fable-5.1"}}}}},
        {"kind": 1, "k": ["customTitle"], "v": "a copilot chat"},
        {"kind": 2, "k": ["requests"], "v": [{"timestamp": 1788565000000}]},
        {"kind": 1, "k": ["requests", 0, "promptTokens"], "v": 1234},
        {"kind": 1, "k": ["requests", 0, "completionTokens"], "v": 56},
        {"kind": 1, "k": ["requests", 0, "copilotCredits"], "v": 0.5},
        {"kind": 1, "k": ["requests", 0, "modelState"], "v": {"completedAt": 1}},
        {"kind": 1, "k": ["requests", 0, "response"], "v": []},
    ])


def _field(index, payload):
    """One length delimited protobuf field, which is all these tests need."""
    return bytes([index << 3 | 2, len(payload)]) + payload


def antigravity(root, session, cwd="/tmp/work"):
    path = os.path.join(root, "conversations", session + ".db")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE trajectory_metadata_blob (id INT, data BLOB);
        CREATE TABLE gen_metadata (idx INT, data BLOB, size INT);
        CREATE TABLE executor_metadata (idx INT, data BLOB);
        CREATE TABLE steps (idx INT, step_type INT, status INT, metadata BLOB);
    """)
    db.execute("INSERT INTO trajectory_metadata_blob VALUES (1, ?)",
               (_field(1, ("file://" + cwd).encode()) + b"\x12\x04next",))
    db.execute("INSERT INTO gen_metadata VALUES (1, ?, 0)",
               (b"junk<USER_REQUEST>\n  an agy prompt\n</USER_REQUEST>more",))
    # the large blob also holds whatever the session was reading, which is why
    # the model is not taken from here
    db.execute("INSERT INTO gen_metadata VALUES (2, ?, 0)",
               (b"the repo mentions claude-opus-5 in its README",))
    db.execute("INSERT INTO executor_metadata VALUES (1, ?)",
               (b"\x08\x04gemini-3.6-flash-medium\x10\x02",))
    db.commit()
    db.close()
    return path
