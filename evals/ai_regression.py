"""Check the real AI against customer messages whose correct reading is already known.

The pytest tests use a fake AI, so they prove the backend is right but cannot tell whether Gemini really understands
your customers. This script can. It calls the real model (a few cents per run), so it is NOT part of `pytest`.

    python -m evals.ai_regression
    python -m evals.ai_regression --model gemini-2.5-flash --delay 5 --min-accuracy 90
    python -m evals.ai_regression --show-errors      # when calls fail: shows why

Add real customer messages to evals/messages.json (copy them from the Messenger inbox, with the reading you expect).
Run it before you change the prompt or the model, and again after: if the score drops, your change caused it.

A message with "asked_for" is a short reply (like "1 of each") to the question "how many of these cakes?".
Only the fields written for a message are checked: language, intent, faq_topic, product, place and items.
It uses the built-in FAQ topics, so it may differ slightly from a shop whose FAQ answers were edited in the dashboard.
"""
import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from app.services.ai_service import AIResult, AIUnavailableError, GeminiAIService
from app.services.conversation_service import _pick_language, _place_from_text, ask_context
from app.services.faq import FAQS

DEFAULT_FILE = Path(__file__).with_name("messages.json")
FIELDS = ("language", "intent", "faq_topic", "product", "place", "items")


def load_cases(path: Path | str = DEFAULT_FILE) -> tuple[list[str], list[dict[str, Any]]]:
    """The menu names the AI is given, and the list of test messages."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(data["menu"]), list(data["cases"])


def _items(items: Any, collapse: bool = False) -> list[tuple[str, int | None]]:
    """Cakes as comparable (name, quantity) pairs, in a fixed order.

    collapse=True drops exact repeats, the same cleanup the bot does before it quotes a price."""
    pairs = []
    for item in items or []:
        name, qty = (item["product"], item.get("quantity")) if isinstance(item, dict) else (item.product, item.quantity)
        pair = (str(name).strip().lower(), qty)
        if collapse and pair in pairs:
            continue
        pairs.append(pair)
    return sorted(pairs, key=lambda pair: (pair[0], -1 if pair[1] is None else pair[1]))


def load_areas(path: Path | str = DEFAULT_FILE) -> list[dict[str, Any]]:
    """Delivery areas the shop serves (the bot reads these from its database), for the place fallback."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [{"name": name} for name in data.get("areas", [])]


def _normal(field: str, value: Any) -> Any:
    if field == "items":
        return _items(value, collapse=True)
    if field == "intent" and hasattr(value, "value"):
        value = value.value
    return value.strip().lower() if isinstance(value, str) else value


