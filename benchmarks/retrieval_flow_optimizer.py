from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable


_WORD_RE = re.compile(r"[a-z0-9]+")
_SOURCE_NUM_SUFFIX_RE = re.compile(r"^(.+?)[_-](\d+)$")
_SESSION_RE = re.compile(
    r"(?:^|[|_\s-])(?:sid|session|s)[=_-]?(\d+)|\bsession[_\s-]*(\d+)\b",
    re.IGNORECASE,
)
_GROUP_SESSION_RE = re.compile(r"(?:^|[|_\s-])s[=_-]?(\d+)(?:[|_\s-]|$)", re.IGNORECASE)
_DATE_RE = re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")

_SYNONYMS = {
    "dad": {"father", "parent"},
    "father": {"dad", "parent"},
    "mom": {"mother", "parent"},
    "mother": {"mom", "parent"},
    "workplace": {"work", "works", "job", "office", "occupation", "position"},
    "occupation": {"job", "work", "works", "position", "career"},
    "position": {"job", "occupation", "work", "works", "role"},
    "educational": {"education", "degree", "school", "background"},
    "education": {"educational", "degree", "school", "background"},
    "background": {"education", "degree", "school"},
    "degree": {"education", "educational", "school", "background"},
    "location": {"where", "place", "city", "hometown", "workplace"},
    "hometown": {"home", "city", "location", "from"},
    "company": {"business", "workplace", "employer"},
    "coworker": {"colleague", "work", "works"},
    "hobby": {"enjoy", "enjoys", "love", "loves", "passion", "passionate", "into"},
    "enjoy": {"hobby", "likes", "love", "loves", "passion"},
    "enjoys": {"hobby", "likes", "love", "loves", "passion"},
    "loves": {"hobby", "enjoy", "enjoys", "passion", "passionate"},
    "passionate": {"hobby", "enjoy", "enjoys", "loves"},
    "boss": {"manager", "supervisor"},
    "subordinate": {"employee", "report", "teammate"},
    "aunt": {"relative"},
    "uncle": {"relative"},
    "cousin": {"relative"},
    "living": {"occupation", "job", "work", "works"},
    "email": {"contact", "address"},
    "contact": {"phone", "number", "email"},
    "number": {"phone", "contact"},
}

_RELATION_TERMS = {
    "father", "dad", "mother", "mom", "coworker", "colleague", "niece", "nephew",
    "sister", "brother", "friend", "wife", "husband", "neighbor", "parent",
    "boss", "manager", "supervisor", "subordinate", "employee", "report",
    "aunt", "uncle", "cousin", "relative",
}
_ATTRIBUTE_TERMS = {
    "education", "educational", "background", "degree", "school", "occupation",
    "position", "job", "workplace", "works", "work", "location", "hometown",
    "company", "hobby", "city", "employer", "role", "enjoy", "enjoys",
    "love", "loves", "likes", "passion", "passionate", "into",
    "email", "address", "contact", "number", "phone", "living",
}


@dataclass(slots=True)
class FlowOperators:
    single_fact: bool = True
    temporal: bool = False
    set_coverage: bool = False
    comparison: bool = False
    update_resolution: bool = False
    multi_session: bool = False
    role_fact: bool = False

    def as_list(self) -> list[str]:
        return [name for name in self.__dataclass_fields__ if getattr(self, name)]

    @property
    def needs_breadth(self) -> bool:
        return self.temporal or self.set_coverage or self.comparison or self.update_resolution or self.multi_session


@dataclass(slots=True)
class FlowCandidate:
    rowid: int | None
    doc_id: str
    content: str
    base_score: float = 0.0
    channels: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    features: dict[str, float] = field(default_factory=dict)


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _expanded_tokens(text: str) -> set[str]:
    tokens = set(_tokens(text))
    expanded = set(tokens)
    for token in tokens:
        expanded.update(_SYNONYMS.get(token, ()))
    return expanded


def _informative(tokens: Iterable[str]) -> set[str]:
    stop = {
        "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for", "from",
        "has", "have", "how", "i", "in", "is", "it", "my", "of", "on", "or", "that",
        "the", "this", "to", "what", "when", "where", "which", "who", "with",
    }
    return {token for token in tokens if len(token) > 2 and token not in stop}


