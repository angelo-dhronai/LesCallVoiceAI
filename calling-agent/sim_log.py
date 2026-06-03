"""
Simulates the EXACT call from the production logs.

Shows two runs side-by-side:
  A) What the agent SAW (Kannada/Punjabi dropped by STT filter — agent got silence)
  B) What the agent WOULD SEE if filter passes those transcripts through

Run: python3 sim_log.py
"""
import asyncio, sys, os

_env = os.path.join(os.path.dirname(__file__), ".env")
for line in open(_env):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

os.environ["AGENT_PROVIDER"] = "openai"
os.environ["AGENT_MODEL"]    = "gpt-4o"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
from app.agent.brain import VoiceAgent

R   = "\033[91m"
G   = "\033[92m"
Y   = "\033[93m"
DIM = "\033[2m"
RED = "\033[41m\033[97m"   # red bg
GRN = "\033[42m\033[97m"   # green bg
RST = "\033[0m"

# ── Exact transcript sequence from the log ────────────────────────────────────
# Each entry: (what_stt_produced, passed_the_filter)
#   True  = agent received this
#   False = blocked by unsupported_script filter

LOG_TRANSCRIPTS = [
    # pipeline 1 ──────────────────────────────────────────────────────────────
    ("ಏನು ಇದು?",                           False),   # Kannada: "What is this?" — BLOCKED
    # (empty audio — no speech)
    ("I was looking for uh um",             True),    # cleaned → "i was looking for"
    ("Latin.",                              True),    # STT hallucination, passes filter
    # (barge-in happened here — agent was saying "One moment...")
    ("ये।",                                True),    # Hindi: just "this" — very short
    ("ਮੈਂ ਵੀ ਕਿਹਾ ਹਾਂ ਆਪ ਹੀ ਮਾਂ।",         False),   # Punjabi — BLOCKED

    # pipeline 2 (new greeting fired) ────────────────────────────────────────
    ("Hello.",                              True),
    ("ਵਾਕਈ ਨਾਲ ਵੀ ਆਉਣਾ ਚਾਹੀਦਾ।",           False),   # Punjabi — BLOCKED

    # pipeline 3 (new greeting fired) ────────────────────────────────────────
    ("Hello",                               True),
]


async def run(label: str, color: str, pass_all: bool) -> None:
    print(f"\n{color}{'━'*68}{RST}")
    print(f"{color}  {label}{RST}")
    print(f"{color}{'━'*68}{RST}")

    agent = VoiceAgent()
    opening = await agent.generate_opening()
    print(f"\n{G}Vishal:{RST} {opening.text}\n")

    pipeline = 1
    for stt_text, passed in LOG_TRANSCRIPTS:
        # Show the raw STT output and filter decision
        if passed:
            tag = f"{DIM}[STT ✓ passed]{RST}"
            show_text = stt_text
        else:
            if pass_all:
                tag = f"{GRN}[STT ✓ now allowed]{RST}"
                show_text = stt_text
            else:
                tag = f"{RED}[STT ✗ BLOCKED]{RST}"
                show_text = stt_text

        print(f"{R}Caller:{RST} {show_text}  {tag}")

        # Decide what the agent sees
        if not passed and not pass_all:
            print(f"{DIM}         (agent received nothing — silent gap){RST}\n")
            continue

        # Feed to agent
        chunks = []
        async for result in agent.process_stream(stt_text):
            chunks.append(result.text)
        response = " ".join(chunks)
        print(f"{G}Vishal:{RST} {response}\n")
        await asyncio.sleep(0.2)

    lead = agent.memory.lead
    print(f"{DIM}Lead collected: {lead.summary_for_prompt()}{RST}")


async def main():
    print(f"""
{Y}╔══════════════════════════════════════════════════════════════════╗
║  PRODUCTION LOG REPLAY — what went wrong vs what should happen  ║
╚══════════════════════════════════════════════════════════════════╝{RST}

Caller profile from log:
  • Speaks Kannada first (ಏನು ಇದು? = "What is this?")
  • Tries some English fragments ("I was looking for...")
  • Speaks Punjabi (ਮੈਂ ਵੀ ਕਿਹਾ... = telling someone to come)
  • Gets confused, just says "Hello" multiple times
  • Call ends with no information collected
""")

    await run(
        "SCENARIO A — As it happened  (Kannada/Punjabi BLOCKED by STT filter)",
        "\033[91m",
        pass_all=False,
    )

    print(f"\n{Y}{'─'*68}{RST}")
    print(f"{Y}  Now replaying with the fix: Kannada/Punjabi passed to LLM{RST}")
    print(f"{Y}{'─'*68}{RST}")

    await run(
        "SCENARIO B — With fix  (all transcripts reach the agent)",
        "\033[92m",
        pass_all=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
