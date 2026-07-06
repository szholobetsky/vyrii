"""ctxwindow — AutoCut Context: non-destructive sliding-window projection of a
chat message list, applied right before each LLM call.

Ported from 1bcoder's `/ctx window` (C:\\Project\\1bcoder\\chat.py, functions
`_ctx_window_projection`/`_rs_select_messages`/`_bm25_select_messages`/
`_dp_select_messages`/`_tr_select_messages`), but as vyrii's own self-contained
copy — no import of/dependency on 1bcoder, only stdlib — mirroring the same
"own copy, not a shim" decision already made for `vyrii/ctxtimer.py`.

Unlike ctxtimer, this is a pure, synchronous, in-process list transform (no LLM
round-trips of its own), so it needs none of ctxtimer's threading/progress_cb/
should_cancel machinery — closer in shape to `engine.py`'s `smart_ctx()`.
"""
from __future__ import annotations

import random


def apply_autocut(messages: list, *, enabled: bool, first: int, last: int,
                   algo: str = "bm25", limit: int = 0) -> list:
    """Non-destructive first+last(+mid) windowed projection of messages.

    Returns messages unchanged when disabled, too short, or the window doesn't
    reduce anything (first/last budgets already cover the whole conversation).
    The tail (last) always keeps at least the final message, even if it alone
    exceeds the last-budget.
    """
    if not enabled or len(messages) <= 1:
        return messages

    last_idx_start = len(messages) - 1
    used = len(messages[-1].get("content") or "") // 4
    for i in range(len(messages) - 2, -1, -1):
        toks = len(messages[i].get("content") or "") // 4
        if used + toks > last:
            break
        used += toks
        last_idx_start = i

    first_idx_end = 0
    if first > 0:
        used = 0
        for i in range(len(messages)):
            toks = len(messages[i].get("content") or "") // 4
            if used + toks > first:
                break
            used += toks
            first_idx_end = i + 1

    if first_idx_end >= last_idx_start:
        return messages

    mid_candidates = messages[first_idx_end:last_idx_start]
    mid_selected: list = []
    if algo and mid_candidates and limit > 0:
        if algo == "rs":
            mid_selected = _rs_select(mid_candidates, limit)
        else:
            query_terms = (messages[-1].get("content") or "").lower().split()
            if algo == "bm25":
                mid_selected = _bm25_select(mid_candidates, query_terms, limit)
            elif algo == "dp":
                mid_selected = _dp_select(mid_candidates, query_terms, limit)
            elif algo == "tr":
                mid_selected = _tr_select(mid_candidates, limit)

    return messages[:first_idx_end] + mid_selected + messages[last_idx_start:]


def _fts_rank(terms: list, contents: dict, top_k: int) -> list:
    """Rank short texts by BM25 using in-memory SQLite FTS5 (stdlib only).

    Own copy for vyrii (see module docstring) — same approach as 1bcoder's
    `_fts_rank`, just applied to conversation messages instead of file contents.
    Returns [(key, rank)] best-first (rank is raw FTS5 rank, negative).
    """
    import sqlite3
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE t USING fts5(key UNINDEXED, content)")
    db.executemany("INSERT INTO t VALUES (?, ?)", contents.items())
    fts_query = " OR ".join(f'"{t}"' for t in terms)
    rows = db.execute(
        "SELECT key, rank FROM t WHERE t MATCH ? ORDER BY rank LIMIT ?",
        (fts_query, top_k)
    ).fetchall()
    db.close()
    return rows


def _rs_select(mid_candidates: list, limit_tokens: int) -> list:
    """Pick whole messages within a token budget via uniform random sampling.

    Note: true single-pass reservoir-sampling semantics aren't needed here since
    mid_candidates is already fully memory-resident — this is budget-constrained
    uniform sampling, functionally equivalent for the "cheap, honest" rs mode.
    """
    order = list(range(len(mid_candidates)))
    random.shuffle(order)
    picked = []
    used = 0
    for idx in order:
        toks = len(mid_candidates[idx].get("content") or "") // 4
        if used + toks > limit_tokens and picked:
            continue
        picked.append(idx)
        used += toks
        if used >= limit_tokens:
            break
    picked.sort()
    return [mid_candidates[i] for i in picked]


def _bm25_select(mid_candidates: list, query_terms: list, limit_tokens: int) -> list:
    """Pick whole messages within a token budget via BM25 relevance to query_terms."""
    if not mid_candidates or not query_terms:
        return []
    contents = {str(i): (m.get("content") or "") for i, m in enumerate(mid_candidates)}
    ranked = _fts_rank(query_terms, contents, top_k=len(mid_candidates))
    picked = []
    used = 0
    for key, _score in ranked:
        idx = int(key)
        toks = len(mid_candidates[idx].get("content") or "") // 4
        if used + toks > limit_tokens and picked:
            continue
        picked.append(idx)
        used += toks
        if used >= limit_tokens:
            break
    picked.sort()
    return [mid_candidates[i] for i in picked]


def _dp_select(mid_candidates: list, query_terms: list, limit_tokens: int) -> list:
    """Pick whole messages via 0/1 knapsack: weight=tokens, value=BM25 relevance."""
    if not mid_candidates or limit_tokens <= 0:
        return []
    weights = [max(1, len(m.get("content") or "") // 4) for m in mid_candidates]
    values = [0.0] * len(mid_candidates)
    if query_terms:
        contents = {str(i): (m.get("content") or "") for i, m in enumerate(mid_candidates)}
        ranked = _fts_rank(query_terms, contents, top_k=len(mid_candidates))
        for key, score in ranked:
            values[int(key)] = -float(score)
    n = len(mid_candidates)
    cap = int(limit_tokens)
    dp = [[0.0] * (cap + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        w, v = weights[i - 1], values[i - 1]
        for c in range(cap + 1):
            dp[i][c] = dp[i - 1][c]
            if w <= c and dp[i - 1][c - w] + v > dp[i][c]:
                dp[i][c] = dp[i - 1][c - w] + v
    picked = []
    c = cap
    for i in range(n, 0, -1):
        if dp[i][c] != dp[i - 1][c]:
            picked.append(i - 1)
            c -= weights[i - 1]
    picked.sort()
    return [mid_candidates[i] for i in picked]


def _tr_select(mid_candidates: list, limit_tokens: int) -> list:
    """Pick whole messages via TextRank/LexRank graph centrality (no query, no LLM)."""
    n = len(mid_candidates)
    if n == 0:
        return []
    if n == 1:
        return list(mid_candidates)
    word_sets = [set((m.get("content") or "").lower().split()) for m in mid_candidates]
    sim = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            a, b = word_sets[i], word_sets[j]
            if not a or not b:
                continue
            inter = len(a & b)
            if inter == 0:
                continue
            score = inter / len(a | b)
            sim[i][j] = score
            sim[j][i] = score
    scores = [1.0 / n] * n
    damping = 0.85
    for _ in range(30):
        new_scores = [(1 - damping) / n] * n
        for i in range(n):
            row_sum = sum(sim[i])
            if row_sum == 0:
                continue
            for j in range(n):
                if sim[i][j] > 0:
                    new_scores[j] += damping * scores[i] * sim[i][j] / row_sum
        scores = new_scores
    order = sorted(range(n), key=lambda i: scores[i], reverse=True)
    picked = []
    used = 0
    for idx in order:
        toks = len(mid_candidates[idx].get("content") or "") // 4
        if used + toks > limit_tokens and picked:
            continue
        picked.append(idx)
        used += toks
        if used >= limit_tokens:
            break
    picked.sort()
    return [mid_candidates[i] for i in picked]