def detect_flow_operators(query: str) -> FlowOperators:
    q = (query or "").lower()
    temporal = bool(re.search(r"\b(before|after|latest|current|currently|recent|previous|earlier|later|when|date|today|yesterday|last|next|during)\b", q))
    set_coverage = bool(re.search(r"\b(all|both|each|every|across|list|how many|how much|total|combined|what .* have|which .*s)\b", q))
    comparison = bool(re.search(r"\b(compare|versus|vs|difference|different|more|less|which|locations|authors)\b", q))
    update_resolution = bool(re.search(r"\b(current|currently|now|latest|new|updated|changed|formerly|previously|still)\b", q))
    role_fact = bool(_RELATION_TERMS & set(_tokens(q))) and bool(_ATTRIBUTE_TERMS & _expanded_tokens(q))
    multi_session = set_coverage or temporal or bool(re.search(r"\b(sessions?|events?|projects?|activities|books|games|concerts?)\b", q))
    single_fact = not (set_coverage or comparison or multi_session) or role_fact
    return FlowOperators(
        single_fact=single_fact,
        temporal=temporal,
        set_coverage=set_coverage,
        comparison=comparison,
        update_resolution=update_resolution,
        multi_session=multi_session,
        role_fact=role_fact,
    )


def source_family(doc_id: str) -> str:
    head = str(doc_id).split("|", 1)[0]
    match = _SOURCE_NUM_SUFFIX_RE.match(head)
    return match.group(1) if match else head


def source_session(doc_id: str, content: str = "") -> str:
    raw = f"{doc_id} {content}"
    match = _SESSION_RE.search(raw)
    if not match:
        return ""
    return match.group(1) or match.group(2) or ""


def source_group_session(doc_id: str) -> str:
    match = _GROUP_SESSION_RE.search(str(doc_id))
    return match.group(1) if match else ""


def _session_num(candidate: FlowCandidate) -> int | None:
    session = source_session(candidate.doc_id, candidate.content)
    if not session.isdigit():
        return None
    return int(session)


def _candidate_facets(candidate: FlowCandidate) -> set[str]:
    content_tokens = _expanded_tokens(candidate.content)
    facets = {f"family:{source_family(candidate.doc_id)}"}
    session = source_session(candidate.doc_id, candidate.content)
    if session:
        facets.add(f"session:{session}")
    for token in sorted((_RELATION_TERMS | _ATTRIBUTE_TERMS) & content_tokens):
        facets.add(f"field:{token}")
    for match in _DATE_RE.finditer(candidate.content):
        facets.add(f"date:{match.group(0)}")
    return facets


def _role_value_pattern(text: str) -> bool:
    return bool(
        re.search(
            r"\b("
            r"works?\s+(?:as|in|at)|"
            r"is\s+(?:a|an|the)\b|"
            r"loves?\b|likes?\b|enjoys?\b|"
            r"passionate\s+about|really\s+into|free\s+time|"
            r"originally\s+from|grew\s+up\s+in|hails?\s+from|from\s+[A-Z][A-Za-z]+,\s*[A-Z][A-Za-z]+|"
            r"[\w.+-]+@[\w.-]+|"
            r"(?:phone|contact|number|email)\s+(?:is|address\s+is|number\s+is)?|"
            r"company\s+(?:is|called|named)"
            r")",
            text or "",
            re.IGNORECASE,
        )
    )


