# Call Audit System

Records caller audio, full transcripts, and metadata for every phone call.

---

## Architecture

The audit system is a thin observability layer that hooks into the existing pipeline at three points:

```
FreeSWITCH → ws_audio_receiver.py
                 ↓ (after PCMU decode + 8→16kHz resample)
             audit_manager.push_audio()          ← normalized PCM16 @16kHz
                 ↓
             call_bridge → pipeline.py
                               ↓ STT transcript produced
                           hub.emit({"type":"user"})
                               ↓ LLM response assembled
                           hub.emit({"type":"agent"})
                 ↓
             conversation_call_bridge._event_loop()
                 ↓
             audit_manager.add_event("Caller"|"Agent", text)
```

**Audio** is captured in `ws_audio_receiver.py` immediately after normalization — after µ-law decoding and resampling — so what is saved is exactly what the STT engine receives: PCM16, mono, 16 kHz.

**Transcripts** are captured via the existing `EventHub` pub/sub system. The audit module subscribes alongside existing consumers and writes entries on every `user` (caller) and `agent` event.

**Metadata** is written when the call ends via `CallAuditManager.close()`, which is called from `TelephonySession.stop()`.

All audio writes go through a bounded `asyncio.Queue` and a background coroutine — they never block the WebSocket handler, STT, TTS, or any audio queues.

---

## Enabling / Disabling

Set the environment variable before starting the runtime:

```bash
# Enable
export ENABLE_CALL_AUDIT=true

# Disable (default)
export ENABLE_CALL_AUDIT=false
```

When `ENABLE_CALL_AUDIT=false` (the default), all audit code paths exit immediately at the first line of each function. There is zero runtime overhead.

The variable is already present in `calling-agent/.env`:

```
ENABLE_CALL_AUDIT=false
```

---

## Recording Locations

```
recordings/
└── YYYYMMDD/
    └── <call_id>/
        ├── caller_audio.wav   — complete caller-side audio
        ├── transcript.txt     — timestamped conversation log
        └── metadata.json      — call metadata
```

`recordings/` is created next to `ws_audio_receiver.py` (i.e. `/home/angelo/recordings/`).

To use a different path:

```bash
export CALL_AUDIT_DIR=/mnt/nas/recordings
```

### caller_audio.wav

- Format: WAV, PCM16, mono, 16 kHz
- Contains all caller audio for the entire call duration (including silence between utterances)
- This is the exact signal sent to the STT engine, after µ-law decoding and resampling

### transcript.txt

Chronological log of all caller and agent speech:

```
[00:05.231]
Caller: Hello

[00:08.112]
Agent: Hi, thank you for answering...

[00:12.551]
Caller: I am interested in property options.
```

Timestamps are `[MM:SS.mmm]` relative to the start of the call.

### metadata.json

```json
{
  "call_id": "abc12345",
  "start_time": "2026-05-29T14:30:00.000000+00:00",
  "end_time": "2026-05-29T14:35:47.213000+00:00",
  "duration_seconds": 347.213,
  "language_detected": "hi",
  "stt_provider": "sarvam",
  "fallback_used": false,
  "transcript_count": 18
}
```

---

## How to Listen to Recordings

Any standard audio player handles the WAV files:

```bash
# Play in terminal
aplay recordings/20260529/abc12345/caller_audio.wav

# Play with ffplay
ffplay recordings/20260529/abc12345/caller_audio.wav

# Convert to MP3 for easier sharing
ffmpeg -i recordings/20260529/abc12345/caller_audio.wav output.mp3

# Inspect file info
ffprobe recordings/20260529/abc12345/caller_audio.wav
```

---

## Troubleshooting

### No recordings directory created

Check that `ENABLE_CALL_AUDIT=true` is exported **before** `ws_audio_receiver.py` starts. The `start_all.sh` script sources `calling-agent/.env`, so editing that file is sufficient.

### WAV file is empty or very small

The WAV header is written at session open; frames stream in during the call. If the process was killed mid-call, the WAV file may be unplayable because the header frame-count field was not updated. To repair:

```bash
ffmpeg -f s16le -ar 16000 -ac 1 -i /tmp/raw_frames.raw repaired.wav
```

For clean shutdowns (SIGTERM / call hangup), the file is always properly closed.

### transcript.txt is missing entries

Agent text requires that the EventHub receives `{"type": "agent"}` events. These are emitted by `pipeline.py _conversation_layer` after each complete agent response. If the conversation engine fails mid-response, the partial text is not emitted.

### Disk usage

At 16 kHz / PCM16 / mono:
- 32 KB/second
- ~1.9 MB/minute
- ~115 MB/hour of caller audio

Plan storage accordingly. The `recordings/` tree can be pruned safely at any time — it is write-once (no file is read back by the system).

---

## Disabling Mid-Production

To disable without restarting:

1. Set `ENABLE_CALL_AUDIT=false` in `calling-agent/.env`
2. Restart `ws_audio_receiver.py` (or run `stop_all.sh && start_all.sh`)

Active calls at the moment of restart will lose their in-progress recording. Completed calls are not affected.
