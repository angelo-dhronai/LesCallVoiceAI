# Phase 12 — current state (snapshot 2026-05-09)

## TL;DR
Phase 12 is **functionally complete and validated end-to-end on a real call**.
Pipeline does: caller speech → Sarvam STT → orchestrator → mock LLM → mock TTS
WAV → playback injection into the live ESL conn → caller hears AI response.

The "robotic sound" the operator heard during the validating call IS the
mock TTS pitch-tone synth working correctly. To get real voice replies
swap `TTS_PROVIDER=mock` → `TTS_PROVIDER=sarvam` and re-run.

## Files (live)
- `/home/angelo/socket_server.py`             — added Phase-12 playback listener (port 9004), call registry, send_lock; added `build_playback_file_payload`; UUID bridge in `build_audio_stream_payload`. Phase 1–11A paths unchanged byte-for-byte.
- `/home/angelo/ws_audio_receiver.py`         — added URL-path UUID parse so WS conn_id == FreeSWITCH Channel-Call-UUID.
- `/home/angelo/stt_stream_worker.py`         — added `_OrchForwarder` (fire-and-forget). FINAL transcripts forwarded to orchestrator.
- `/home/angelo/conversation_orchestrator.py` — NEW. Asyncio TCP server :9003. FSM, mock + openai LLM scaffolds, mock + sarvam TTS scaffolds, JSONL persistence, PHASE-12 ORCHESTRATOR SUMMARY.

SHA-256 of the validated versions: `phase12-state/source-snapshot/sha256.txt`.

## Resume
```
/home/angelo/phase12-state/start_phase12.sh        # boot all four
# place call
pkill -f 'socket_server\.py|ws_audio_receiver\.py|stt_stream_worker\.py|conversation_orchestrator\.py'   # stop
```

Switch providers per-run by exporting before invoking, e.g.:
```
TTS_PROVIDER=sarvam /home/angelo/phase12-state/start_phase12.sh
```

## Validation evidence (real call, run3)
| Layer | Verdict |
|---|---|
| Phase 9 audio fork | CASE WS-G — STREAM HEALTHY, 16001 bps, no stalls |
| Phase 10 STT pipeline | CASE STT-E — 11 finals, 0 drops |
| Phase 11A Sarvam | CASE STT-SARVAM-G — 1 connect, 0 errors |
| **Phase 12 orchestrator** | **CASE ORCH-A — transcripts=11 turns=11 playbacks=11** |

Logs: `phase12-state/logs/phase12_logs.run{1,2,3}/`.

## Two issues encountered (and their fix history)
1. **conn_id mismatch** (run 1) — ws_audio_receiver minted random short id, didn't match the FS UUID the playback registry was keyed by. **Fixed**: socket_server appends `/<uuid>` to the WS URL; receiver parses leading path segment as conn_id.
2. **mod_audio_stream rejects `?query`** (run 2) — its `validate_ws_uri` (audio_streamer_glue.cpp ~L516) accepts only digits between `:` and `/` for the port; URLs with `?` silently rejected. **Fixed**: use path segment, not query string. Run 3 confirmed.

## Known pre-existing issue (unrelated to Phase 12, deferred)
`/tmp/esl_recordings/` is empty after each call even though `CHANNEL_EXECUTE app=record_session` confirms ESL-side attach. The dialplan-side recording at `/tmp/dialplan_test.wav` works fine. Phase-7 Decision Engine prints `CASE R1`. Not caused by Phase 12 — this was R1 in `phase12_logs.run1` BEFORE the bridge was added. Triage TODO when next picked up.

## Open task on resume
- **Task #8 (pending)**: Triage empty `/tmp/esl_recordings` (Phase-7 R1 — not blocking Phase 12).

## Process boundary unchanged
Each of the four files owns exactly one responsibility:
- socket_server: ESL ownership, playback execution, recording
- ws_audio_receiver: WS audio reception
- stt_stream_worker: streaming STT
- conversation_orchestrator: LLM + TTS + playback request orchestration

Failure isolation invariants (per-process): orchestrator can crash without affecting STT; STT can crash without affecting receiver; receiver can crash without affecting ESL; orchestrator unavailable → Phase-11 pipeline still healthy.
