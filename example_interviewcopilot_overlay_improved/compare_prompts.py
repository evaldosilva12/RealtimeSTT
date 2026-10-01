"""Compare prompt versions and OpenAI models on fixed interview questions.

Usage (venv active, from this folder):
    python compare_prompts.py [--configs A,B,C] [--questions 1,2]

Reads the active profile from a COPY of the SQLite DB (the real one is never
touched) and calls the OpenAI API with the key from .env. Results go to
compare_results/results.json and compare_results/results.md.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from actions import ActionService
from config import settings
from llm_service import LLMService
from prompt_builder import build_system, build_user
from storage import Storage

OUT_DIR = Path(__file__).parent / "compare_results"

GUARDRAILS = """- I coordinate and unblock engineering work. I do not write code, manage people, or make architecture or product decisions. Never claim any of these, and do not bring them up unless asked.
- On compliance questions: I support SOC 2 work (access control tracking, evidence collection, audit preparation). I do not own or lead audits.
- My last job ended in March 2026. Do not say I am working there now.
- Positioning: the practical person who keeps engineering work organized so senior engineers can focus on technical work. Show this with real examples, not with slogans."""

# USD per 1M tokens: input, cached input, output
PRICES = {
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
}

# key -> (label, model, prompt version, extra request params)
CONFIGS = {
    "A": ("4.1-mini | OLD prompt (current app)", "gpt-4.1-mini", "old", {"temperature": 0.6, "max_tokens": 450}),
    "B": ("4.1-mini | NEW prompt", "gpt-4.1-mini", "new", {"temperature": 0.6, "max_tokens": 450}),
    "C": ("Luna | NEW | reasoning none", "gpt-6-luna", "new", {"reasoning_effort": "none", "max_completion_tokens": 1000}),
    "D": ("Luna | NEW | reasoning default", "gpt-6-luna", "new", {"max_completion_tokens": 1500}),
    "E": ("Sol | NEW | reasoning low", "gpt-6.1-sol", "new", {"reasoning_effort": "low", "max_completion_tokens": 1500}),
}

# (id, tag, action_type, conversation before, selected text)
QUESTIONS = [
    (1, "greeting", "answer_last_n", [],
     "Hi, thanks for joining us today. How are you doing?"),
    (2, "intro", "answer_last_n", ["Hi, I'm Mark, I lead engineering here. Great to meet you."],
     "So let's start with the basics. Can you tell me about yourself and your background?"),
    (3, "behavioral", "answer_last_n", ["Great, thanks for that overview. That was helpful."],
     "Tell me about a time when you faced a technical challenge and a people challenge at the same time."),
    (4, "soc2", "answer_last_n", [],
     "We care a lot about SOC 2 here. What has been your experience with compliance work?"),
    (5, "pushback", "answer_last_n", ["Our engineers are very senior and they are busy."],
     "You don't write code. So how would you earn their respect and actually help them?"),
    (6, "why_role", "answer_last_n", [],
     "Why DATS, and why this role?"),
    (7, "weakness", "answer_last_n", [],
     "What would you say is your biggest weakness?"),
    (8, "metric_followup", "answer_last_n",
     ["You mentioned you reduced the manual workload a lot at 908 Engineering."],
     "Interesting. How did you actually get to that number, and what exactly did you automate?"),
    (9, "mid_story_no_question", "answer_last_n",
     ["Let me give you some context about the team before the next question.",
      "We have five squads and two of them are fully remote across different time zones."],
     "And our estimations were perfectly laid out till end of summer. But then what happened that our company signed a contract with the new..."),
    (10, "question_earlier", "answer_last_n",
     ["Tell me about a time you had to handle a last minute scope change.", "Take your time, no rush."],
     "Yeah, and just to add, in our case it happens quite often with releases."),
    (11, "ask_interviewer", "ask_question",
     ["We are growing fast and the release process is still a bit messy.", "I think that covers my side."],
     "Do you have any questions for us?"),
    (12, "tool_gap", "answer_last_n", [],
     "Have you used Jira or Linear to manage releases? Tell me how."),
]


def stream_openai(model: str, messages: list[dict], extra: dict) -> dict:
    body = {"model": model, "messages": messages, "stream": True,
            "stream_options": {"include_usage": True}, **extra}
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    first = None
    chunks: list[str] = []
    usage = None
    finish = None
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            for raw in response:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                event = json.loads(data)
                if event.get("usage"):
                    usage = event["usage"]
                for choice in event.get("choices") or []:
                    delta = (choice.get("delta") or {}).get("content") or ""
                    if delta:
                        if first is None:
                            first = time.perf_counter() - start
                        chunks.append(delta)
                    finish = choice.get("finish_reason") or finish
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        return {"error": f"HTTP {exc.code}: {detail}"}
    total = time.perf_counter() - start
    text = "".join(chunks).strip()
    usage = usage or {}
    prompt_tokens = usage.get("prompt_tokens", 0)
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
    price_in, price_cached, price_out = PRICES.get(model, (0, 0, 0))
    cost = ((prompt_tokens - cached) * price_in + cached * price_cached + completion * price_out) / 1e6
    return {
        "text": text, "ttft": first, "total": total, "words": len(text.split()),
        "prompt_tokens": prompt_tokens, "cached_tokens": cached,
        "completion_tokens": completion, "reasoning_tokens": reasoning,
        "finish_reason": finish, "cost_usd": cost,
    }


def old_messages(action_service: ActionService, storage: Storage, question) -> list[dict]:
    _, _, action_type, before, selected = question
    session_id = str(uuid.uuid4())
    storage.create_session(session_id)
    now = time.time()
    for index, text in enumerate([*before, selected]):
        storage.save_utterance(str(uuid.uuid4()), session_id, "other", "final", text)
        storage.connection.execute(
            "UPDATE utterances SET created_at=? WHERE session_id=? AND text=?", (now + index, session_id, text))
    storage.connection.commit()
    messages, _, _ = action_service._build_messages(
        session_id, action_type, selected, "direct prompt", {"text": selected}, "normal")
    return messages


def new_messages(profile: dict, question) -> list[dict]:
    _, _, action_type, before, selected = question
    return [
        {"role": "system", "content": build_system(profile)},
        {"role": "user", "content": build_user(action_type, selected, before, [], response_style=profile.get("response_style") or "Natural")},
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configs", default=",".join(CONFIGS))
    parser.add_argument("--questions", default=",".join(str(q[0]) for q in QUESTIONS))
    args = parser.parse_args()
    config_keys = [c.strip().upper() for c in args.configs.split(",") if c.strip()]
    question_ids = {int(x) for x in args.questions.split(",") if x.strip()}
    questions = [q for q in QUESTIONS if q[0] in question_ids]

    if not settings.openai_api_key:
        sys.exit("OPENAI_API_KEY missing in .env")

    tmp_dir = Path(tempfile.mkdtemp())
    db_copy = tmp_dir / "copy.sqlite3"
    shutil.copy(settings.db_path, db_copy)
    storage = Storage(db_copy)
    profile = dict(storage.get_active_interview_profile() or storage.ensure_default_interview_profile())
    new_profile = {**profile, "additional_instructions": GUARDRAILS}
    action_service = ActionService(storage, LLMService(), None)  # type: ignore[arg-type]

    OUT_DIR.mkdir(exist_ok=True)
    results = []
    for key in config_keys:
        label, model, version, extra = CONFIGS[key]
        print(f"\n=== {key}: {label}", flush=True)
        for question in questions:
            messages = old_messages(action_service, storage, question) if version == "old" else new_messages(new_profile, question)
            result = stream_openai(model, messages, extra)
            result.update({"config": key, "label": label, "qid": question[0], "tag": question[1]})
            results.append(result)
            if "error" in result:
                print(f"  Q{question[0]:>2} {question[1]:<22} ERROR {result['error'][:120]}", flush=True)
            else:
                print(f"  Q{question[0]:>2} {question[1]:<22} ttft={result['ttft'] or 0:.2f}s total={result['total']:.2f}s "
                      f"words={result['words']:>3} reason={result['reasoning_tokens']:>4} "
                      f"cached={result['cached_tokens']}/{result['prompt_tokens']} ${result['cost_usd']:.5f}", flush=True)

    (OUT_DIR / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(results, questions, config_keys)
    print(f"\nSaved to {OUT_DIR}")


def write_markdown(results: list[dict], questions, config_keys) -> None:
    lines = ["# Prompt / model comparison\n", "## Summary (averages)\n",
             "| Config | TTFT s | Total s | Words | Reasoning tok | Cache hit | $/answer | Errors |", "|---|---|---|---|---|---|---|---|"]
    for key in config_keys:
        rows = [r for r in results if r["config"] == key]
        ok = [r for r in rows if "error" not in r]
        if not ok:
            lines.append(f"| {key} {CONFIGS[key][0]} | - | - | - | - | - | - | {len(rows)} |")
            continue
        avg = lambda name: sum((r[name] or 0) for r in ok) / len(ok)
        hit = sum(r["cached_tokens"] for r in ok) / max(1, sum(r["prompt_tokens"] for r in ok))
        lines.append(f"| {key} {CONFIGS[key][0]} | {avg('ttft'):.2f} | {avg('total'):.2f} | {avg('words'):.0f} | "
                     f"{avg('reasoning_tokens'):.0f} | {hit:.0%} | {avg('cost_usd'):.5f} | {len(rows) - len(ok)} |")
    lines.append("\n## Answers\n")
    for question in questions:
        lines.append(f"### Q{question[0]} ({question[1]}) - {question[2]}\n")
        if question[3]:
            lines.append("Before: " + " / ".join(question[3]) + "\n")
        lines.append(f"**Selected:** {question[4]}\n")
        for key in config_keys:
            result = next((r for r in results if r["config"] == key and r["qid"] == question[0]), None)
            if not result:
                continue
            if "error" in result:
                lines.append(f"**{key} {CONFIGS[key][0]}** - ERROR: {result['error']}\n")
                continue
            lines.append(f"**{key} {CONFIGS[key][0]}** ({result['words']} words, ttft {result['ttft'] or 0:.2f}s, "
                         f"total {result['total']:.2f}s, finish={result['finish_reason']})\n")
            lines.append("> " + (result["text"] or "(empty)").replace("\n", "\n> ") + "\n")
    (OUT_DIR / "results.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
