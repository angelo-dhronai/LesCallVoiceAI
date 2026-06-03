"""
Multilingual conversation simulator — Hindi · Gujarati · English
Run: python3 sim.py [scenario_number]
"""
import asyncio
import sys
import os

_env_path = os.path.join(os.path.dirname(__file__), ".env")
for line in open(_env_path):
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
B   = "\033[94m"
RST = "\033[0m"

SCENARIOS = [
    # ── HINDI ──────────────────────────────────────────────────────────────
    {
        "lang": "HINDI",
        "name": "Full sales flow — investment buyer, Surat 3BHK, budget ₹1Cr",
        "turns": [
            "हाँ, मैं Surat में देख रहा हूँ",
            "3BHK चाहिए",
            "Investment के लिए है",
            "Budget around 1 crore है",
            "6 महीने में चाहिए",
            "ठीक है, site देखना चाहता हूँ। Saturday को free हूँ",
            "मेरा नाम Amit है, number 9876543210",
        ],
    },
    {
        "lang": "HINDI",
        "name": "Objection handling — caller is busy, keeps pushing back",
        "turns": [
            "Surat में 2BHK chahiye, ghar ke liye",
            "Budget 70-80 lakh",
            "Abhi busy hoon, baad mein call karna",
            "Nahi, time nahi hai",
            "Theek hai, WhatsApp pe bhejo",
        ],
    },
    {
        "lang": "HINDI",
        "name": "Script discipline — Hindi Devanagari throughout, no Romanisation",
        "turns": [
            "नमस्ते, मुझे Vapi में flat चाहिए",
            "2BHK, किराये के लिए नहीं, खरीदना है",
            "Budget 35 lakh के आसपास है",
            "कौनसा project best रहेगा?",
        ],
    },
    # ── GUJARATI ───────────────────────────────────────────────────────────
    {
        "lang": "GUJARATI",
        "name": "Full sales flow — self-use, Vapi 2BHK, ready possession",
        "turns": [
            "હા, Vapi માં જ જોવું છે",
            "2BHK જોઈએ છે, પોતાના ઘર માટે",
            "Budget 30-35 lakh ની આસપાસ છે",
            "Ready possession જ જોઈએ",
            "Site visit ક્યારે કરી શકાય?",
            "Sunday સવારે ઠીક રહેશે. મારું નામ Priya છે",
        ],
    },
    {
        "lang": "GUJARATI",
        "name": "Script discipline — Gujarati script throughout, no Romanisation",
        "turns": [
            "Silvassa માં property જોવી છે",
            "Weekend home જોઈએ છે, resort style",
            "Budget 50-60 lakh",
            "Amenities કેવી છે?",
        ],
    },
    {
        "lang": "GUJARATI",
        "name": "Objection — caller says too expensive, agent reframes",
        "turns": [
            "Surat માં 3BHK જોઈએ",
            "Investment માટે",
            "ભાવ ઘણો વધારે છે",
            "Budget 70 lakh જ છે",
            "Okay, thoda sochh ke batata hoon",
        ],
    },
    # ── ENGLISH ────────────────────────────────────────────────────────────
    {
        "lang": "ENGLISH",
        "name": "Full sales flow — luxury buyer, Surat premium 4BHK",
        "turns": [
            "I'm looking in Surat",
            "4BHK, something really premium",
            "Budget is around 3 crore, flexible",
            "This is for my own home, not investment",
            "What makes Agastya special exactly?",
            "Sounds good. I'm Rajan. Can I visit this weekend?",
        ],
    },
    {
        "lang": "ENGLISH",
        "name": "FAQ handling — caller asks lots of questions before committing",
        "turns": [
            "Looking in Silvassa, something peaceful",
            "Is it RERA registered?",
            "What's the possession timeline?",
            "Are there schools nearby?",
            "Okay, my wife and I want to see it. Saturday works. I'm Deepak.",
        ],
    },
    # ── CODE-SWITCHING ──────────────────────────────────────────────────────
    {
        "lang": "HINGLISH",
        "name": "Code-switching — caller mixes Hindi + English naturally",
        "turns": [
            "Hi, Surat mein 3BHK chahiye yaar",
            "Investment ke liye, good ROI wala",
            "Budget around 80 lakh to 1 crore",
            "Yeh Aranya II wala kaisa hai? IT corridor mein hai na?",
            "Okay sounds solid. Main Karan hoon. Saturday visit kar sakta hoon?",
        ],
    },
    {
        "lang": "HINGLISH",
        "name": "Caller starts English, switches to Hindi mid-way",
        "turns": [
            "Hi, I want a flat in Surat",
            "3BHK, budget around 1.2 crore",
            "Actually yaar, self-use ke liye chahiye, family ke saath rehna hai",
            "Satva kaisa hai? Schools ke paas hai na?",
            "Haan theek hai, site dekh lete hain. Mera naam Rohit hai.",
        ],
    },
]


async def run_scenario(scenario: dict, index: int) -> None:
    lang_color = {"HINDI": "\033[96m", "GUJARATI": "\033[95m",
                  "ENGLISH": "\033[94m", "HINGLISH": "\033[93m"}.get(scenario["lang"], Y)

    print(f"\n{lang_color}{'━'*70}{RST}")
    print(f"{lang_color}[{scenario['lang']}] {index+1}. {scenario['name']}{RST}")
    print(f"{lang_color}{'━'*70}{RST}")

    agent = VoiceAgent()
    opening = await agent.generate_opening()
    print(f"\n{G}Vishal:{RST} {opening.text}\n")

    for turn in scenario["turns"]:
        print(f"{R}Caller:{RST} {turn}")
        chunks = []
        async for result in agent.process_stream(turn):
            chunks.append(result.text)
        print(f"{G}Vishal:{RST} {' '.join(chunks)}\n")
        await asyncio.sleep(0.2)

    lead = agent.memory.lead
    summary = lead.summary_for_prompt()
    print(f"{DIM}Lead: {summary}{RST}")


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set")
        sys.exit(1)

    if len(sys.argv) > 1:
        idx = int(sys.argv[1]) - 1
        await run_scenario(SCENARIOS[idx], idx)
        return

    for i, s in enumerate(SCENARIOS):
        await run_scenario(s, i)


if __name__ == "__main__":
    asyncio.run(main())