def compare(result: AIResult, expected: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """Where the AI's reading differs from the expected one: (field, expected, got). Unlisted fields are not checked."""
    wrong = []
    for field in FIELDS:
        if field in expected:
            want, got = _normal(field, expected[field]), _normal(field, getattr(result, field))
            if want != got:
                wrong.append((field, want, got))
    return wrong


def describe_error(exc: BaseException) -> str:
    """The kind of failure, e.g. "ClientError 429 RESOURCE_EXHAUSTED". Never the message, which could contain a key."""
    cause = exc.__cause__ or exc
    return " ".join(str(part) for part in (type(cause).__name__, getattr(cause, "code", None), getattr(cause, "status", None)) if part)


def run(service: Any, menu: list[str], topics: dict[str, str], cases: list[dict[str, Any]],
        delay: float = 0.0, sleep: Callable[[float], None] = time.sleep,
        areas: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Send every message to the AI. Each outcome has the case, the differences found, and any error."""
    outcomes = []
    for index, case in enumerate(cases):
        if index and delay:
            sleep(delay)
        extra = {"context": ask_context(case["asked_for"])} if case.get("asked_for") else {}
        try:
            result = service.interpret_message(case["text"], "idle", menu, topics, **extra)
            raw = {"language": result.language, "place": result.place, "items": _items(result.items)}
            # Score what the bot would really use: the AI's answer plus the same fallbacks the bot applies.
            update = {"language": _pick_language(result.language, case["text"])}
            if result.place is None and areas:
                update["place"] = _place_from_text(case["text"], areas)
            result = result.model_copy(update=update)
            outcomes.append({"case": case, "wrong": compare(result, case["expect"]), "error": None,
                             "raw_language": raw["language"], "raw_place": raw["place"], "raw_items": raw["items"]})
        except AIUnavailableError as exc:
            outcomes.append({"case": case, "wrong": [], "error": f"the AI call failed: {describe_error(exc)}",
                             "detail": str(exc.__cause__ or exc)[:400]})
    return outcomes


def _right(outcome: dict[str, Any]) -> bool:
    return not outcome["wrong"] and outcome["error"] is None


def accuracy(outcomes: list[dict[str, Any]]) -> float:
    """Percent of messages read completely right."""
    return 100.0 * sum(_right(o) for o in outcomes) / len(outcomes) if outcomes else 0.0


def report(outcomes: list[dict[str, Any]], show_details: bool = False) -> str:
    by_language: dict[str, list[bool]] = defaultdict(list)
    by_field: dict[str, list[bool]] = defaultdict(list)
    for o in outcomes:
        by_language[o["case"]["expect"].get("language", "?")].append(_right(o))
        wrong_fields = {field for field, _, _ in o["wrong"]}
        for field in o["case"]["expect"]:
            by_field[field].append(o["error"] is None and field not in wrong_fields)
    pct = lambda flags: f"{100 * sum(flags) / len(flags):.0f}% ({sum(flags)}/{len(flags)})"  # noqa: E731
    lines = [f"Messages read completely right: {accuracy(outcomes):.0f}% ({sum(_right(o) for o in outcomes)}/{len(outcomes)})", "", "By language:"]
    lines += [f"  {lang}: {pct(flags)}" for lang, flags in sorted(by_language.items())]
    lines += ["", "By field:"] + [f"  {field}: {pct(flags)}" for field, flags in sorted(by_field.items())]
    def alone(field: str, label: str, got: Callable[[dict[str, Any]], Any], want: Callable[[Any], Any]) -> None:
        flags = [got(o) == want(o["case"]["expect"][field]) for o in outcomes
                 if o["error"] is None and field in o["case"]["expect"] and f"raw_{field}" in o]
        if flags:
            lines.append(f"  {label} from the AI alone (before the bot's own fallbacks): {pct(flags)}")

    alone("language", "language", lambda o: _normal("language", o["raw_language"]), lambda v: _normal("language", v))
    alone("place", "place", lambda o: _normal("place", o["raw_place"]), lambda v: _normal("place", v))
    alone("items", "items", lambda o: o["raw_items"], lambda v: _items(v))
    causes: dict[str, int] = defaultdict(int)
    for o in outcomes:
        if o["error"]:
            causes[o["error"].removeprefix("the AI call failed: ")] += 1
    if causes:
        lines += ["", "Failed calls by cause:"] + [f"  {cause} x{count}" for cause, count in sorted(causes.items())]
        if any("429" in cause for cause in causes):
            lines.append("  429 means rate limited: run again with a bigger --delay, for example --delay 5.")
        if show_details:  # what Google or the SDK said, once per kind of failure (never your key)
            seen: set[str] = set()
            for o in outcomes:
                if o["error"] and o["error"] not in seen:
                    seen.add(o["error"])
                    lines.append(f"  {o['error'].removeprefix('the AI call failed: ')}: {o.get('detail', '')}")
    failures = [o for o in outcomes if not _right(o)]
    if failures:
        lines += ["", "Differences:"]
        for o in failures:
            lines.append(f"  \"{o['case']['text']}\"")
            lines += [f"      {field}: expected {want!r}, got {got!r}" for field, want, got in o["wrong"]]
            if o["error"]:
                lines.append(f"      error: {o['error']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the real AI against known customer messages.")
    parser.add_argument("--file", default=str(DEFAULT_FILE), help="JSON file of messages (default: evals/messages.json)")
    parser.add_argument("--model", default=None, help="Gemini model to test (default: the GEMINI_MODEL setting)")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds to wait between messages (default 1; use 5 on a free key)")
    parser.add_argument("--show-errors", action="store_true", help="also print what Google or the SDK said about each kind of failure")
    parser.add_argument("--min-accuracy", type=float, default=0.0, help="exit with an error below this percent")
    args = parser.parse_args(argv)

    menu, cases = load_cases(args.file)
    topics = {topic: row["description"] for topic, row in FAQS.items()}
    logging.disable(logging.CRITICAL)  # the report lists every failure; skip one log line per failed call
    try:
        outcomes = run(GeminiAIService(args.model), menu, topics, cases, args.delay, areas=load_areas(args.file))
    finally:
        logging.disable(logging.NOTSET)
    print(report(outcomes, args.show_errors))
    if outcomes and all(o["error"] for o in outcomes):
        print("\nEvery AI call failed. Check that GEMINI_API_KEY is set in .env and that you are online.")
    return 1 if accuracy(outcomes) < args.min_accuracy else 0


if __name__ == "__main__":
    sys.exit(main())
