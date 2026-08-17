"""A fake `pi` executable.

Writes a JSONL session shaped like the real one so the adapter can be tested without a
provider, a network, or a key. Behaviour is chosen with RCPIA_FAKE_PI_MODE:

    ok           a well-formed run ending in the JSON answer
    preamble     a sentence, then the JSON in a later text part
    fenced       the answer wrapped in a ```json block
    thinking     thinking and tool-call parts around the answer
    empty        a session with no assistant text at all
    garbage      an answer that is not JSON
    truncated    a well-formed answer followed by a half-written line
    crash        non-zero exit
    nosession    exits 0 without writing a session
    hang         sleeps, to exercise the timeout
    invented     an answer citing evidence the bundle never contained
    wrongcase    an answer about a different case
    offschema    valid JSON that does not match AgentOutput

The answer echoes the case, role and first snippet id found in the task bundle, the way
a cooperating model would. That makes the fake exercise the real path — a bundle that
never reached the model produces an answer that fails validation, which is exactly what
should happen.
"""

import json
import os
import sys
import time
from pathlib import Path


def argument(flag: str) -> str | None:
    argv = sys.argv
    return argv[argv.index(flag) + 1] if flag in argv else None


def read_bundle() -> dict:
    """Read the task bundle Pi was given as `-p "@path"`."""
    prompt = argument("-p") or ""
    if not prompt.startswith("@"):
        return {}
    try:
        return json.loads(Path(prompt[1:]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def answer_for(bundle: dict, mode: str) -> dict:
    inputs = bundle.get("inputs") or {}
    snippets = inputs.get("source_snippets") or []
    citation = snippets[0]["id"] if snippets else None

    answer = {
        "agent_role": inputs.get("agent_role", "urology_oncology"),
        "case_id": inputs.get("case_id", "case-under-test"),
        "recommendation": "Cystectomie totale après optimisation.",
        "rationale": "Tumeur infiltrant le muscle.",
        "evidence_references": (
            [{"id": citation, "supports": "Infiltration confirmée"}] if citation else []
        ),
    }

    if mode == "invented":
        answer["evidence_references"] = [{"id": "ev-999", "supports": "Inventé"}]
    elif mode == "wrongcase":
        answer["case_id"] = "00000000-0000-4000-8000-000000000000"
    elif mode == "offschema":
        answer["confidence"] = 0.97  # AgentOutput forbids unknown fields
    return answer


def event(kind: str, **fields: object) -> str:
    return json.dumps({"type": kind, **fields}, ensure_ascii=False)


def assistant(parts: list[dict], usage: bool = True) -> str:
    message: dict = {
        "role": "assistant",
        "content": parts,
        "provider": "azure-openai-responses",
        "model": "gpt-5.6-sol",
    }
    if usage:
        message["usage"] = {
            "input": 1200,
            "output": 300,
            "totalTokens": 1500,
            "cost": {"input": 0.001, "output": 0.002, "total": 0.003},
        }
    return json.dumps({"type": "message", "message": message}, ensure_ascii=False)


def main() -> int:
    mode = os.environ.get("RCPIA_FAKE_PI_MODE", "ok")

    if mode == "hang":
        time.sleep(30)
        return 0
    if mode == "crash":
        print("azure: deployment not found", file=sys.stderr)
        return 3
    if mode == "nosession":
        return 0

    session_path = argument("--session")
    if session_path is None:
        print("fake pi: --session is required", file=sys.stderr)
        return 2

    path = Path(session_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        event("session", version=3, id="019eafa0-fake", cwd=str(Path.cwd())),
        event(
            "model_change",
            provider="azure-openai-responses",
            modelId="gpt-5.6-sol",
        ),
        json.dumps(
            {
                "type": "message",
                "message": {"role": "user", "content": [{"type": "text", "text": "task"}]},
            }
        ),
    ]

    answer = json.dumps(answer_for(read_bundle(), mode), ensure_ascii=False)

    if mode in ("ok", "invented", "wrongcase", "offschema"):
        lines.append(assistant([{"type": "text", "text": answer}]))
    elif mode == "preamble":
        # Two assistant messages: chatter first, the real answer second.
        lines.append(assistant([{"type": "text", "text": "Je vais analyser ce dossier."}]))
        lines.append(assistant([{"type": "text", "text": answer}]))
    elif mode == "fenced":
        lines.append(assistant([{"type": "text", "text": f"```json\n{answer}\n```"}]))
    elif mode == "thinking":
        lines.append(
            assistant(
                [
                    {"type": "thinking", "thinking": "Le muscle est infiltré, donc…"},
                    {"type": "toolCall", "id": "call_1", "name": "read", "arguments": {}},
                    {"type": "text", "text": answer},
                ]
            )
        )
    elif mode == "empty":
        lines.append(assistant([{"type": "thinking", "thinking": "…"}]))
    elif mode == "garbage":
        lines.append(assistant([{"type": "text", "text": "Je recommande une cystectomie."}]))
    elif mode == "truncated":
        lines.append(assistant([{"type": "text", "text": answer}]))
        lines.append('{"type": "message", "message": {"role": "assist')

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # The real Pi echoes its startup line to stderr under --verbose.
    print("provider=azure-openai-responses model=gpt-5.6-sol", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