def _base_relevance(query: str, operators: FlowOperators, candidate: FlowCandidate, max_base: float) -> tuple[float, dict[str, float]]:
    q_tokens = _expanded_tokens(query)
    c_tokens = _expanded_tokens(candidate.content)
    q_info = _informative(q_tokens)
    c_info = _informative(c_tokens)
    overlap = len(q_info & c_info) / max(len(q_info), 1)
    dice = (2.0 * len(q_info & c_info) / max(len(q_info) + len(c_info), 1)) if c_info else 0.0
    base_norm = candidate.base_score / max(max_base, 1e-9)
    relation_match = 1.0 if (_RELATION_TERMS & q_tokens & c_tokens) else 0.0
    attribute_match = 1.0 if ((_ATTRIBUTE_TERMS & _expanded_tokens(query)) & c_tokens) else 0.0
    value_pattern = 1.0 if operators.role_fact and relation_match and _role_value_pattern(candidate.content) else 0.0
    exact_phrase = 1.0 if len(query) >= 8 and query.lower() in candidate.content.lower() else 0.0
    temporal_match = 1.0 if operators.temporal and (_DATE_RE.search(candidate.content) or source_session(candidate.doc_id, candidate.content)) else 0.0
    field_score = 0.0
    if operators.role_fact:
        field_score = 0.20 * relation_match + 0.16 * attribute_match + 0.16 * value_pattern
    channel_bonus = 0.0
    if "field" in candidate.channels:
        channel_bonus += 0.18
    if "lexical" in candidate.channels:
        channel_bonus += 0.08
    if "fallback" in candidate.channels:
        channel_bonus += 0.04
    score = (
        0.35 * base_norm
        + 0.30 * overlap
        + 0.13 * dice
        + 0.04 * exact_phrase
        + 0.05 * temporal_match
        + field_score
        + channel_bonus
    )
    features = {
        "base_norm": round(base_norm, 6),
        "overlap": round(overlap, 6),
        "dice": round(dice, 6),
        "relation_match": relation_match,
        "attribute_match": attribute_match,
        "value_pattern": value_pattern,
        "exact_phrase": exact_phrase,
        "temporal_match": temporal_match,
        "field_score": round(field_score, 6),
        "channel_bonus": round(channel_bonus, 6),
    }
    return score, features


def _lexical_fallback_candidates(
    query: str,
    all_docs: dict[str, tuple[int, str]],
    *,
    limit: int,
    channel: str,
) -> list[FlowCandidate]:
    q_info = _informative(_expanded_tokens(query))
    q_all = _expanded_tokens(query)
    scored: list[tuple[float, str, int, str]] = []
    for doc_id, (rowid, text) in all_docs.items():
        c_tokens = _expanded_tokens(text)
        c_info = _informative(c_tokens)
        overlap = len(q_info & c_info) / max(len(q_info), 1)
        relation = 1.0 if (_RELATION_TERMS & q_all & c_tokens) else 0.0
        attribute = 1.0 if ((_ATTRIBUTE_TERMS & q_all) & c_tokens) else 0.0
        value_pattern = 1.0 if relation and _role_value_pattern(text) else 0.0
        phrase = 1.0 if len(query) >= 8 and query.lower() in text.lower() else 0.0
        score = overlap + 0.42 * relation + 0.35 * attribute + 0.30 * value_pattern + 0.25 * phrase
        if score > 0:
            scored.append((score, doc_id, rowid, text))
    scored.sort(reverse=True, key=lambda item: item[0])
    return [
        FlowCandidate(rowid=rowid, doc_id=doc_id, content=text, base_score=score, channels={channel})
        for score, doc_id, rowid, text in scored[:limit]
    ]


def _expand_related_candidates(
    seeds: list[FlowCandidate],
    all_docs: dict[str, tuple[int, str]],
    operators: FlowOperators,
    *,
    limit: int,
) -> list[FlowCandidate]:
    families = {source_family(candidate.doc_id) for candidate in seeds[:12]}
    sessions = {
        int(session)
        for candidate in seeds[:12]
        for session in [source_session(candidate.doc_id, candidate.content)]
        if session.isdigit()
    }
    out: list[FlowCandidate] = []
    seen = {candidate.doc_id for candidate in seeds}
    for doc_id, (rowid, text) in all_docs.items():
        if doc_id in seen:
            continue
        family_hit = source_family(doc_id) in families and operators.needs_breadth
        session = source_session(doc_id, text)
        neighbor_hit = False
        if operators.temporal and session.isdigit():
            num = int(session)
            neighbor_hit = any(abs(num - seed_num) <= 1 for seed_num in sessions)
        if family_hit or neighbor_hit:
            channels = {"family"} if family_hit else set()
            if neighbor_hit:
                channels.add("temporal_neighbor")
            out.append(FlowCandidate(rowid=rowid, doc_id=doc_id, content=text, base_score=0.01, channels=channels))
            if len(out) >= limit:
                break
    return out


