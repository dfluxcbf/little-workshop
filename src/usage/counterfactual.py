"""What automating a candidate would change, estimated before anything is built from the sessions its
occurrences happened in (docs/pillars/usage.md, "What automating would save").

Every request re-sends the whole conversation, so a tool call costs twice: the turns it takes, and what
it adds to the conversation, which every later request re-reads until the session compacts or is
cleared. The stored data gives both for the past, exactly: each request's context size and weighted
cost, in order, per thread (the main conversation, and each subagent's). Replaying an occurrence against
its own thread gives what it cost from then on; the same replay with the occurrence replaced gives what
the replacement would have cost. Only the replacement's own footprint is modelled, from constants
fitted on every stored turn (`calibrate`), and the fit is checked on the newest turns it was not fitted
on. Measured on this machine's 20,693 turns (2026-10-04): 2.41 bytes of tool result per token, 97 tokens
of overhead per call, the model's own text staying in the conversation, an addition re-read by a median
of 144 later requests; on the newest 30% the fit was within 6.7% per turn (median) and 0.0% in total."""

from __future__ import annotations

import bisect
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from statistics import median

from usage.pricing import Price, price_for, weighted

# A request whose context is under this share of the one before it follows a compaction or a /clear:
# what was added before it is no longer re-read.
COMPACTION_RATIO = 0.7
REPLY_BYTES = 400  # what a replacement answers with: a status line, not pages of output
HOLDOUT_SHARE = 0.3  # the newest share of turns the calibration is checked on, not fitted on
MIN_TURNS = 200  # fewer measured turns than this: the fallback constants are used
LARGE_RESULT_BYTES = 2000
SMALL_RESULT_BYTES = 200
MAIN = ""  # the thread key of a session's main conversation; a subagent's is its agent name


@dataclass
class Calibration:
    """What a tool call adds to the conversation, fitted on stored turns: `output tokens the model wrote
    + calls × call_overhead + result bytes / bytes_per_token`."""

    bytes_per_token: float = 2.4
    call_overhead: float = 97.0
    call_output: float = 342.0  # what the model writes in a turn that makes one call (median)
    turns: int = 0  # how many turns the constants were fitted on (0: the fallback constants)
    check: dict = field(default_factory=dict)  # the fit tested on the newest turns it was not fitted on

    def footprint(self, output_tokens: float, calls: int, result_bytes: float) -> float:
        return output_tokens + calls * self.call_overhead + result_bytes / self.bytes_per_token

    def as_dict(self) -> dict:
        return asdict(self)


