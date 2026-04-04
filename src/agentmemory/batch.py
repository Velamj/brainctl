"""
brainctl batch — Anthropic Batch API integration for 50% cost reduction.

Use for any non-interactive LLM operations:
- Memory compression (hippocampus)
- Dream hypothesis generation
- Bulk affect classification (LLM-grade)
- Memory quality audits
- Report generation

Batch requests are processed within 24 hours (usually minutes).
"""

import json
import os
import time
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(os.environ.get("BRAIN_DB", str(Path.home() / "agentmemory" / "db" / "brain.db")))


def get_client():
    """Get Anthropic client. Raises ImportError with instructions if not installed."""
    try:
        import anthropic
        return anthropic.Anthropic()
    except ImportError:
        raise ImportError("pip install anthropic  — required for batch processing")


# =============================================================================
# BATCH SUBMISSION
# =============================================================================

def submit_batch(
    requests: list[dict],
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 2048,
    system: Optional[str] = None,
) -> str:
    """
    Submit a batch of prompts to Anthropic's Batch API at 50% cost.

    Args:
        requests: list of {"custom_id": str, "prompt": str} dicts
        model: model to use (default: sonnet for cost efficiency)
        max_tokens: max response tokens per request
        system: optional system prompt applied to all requests

    Returns:
        batch_id: str — use to check status and retrieve results
    """
    client = get_client()

    batch_requests = []
    for req in requests:
        messages = [{"role": "user", "content": req["prompt"]}]
        params = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            params["system"] = system
        batch_requests.append({
            "custom_id": req["custom_id"],
            "params": params,
        })

    batch = client.messages.batches.create(requests=batch_requests)
    return batch.id


def check_batch(batch_id: str) -> dict:
    """Check batch status. Returns {status, counts}."""
    client = get_client()
    status = client.messages.batches.retrieve(batch_id)
    return {
        "id": batch_id,
        "status": status.processing_status,
        "counts": {
            "processing": status.request_counts.processing,
            "succeeded": status.request_counts.succeeded,
            "errored": status.request_counts.errored,
            "canceled": status.request_counts.canceled,
            "expired": status.request_counts.expired,
        },
    }


def get_results(batch_id: str) -> list[dict]:
    """Get results from a completed batch. Returns list of {custom_id, text, error}."""
    client = get_client()
    results = []
    for result in client.messages.batches.results(batch_id):
        if result.result.type == "succeeded":
            text = result.result.message.content[0].text
            results.append({"custom_id": result.custom_id, "text": text, "error": None})
        else:
            results.append({"custom_id": result.custom_id, "text": None,
                            "error": str(getattr(result.result, "error", "unknown"))})
    return results


def submit_and_wait(
    requests: list[dict],
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 2048,
    system: Optional[str] = None,
    poll_interval: int = 10,
    timeout: int = 3600,
) -> list[dict]:
    """
    Submit batch and block until results are ready.

    Args:
        requests: list of {"custom_id": str, "prompt": str}
        poll_interval: seconds between status checks
        timeout: max seconds to wait

    Returns:
        list of {"custom_id": str, "text": str, "error": str|None}
    """
    batch_id = submit_batch(requests, model=model, max_tokens=max_tokens, system=system)

    start = time.time()
    while time.time() - start < timeout:
        status = check_batch(batch_id)
        if status["status"] == "ended":
            return get_results(batch_id)
        time.sleep(poll_interval)

    raise TimeoutError(f"Batch {batch_id} did not complete within {timeout}s")


# =============================================================================
# HIPPOCAMPUS INTEGRATION — Batch compression
# =============================================================================

COMPRESS_SYSTEM = """You are a memory consolidation engine. You merge redundant 
and overlapping memories into concise, high-signal summaries. Preserve all unique 
facts. Remove duplicated information. Output a JSON array of strings — each string 
is one consolidated memory. Be concise but preserve specificity."""