def optimize_ranked_documents(
    query: str,
    retrieved_rows: list[dict[str, Any]],
    rowid_to_doc_id: dict[int, str],
    rowid_to_text: dict[int, str],
    *,
    top_k: int,
) -> tuple[list[str], dict[str, Any]]:
    """Union retrieval channels and build a top-k list from generic evidence features."""

    operators = detect_flow_operators(query)
    all_docs = {
        doc_id: (rowid, rowid_to_text.get(rowid, ""))
        for rowid, doc_id in rowid_to_doc_id.items()
    }
    by_doc: dict[str, FlowCandidate] = {}
    for row in retrieved_rows:
        try:
            rowid = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        doc_id = rowid_to_doc_id.get(rowid)
        if not doc_id:
            continue
        score = float(row.get("final_score") or row.get("rrf_score") or row.get("retrieval_score") or 0.0)
        by_doc[doc_id] = FlowCandidate(
            rowid=rowid,
            doc_id=doc_id,
            content=rowid_to_text.get(rowid, str(row.get("content") or "")),
            base_score=score,
            channels={"fts_vec"},
            metadata={"row": row},
        )

    fallback_limit = max(top_k * 6, 30)
    fallback_channel = "field" if operators.role_fact else "lexical"
    for candidate in _lexical_fallback_candidates(query, all_docs, limit=fallback_limit, channel=fallback_channel):
        existing = by_doc.get(candidate.doc_id)
        if existing:
            existing.channels.update(candidate.channels)
            existing.base_score = max(existing.base_score, candidate.base_score)
        else:
            by_doc[candidate.doc_id] = candidate

    seed_candidates = sorted(by_doc.values(), key=lambda item: item.base_score, reverse=True)
    if operators.needs_breadth:
        for candidate in _expand_related_candidates(seed_candidates, all_docs, operators, limit=max(top_k * 4, 20)):
            existing = by_doc.get(candidate.doc_id)
            if existing:
                existing.channels.update(candidate.channels)
                existing.base_score = max(existing.base_score, candidate.base_score)
            else:
                by_doc[candidate.doc_id] = candidate
        retrieved_families = {
            source_family(candidate.doc_id)
            for candidate in by_doc.values()
            if "fts_vec" in candidate.channels
        }
        for candidate in by_doc.values():
            if "fts_vec" not in candidate.channels and source_family(candidate.doc_id) in retrieved_families:
                candidate.channels.add("family")

    candidates = list(by_doc.values())
    max_base = max((candidate.base_score for candidate in candidates), default=1.0)
    for candidate in candidates:
        candidate.score, candidate.features = _base_relevance(query, operators, candidate, max_base)

    session_nums = [num for candidate in candidates for num in [_session_num(candidate)] if num is not None]
    min_session = min(session_nums, default=0)
    max_session = max(session_nums, default=0)
    if operators.temporal or operators.update_resolution:
        wants_latest = bool(re.search(r"\b(current|currently|now|latest|new|updated|changed|recent|most recent|after)\b", query.lower()))
        wants_earlier = bool(re.search(r"\b(before|previous|previously|earlier|former|formerly)\b", query.lower()))
        span = max(max_session - min_session, 1)
        for candidate in candidates:
            num = _session_num(candidate)
            if num is None:
                continue
            normalized = (num - min_session) / span
            recency_bonus = 0.0
            if wants_latest or operators.update_resolution:
                recency_bonus += 0.12 * normalized
            if wants_earlier:
                recency_bonus += 0.08 * (1.0 - normalized)
            text = candidate.content.lower()
            if operators.update_resolution and re.search(r"\b(current|currently|now|latest|updated|changed|new)\b", text):
                recency_bonus += 0.05
            if operators.update_resolution and re.search(r"\b(previous|previously|former|formerly|old|outdated)\b", text):
                recency_bonus -= 0.05
            candidate.score += recency_bonus
            candidate.features["temporal_recency_bonus"] = round(recency_bonus, 6)

    if operators.role_fact:
        query_roles = _RELATION_TERMS & _expanded_tokens(query)
        role_groups = {
            source_group_session(doc_id)
            for doc_id, (_rowid, text) in all_docs.items()
            if source_group_session(doc_id)
            and query_roles
            and query_roles & _expanded_tokens(text)
        }
        for candidate in candidates:
            group = source_group_session(candidate.doc_id)
            coref_bonus = 0.0
            cand_tokens = _expanded_tokens(candidate.content)
            direct_relation = bool(query_roles & cand_tokens)
            has_attribute = bool((_ATTRIBUTE_TERMS & _expanded_tokens(query)) & cand_tokens)
            has_value = _role_value_pattern(candidate.content)
            if (
                group
                and group in role_groups
                and has_value
                and not direct_relation
            ):
                coref_bonus = 0.50
            if coref_bonus:
                candidate.score += coref_bonus
                candidate.features["role_coref_group_bonus"] = round(coref_bonus, 6)
            elif direct_relation and has_value:
                candidate.score += 0.35
                candidate.features["role_direct_value_bonus"] = 0.35
            elif query_roles and not direct_relation:
                candidate.score -= 0.33
                candidate.features["role_mismatch_penalty"] = 0.33
            elif direct_relation and not has_value and not has_attribute:
                candidate.score -= 0.28
                candidate.features["role_intro_penalty"] = 0.28

    if not candidates:
        return [], {
            "operators": operators.as_list(),
            "candidate_counts": {"fts_vec": 0, "lexical": 0, "field": 0, "family": 0},
            "fallback_used": True,
            "selected": [],
        }

    selected: list[FlowCandidate] = []
    selected_facets: set[str] = set()
    selected_families: set[str] = set()
    selected_sessions: set[str] = set()
    query_terms = _informative(_expanded_tokens(query))
    selected_query_terms: set[str] = set()
    pool = sorted(candidates, key=lambda item: item.score, reverse=True)
    while pool and len(selected) < top_k:
        best_index = 0
        best_gain = -1e9
        for index, candidate in enumerate(pool):
            facets = _candidate_facets(candidate)
            family = source_family(candidate.doc_id)
            session = source_session(candidate.doc_id, candidate.content)
            candidate_query_terms = _informative(_expanded_tokens(candidate.content)) & query_terms
            uncovered_query_terms = candidate_query_terms - selected_query_terms
            new_facets = facets - selected_facets
            gain = candidate.score
            if operators.needs_breadth:
                gain += min(0.28, 0.045 * len(new_facets))
                gain += min(0.24, 0.08 * len(uncovered_query_terms))
                if family not in selected_families:
                    gain += 0.055
                elif "family" in candidate.channels and len(selected) < max(5, top_k):
                    # Same source-family siblings are useful when the query asks
                    # for a set; plain duplicates from the same session are not.
                    gain += 0.16
                if session and session not in selected_sessions:
                    gain += 0.08
                elif session:
                    gain -= 0.12
                if not candidate_query_terms and "family" not in candidate.channels:
                    gain -= 0.16
                if not uncovered_query_terms and session in selected_sessions:
                    gain -= 0.06
            elif operators.role_fact:
                # Single fact retrieval should stay precision-first.
                if family in selected_families:
                    gain -= 0.04
            if "temporal_neighbor" in candidate.channels and operators.temporal:
                gain += 0.035
            if gain > best_gain:
                best_gain = gain
                best_index = index
        item = pool.pop(best_index)
        selected.append(item)
        selected_facets.update(_candidate_facets(item))
        selected_families.add(source_family(item.doc_id))
        selected_query_terms.update(_informative(_expanded_tokens(item.content)) & query_terms)
        session = source_session(item.doc_id, item.content)
        if session:
            selected_sessions.add(session)

    channel_counts: dict[str, int] = {}
    for candidate in candidates:
        for channel in candidate.channels:
            channel_counts[channel] = channel_counts.get(channel, 0) + 1
    trace = {
        "operators": operators.as_list(),
        "candidate_counts": channel_counts,
        "fallback_used": "fts_vec" not in channel_counts or len(retrieved_rows) < top_k,
        "selected": [
            {
                "doc_id": candidate.doc_id,
                "score": round(candidate.score, 6),
                "channels": sorted(candidate.channels),
                "features": candidate.features,
            }
            for candidate in selected
        ],
    }
    return [candidate.doc_id for candidate in selected], trace