class Replay:
    """The stored requests as threads, with what each request cost and what each context token in it cost."""

    def __init__(self, conn: sqlite3.Connection, prices: dict[str, Price]):
        self.threads: dict[tuple[str, str], dict] = {}
        self.where: dict[str, tuple[tuple[str, str], int]] = {}
        read_cost = read_tokens = 0.0
        rows = conn.execute("SELECT * FROM requests ORDER BY session_id, ts, request_id").fetchall()
        for row in rows:
            request = dict(row)
            key = (request["session_id"], (request.get("agent_name") or "subagent") if request.get("query_source") == "subagent" else MAIN)
            thread = self.threads.setdefault(key, {"ts": [], "ctx": [], "cost": [], "out": [], "price": [], "model": request["model"]})
            parts = weighted(request, prices)
            context = request["input_tokens"] + request["cache_read_tokens"] + request["cache_write_tokens"]
            self.where[request["request_id"]] = (key, len(thread["ts"]))
            thread["ts"].append(request["ts"])
            thread["ctx"].append(context)
            thread["cost"].append(sum(parts.values()))
            thread["out"].append(request["output_tokens"])
            # What one context token of this request cost, with its own mix of cache reads, writes and misses.
            thread["price"].append((parts["input"] + parts["cache_read"] + parts["cache_write"]) / context if context else 0.0)
            read_cost += parts["cache_read"]
            read_tokens += request["cache_read_tokens"]
        for thread in self.threads.values():
            ctx, n = thread["ctx"], len(thread["ctx"])
            starts = [k for k in range(1, n) if ctx[k] < COMPACTION_RATIO * ctx[k - 1]]
            thread["next_boundary"] = [starts[bisect.bisect_right(starts, i)] if bisect.bisect_right(starts, i) < len(starts) else n
                                       for i in range(n)]
            running, suffix = 0.0, [0.0] * (n + 1)
            for k in range(n - 1, -1, -1):
                running += thread["price"][k]
                suffix[k] = running
            thread["suffix_price"] = suffix
        fallback = prices.get("", next(iter(prices.values())))
        self.machine_read_price = read_cost / read_tokens if read_tokens else fallback.input * fallback.cache_read / 1e6

    def requests_between(self, start: date, end: date) -> int:
        first, last = start.isoformat(), (end + timedelta(days=1)).isoformat()
        return sum(1 for (session, agent), thread in self.threads.items() if agent == MAIN
                   for ts in thread["ts"] if first <= ts[:10] < last)

    def locate(self, call: dict) -> tuple[tuple[str, str], int] | None:
        """The request that made `call`: by its request id, else the main thread's last request before it."""
        if call.get("request_id") in self.where:
            return self.where[call["request_id"]]
        key = (call["session_id"], MAIN)
        thread = self.threads.get(key)
        if not thread:
            return None
        index = bisect.bisect_right(thread["ts"], call["ts"]) - 1
        return (key, index) if index >= 0 else None

    def _reread_price(self, thread: dict, after: int) -> tuple[float, int]:
        """What one token added before request `after` + 1 costs from then on: the sum of each later
        request's price per context token, up to the next compaction or clear; and how many requests that is."""
        end = thread["next_boundary"][after]
        start = after + 1
        if start >= end:
            return 0.0, 0
        return thread["suffix_price"][start] - thread["suffix_price"][end], end - start

    def occurrence(self, calls: list[dict], calibration: Calibration, *, trigger_ok: bool) -> dict | None:
        """What one past occurrence cost from its first turn on, and what it would have cost replaced: by a
        tool Claude calls (`called`), or by a trigger or a schedule, with no turn at all (`triggered`, None
        when `trigger_ok` is false). The `_low` figures assume the replacement makes Claude take one more
        turn to check on it, as a call that returns before its work is done does."""
        first, last = self.locate(calls[0]), self.locate(calls[-1])
        if first is None or last is None or first[0] != last[0]:
            return None
        key, i1 = first
        ik = max(i1, last[1])
        thread = self.threads[key]
        n = len(thread["ts"])
        # What the occurrence's own content added to the conversation: the text its turns wrote, each call's
        # overhead and its results. Not the observed growth to the next request: that also holds what the
        # harness inserted between turns (reminders, attachments), which a replacement would not remove.
        result_bytes = sum(c.get("result_bytes") or 0 for c in calls)
        added = calibration.footprint(sum(thread["out"][i1:ik + 1]), len(calls), result_bytes)
        reread, later = self._reread_price(thread, ik)
        kept_turn = thread["cost"][i1]
        dropped_turns = sum(thread["cost"][i1 + 1:ik + 1])
        before = kept_turn + dropped_turns + added * reread
        replacement = calibration.footprint(thread["out"][i1], 1, REPLY_BYTES)
        called = before - (kept_turn + replacement * reread)
        # One more turn to check on the replacement: a request like the one after the occurrence, adding a short call.
        check_turn = thread["cost"][ik + 1] if ik + 1 < n else thread["cost"][ik]
        check = check_turn + calibration.footprint(calibration.call_output, 1, REPLY_BYTES) * reread
        return {
            "turns": ik - i1 + 1, "added_tokens": added, "later_requests": later,
            "output_bytes": result_bytes,
            "called": called, "called_low": called - check,
            "triggered": before if trigger_ok else None, "triggered_low": before - check if trigger_ok else None,
        }