def batch_compress_memories(scope_groups: dict[str, list[dict]], model: str = "claude-sonnet-4-6") -> str:
    """
    Submit memory compression tasks as a batch at 50% cost.

    Args:
        scope_groups: {scope: [memory_dicts]} — groups of memories to compress
        model: model to use

    Returns:
        batch_id — poll with check_batch(), retrieve with get_results()
    """
    requests = []
    for scope, memories in scope_groups.items():
        max_output = max(1, len(memories) // 3)
        prompt = (
            f"Scope: {scope}\n"
            f"Compress these {len(memories)} memories into at most {max_output} consolidated memories.\n"
            f"Preserve all unique facts. Return a JSON array of strings.\n\n"
            f"Memories:\n{json.dumps(memories, indent=2)}"
        )
        requests.append({
            "custom_id": f"compress:{scope}",
            "prompt": prompt,
        })

    return submit_batch(requests, model=model, max_tokens=4096, system=COMPRESS_SYSTEM)


# =============================================================================
# DREAM INTEGRATION — Batch hypothesis generation
# =============================================================================

DREAM_SYSTEM = """You are a creative synthesis engine. Given two memories from 
different domains, generate a hypothesis about a non-obvious connection between 
them. Be specific and actionable. Output a single sentence hypothesis."""


def batch_dream_hypotheses(pairs: list[tuple[dict, dict]], model: str = "claude-sonnet-4-6") -> str:
    """
    Submit dream hypothesis generation as a batch.

    Args:
        pairs: list of (memory_a, memory_b) tuples from different scopes

    Returns:
        batch_id
    """
    requests = []
    for i, (a, b) in enumerate(pairs):
        prompt = (
            f"Memory A (scope: {a.get('scope', '?')}):\n{a.get('content', '')}\n\n"
            f"Memory B (scope: {b.get('scope', '?')}):\n{b.get('content', '')}\n\n"
            f"What non-obvious connection or insight links these two memories?"
        )
        requests.append({
            "custom_id": f"dream:{a.get('id', i)}:{b.get('id', i)}",
            "prompt": prompt,
        })

    return submit_batch(requests, model=model, max_tokens=512, system=DREAM_SYSTEM)


# =============================================================================
# QUALITY AUDIT — Batch memory linting
# =============================================================================

AUDIT_SYSTEM = """You are a memory quality auditor. Evaluate the given memory for:
1. Accuracy — does it contain contradictions or obviously wrong claims?
2. Relevance — is this still useful or is it stale?
3. Clarity — is it clearly written or ambiguous?
4. Redundancy — does it overlap with the other memories shown?

Output JSON: {"score": 0-10, "issues": ["issue1", ...], "recommendation": "keep|revise|retire"}"""


def batch_audit_memories(memories: list[dict], model: str = "claude-sonnet-4-6") -> str:
    """
    Submit memory quality audit as a batch.

    Args:
        memories: list of memory dicts with at least {id, content, category, confidence}

    Returns:
        batch_id
    """
    # Send memories in groups of 10 for cross-reference
    requests = []
    for i in range(0, len(memories), 10):
        group = memories[i:i + 10]
        prompt = "Audit each of these memories:\n\n"
        for m in group:
            prompt += f"[ID {m['id']}] ({m.get('category', '?')}, conf={m.get('confidence', '?')}) {m['content']}\n\n"
        prompt += "Return a JSON array with one audit object per memory."
        requests.append({
            "custom_id": f"audit:group-{i}",
            "prompt": prompt,
        })

    return submit_batch(requests, model=model, max_tokens=4096, system=AUDIT_SYSTEM)


# =============================================================================
# CLI COMMANDS
# =============================================================================

def cmd_batch_submit(args):
    """Submit a batch from a prompts file (one prompt per line)."""
    prompts_file = args.file
    model = getattr(args, "model", "claude-sonnet-4-6")

    requests = []
    with open(prompts_file) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            requests.append({"custom_id": f"task-{i}", "prompt": line})

    if not requests:
        print(json.dumps({"error": "No prompts found in file"}))
        return

    batch_id = submit_batch(requests, model=model)
    print(json.dumps({"ok": True, "batch_id": batch_id, "tasks": len(requests)}))


def cmd_batch_status(args):
    """Check batch status."""
    result = check_batch(args.batch_id)
    print(json.dumps(result, indent=2))


def cmd_batch_results(args):
    """Get batch results."""
    results = get_results(args.batch_id)
    if getattr(args, "output", None) == "jsonl":
        for r in results:
            print(json.dumps(r))
    else:
        print(json.dumps(results, indent=2))


def cmd_batch_compress(args):
    """Submit hippocampus compression as a batch (50% cost)."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    # Find scopes with 10+ active memories
    scope_rows = db.execute("""
        SELECT scope, COUNT(*) as cnt FROM memories 
        WHERE retired_at IS NULL 
        GROUP BY scope HAVING cnt >= 10
        ORDER BY cnt DESC
    """).fetchall()

    if not scope_rows:
        print(json.dumps({"ok": True, "message": "No scopes need compression"}))
        return

    scope_groups = {}
    for row in scope_rows:
        memories = db.execute(
            "SELECT id, category, scope, content, confidence, temporal_class "
            "FROM memories WHERE retired_at IS NULL AND scope = ? "
            "ORDER BY confidence DESC",
            (row["scope"],)
        ).fetchall()
        scope_groups[row["scope"]] = [dict(m) for m in memories]

    model = getattr(args, "model", "claude-sonnet-4-6")
    batch_id = batch_compress_memories(scope_groups, model=model)
    print(json.dumps({
        "ok": True,
        "batch_id": batch_id,
        "scopes": len(scope_groups),
        "total_memories": sum(len(v) for v in scope_groups.values()),
        "hint": f"Check status: brainctl batch status {batch_id}",
    }))
    db.close()


def cmd_batch_audit(args):
    """Submit memory quality audit as a batch (50% cost)."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    memories = db.execute(
        "SELECT id, content, category, confidence FROM memories "
        "WHERE retired_at IS NULL ORDER BY confidence ASC LIMIT ?",
        (getattr(args, "limit", 100),)
    ).fetchall()

    if not memories:
        print(json.dumps({"ok": True, "message": "No memories to audit"}))
        return

    model = getattr(args, "model", "claude-sonnet-4-6")
    batch_id = batch_audit_memories([dict(m) for m in memories], model=model)
    print(json.dumps({
        "ok": True,
        "batch_id": batch_id,
        "memories_audited": len(memories),
        "hint": f"Check status: brainctl batch status {batch_id}",
    }))
    db.close()
