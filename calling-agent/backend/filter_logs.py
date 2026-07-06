"""Tail and filter logs/latest.log by tag while the agent is running.

Usage:
    python filter_logs.py                  # follow everything
    python filter_logs.py VAD STT          # follow only lines containing [VAD] or [STT]
    python filter_logs.py --no-follow TTS  # print existing matches and exit
"""
import sys
import time
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent / "logs" / "latest.log"

# Common tags emitted across the pipeline, for reference:
#   PIPELINE, VAD, TURN, STT, STT:Sarvam, TTS, TTS:Sarvam, CONV, WEB, WARMUP,
#   OPENING, BARGE-IN, ECHO-GATE, ECHO-MUTE, LocalMicSource, LocalSpeakerSink


def main() -> None:
    args = sys.argv[1:]
    follow = "--no-follow" not in args
    tags = [a for a in args if not a.startswith("--")]

    def matches(line: str) -> bool:
        if not tags:
            return True
        return any(f"[{tag}" in line for tag in tags)

    if not LOG_FILE.exists():
        print(f"No log file yet at {LOG_FILE} — start run.py first.", file=sys.stderr)
        sys.exit(1)

    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if matches(line):
                print(line, end="")

        if not follow:
            return

        while True:
            line = f.readline()
            if not line:
                time.sleep(0.3)
                continue
            if matches(line):
                print(line, end="")


if __name__ == "__main__":
    main()