def _turns(conn: sqlite3.Connection, replay: Replay) -> list[dict]:
    """Every stored turn that made tool calls and has a next request in its thread before any compaction:
    how much it grew the conversation, and what it wrote and got back."""
    calls: dict[str, list[dict]] = {}
    for row in conn.execute("SELECT request_id, result_bytes FROM tool_calls WHERE request_id IS NOT NULL"):
        calls.setdefault(row["request_id"], []).append(dict(row))
    turns = []
    for request_id, made in calls.items():
        if request_id not in replay.where:
            continue
        key, i = replay.where[request_id]
        thread = replay.threads[key]
        if i + 1 >= thread["next_boundary"][i]:
            continue
        growth = thread["ctx"][i + 1] - thread["ctx"][i]
        turns.append({"ts": thread["ts"][i], "growth": growth, "out": thread["out"][i], "calls": len(made),
                      "result_bytes": sum(c["result_bytes"] or 0 for c in made)})
    return sorted(turns, key=lambda turn: turn["ts"])


def _fit(turns: list[dict]) -> Calibration | None:
    """The overhead from turns whose result is tiny (their growth is the model's text and the overhead), then
    the bytes per token over the turns with a large result, once their text and overhead are taken out."""
    small = [t["growth"] - t["out"] for t in turns if t["result_bytes"] < SMALL_RESULT_BYTES and t["calls"] == 1]
    single = [t["out"] for t in turns if t["calls"] == 1]
    if len(turns) < MIN_TURNS or not small or not single:
        return None
    overhead = median(small)
    large = [(t["result_bytes"], t["growth"] - t["out"] - t["calls"] * overhead) for t in turns
             if t["result_bytes"] > LARGE_RESULT_BYTES and t["growth"] - t["out"] - t["calls"] * overhead > 0]
    if not large:
        return None
    # A ratio of sums, not a median of ratios: an estimate adds results up, so their total is what must be right.
    bytes_per_token = sum(b for b, _ in large) / sum(tokens for _, tokens in large)
    return Calibration(bytes_per_token=bytes_per_token, call_overhead=overhead, call_output=median(single), turns=len(turns))


def calibrate(conn: sqlite3.Connection, replay: Replay) -> Calibration:
    """The constants fitted on every stored turn, and the same fit made on the older turns and tested on
    the newest `HOLDOUT_SHARE` of them: the error an estimate carries. The fallback constants (measured
    on this machine on 2026-10-04) stand in while there are too few turns."""
    turns = [t for t in _turns(conn, replay) if t["growth"] >= 0]
    fitted = _fit(turns)
    if fitted is None:
        return Calibration(check={"turns": 0, "note": f"fewer than {MIN_TURNS} measured turns: fallback constants"})
    cut = int(len(turns) * (1 - HOLDOUT_SHARE))
    older, newer = turns[:cut], turns[cut:]
    trial = _fit(older)
    if trial is not None and newer:
        predicted = [trial.footprint(t["out"], t["calls"], t["result_bytes"]) for t in newer]
        errors = [abs(p - t["growth"]) for p, t in zip(predicted, newer)]
        relative = [abs(p - t["growth"]) / t["growth"] for p, t in zip(predicted, newer) if t["growth"] > 0]
        actual = sum(t["growth"] for t in newer)
        fitted.check = {
            "turns": len(newer), "median_abs_error_tokens": median(errors),
            "median_relative_error": median(relative) if relative else None,
            "total_error": (sum(predicted) - actual) / actual if actual else None,
        }
    return fitted


def method_report(conn: sqlite3.Connection, replay: Replay, calibration: Calibration) -> dict:
    """What the estimates rest on, for the page: the constants, their check, and how long an addition stays."""
    rereads = [thread["next_boundary"][i] - i - 1 for thread in replay.threads.values() for i in range(len(thread["ts"]))]
    subagent_threads = sum(1 for (_, agent) in replay.threads if agent != MAIN)
    return {
        "calibration": calibration.as_dict(),
        "later_requests_median": median(rereads) if rereads else None,
        "threads": len(replay.threads), "subagent_threads": subagent_threads,
        "compaction_ratio": COMPACTION_RATIO, "reply_bytes": REPLY_BYTES,
    }
