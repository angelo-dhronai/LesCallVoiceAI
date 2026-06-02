Executive Summary

  This document provides a comprehensive architectural analysis of the voice calling agent project located in
  /home/angelo/lescall/calling-agent/backend/app/voice. The system is an inbound real estate sales agent for Pramukh Group,
  named Vishal Patel, designed to guide callers through a 6-stage consultative sales flow
  (GREETING → DISCOVERY → PROJECT_MATCH → INVITE_VISIT → CONFIRMATION → CLOSING). The agent answers the call with
  a bilingual inbound welcome ("Hello! Namaste — this is Vishal Patel calling from Pramukh Group. Hope I haven't
  caught you at a bad time."), detects the caller's language from their first response, and mirrors it throughout
  the conversation.

  The architecture follows a modular pipeline design with clear separation of concerns: audio input, voice activity
  detection (VAD), speech-to-text (STT) with fallback and post-processing, conversation routing/engine, text-to-speech
  (TTS) with streaming capabilities, and turn management. The system uses asynchronous processing and queues to ensure
  the audio capture loop never blocks on downstream processing, enabling real-time voice interaction with barge-in
  support.

  Key characteristics include:
  - Language handling: Robust detection and switching between English, Hindi, and Gujarati (both script and
  transliterated)
  - Deterministic shortcuts: Zero-latency responses for common phrases before LLM invocation
  - Parallel LLM execution: Background LLM processing with filler responses to mask latency
  - Structured memory: Tracks conversation state, booking data, and context for LLM prompting
  - Interruption handling: Barge-in detection with cooldown to allow user interruption during TTS playback
  - Fallback mechanisms: Multiple STT/TTS providers with automatic fallback on failure

  Architecture Overview

  End-to-End Call Flow (Production Telephony Path)

  0. FreeSWITCH receives inbound call → answers → dispatches uuid_audio_stream to mod_audio_stream
  0b. mod_audio_stream opens WebSocket to ws_audio_receiver.py (port 9001)
  0c. ws_audio_receiver.py calls conversation_call_bridge.get_or_create() → TelephonySession.start()
  0d. VoicePipeline.start() runs; greeting enqueued to TTS queue immediately
  1. Audio Capture: TelephonySource receives PCM frames pushed by ws_audio_receiver.py (PCMU→PCM16, 8k→16k)
  2. Voice Activity Detection: VAD processes frames to detect speech/silence boundaries with calibration
  3. Turn Management: TurnManager accumulates speech and triggers on semantic completion + silence
  4. Speech-to-Text: STTRouter attempts primary (Sarvam saarika:v2.5) then fallback (OpenAI Whisper) transcription
  5. Post-processing: Domain-specific corrections applied to STT output (city names, BHK, phonetic corrections)
  6. Conversation Routing: ConversationRouter detects intent, manages stage flow, extracts structured data
  7. LLM Processing: OpenAI GPT-4o-mini generates responses with stage-aware system prompts (streaming)
  8. Text-to-Speech: SarvamTTS (bulbul:v3) streams PCM to TelephonySink → WAV file → FreeSWITCH playback
  9. Event Broadcasting: EventHub decouples pipeline from WebSocket clients for monitoring

  Production Telephony Bridge Architecture

  FreeSWITCH (inbound call)
      ↓ mod_audio_stream WebSocket (port 9001)
  ws_audio_receiver.py  — PCMU→PCM16, 8k→16k normalization; pushes frames to call_bridge
      ↓ conversation_call_bridge.py
  TelephonySession (one per conn_id)
      ↓ TelephonySource.push_frame()   ↑ TelephonySink.wav_queue
  VoicePipeline (pipeline.py)
      → audio_vad_layer → stt_worker → conversation_layer → tts_layer
  TelephonySink.flush() → WAV file → conversation_call_bridge._playback_loop()
      ↓ TCP 127.0.0.1:9004 (length-prefixed JSON playback_request)
  socket_server.py (ESL controller) → bgapi uuid_audio_stream playback → FreeSWITCH

  Key process boundaries: ws_audio_receiver.py and conversation_call_bridge.py are
  in the same Python process (ws_audio_receiver imports from conversation_call_bridge).
  socket_server.py runs as a SEPARATE process communicating over TCP port 9004.

  Audio Ingestion Flow

  FreeSWITCH/mod_audio_stream → ws_audio_receiver.py (decode PCMU, resample to 16k)
                                    ↓ TelephonySource.push_frame()
                                 TelephonySource queue → VAD → TurnManager (speech detection)
                                                             ↓
                                                     Audio buffered during speech
                                                             ↓
                                                     On turn trigger: AudioSegment → STT Worker

  STT Flow

  AudioSegment → STTRouter.transcribe()
                ↓
          Try primary (SarvamSTT) with retries
                ↓
          On failure/empty: fallback (OpenAIWhisperSTT)
                ↓
          Transcript → validate_transcript() (hallucination/filtering)
                ↓
          post_process_transcript() (domain corrections)
                ↓
          Clean transcript → ConversationRouter.process_stream()

  VAD Flow

  VAD uses energy-based detection with adaptive calibration:
  1. Calibration phase: Collect first N frames to establish noise floor
  2. Threshold = max(min_threshold, min(calibrated_multiplier * noise_p95, max_threshold))
  3. Speech detected when energy > threshold AND energy > minimum_rms_floor
  4. Barge-in uses stricter threshold (threshold * barge_in_multiplier) during TTS playback

  Turn Management Flow

  TurnManager triggers when ALL conditions met:
  1. Silence duration ≥ silence_threshold_ms (configurable, default 600ms)
  2. Semantic completion:
    - Ends with sentence punctuation OR
    - Word count ≥ min_words_short (3) with punctuation OR
    - Word count ≥ min_words_long (5) regardless of punctuation
  3. Additional grace_window_ms (default 150ms) after conditions 1+2 first met

  Conversation Engine Flow

  ConversationRouter.process_stream():
  1. Language detection/v3 (script → pattern → LLM fallback)
  2. Intent detection (deterministic, domain-specific)
  3. Fire LLM in background (parallel with deterministic routing)
  4. Deterministic shortcuts (yield + return immediately) for:
    - Farewell, objection (2-strike graceful close), gratitude, identity questions, language switches
  5. _extract_data_from_input(): side-effect extraction of location/BHK/time/purpose (non-blocking)
  6. _maybe_advance_stage(): soft stage progression based on collected data
  7. LLM handles all remaining turns with filler timeout support
  8. System prompt contains full Pramukh brand identity, portfolio, 9-stage guidelines, objection scripts

  Memory Flow

  ConversationMemory manages three layers:
  1. Short-term: Sliding window of recent turns (user/assistant pairs)
  2. Context: Extracted structured info (user name, intent, topic, sentiment)
  3. Booking: Pramukh Group appointment state (location, property type, dates, etc.)
  All layers injected into LLM prompts for context-aware responses.

  LLM Interaction Flow

  1. System prompt built with (_build_system_prompt()):
    - Agent identity: Vishal Patel, inbound Pramukh Group sales agent
    - Brand identity: 30+ years, 60+ projects, zero stalls, RERA compliant
    - Current call state: stage, language, all collected info so far
    - Stage-specific current goal (one of 6 stage goals)
    - Complete project portfolio: 15+ projects across Surat/Vapi/Silvassa with prices, USPs, best-fit guidance
    - Quick match guide (budget/location/config → project)
    - 9-stage flow guidelines (soft instruction, not rigid gates)
    - Objection handling scripts (brochure, price, family, comparing, reliability)
    - Universal rules (one question per turn, 3-4 lines max, two time options, no IVR tone)
    - Language-specific instruction (highest priority, never refuse to switch)
    - Known context from memory
  2. Messages: System prompt + conversation history (sliding window: last N*2 turns)
  3. Streaming response with sentence-level chunking for low-latency TTS
  4. LLM_MAX_TOKENS = 200 (raised from 150 for richer inbound responses)

  TTS Flow

  StreamingTTS implementations:
  - SarvamTTS: HTTP stream endpoint to Sarvam Bulbul API, linear16 PCM chunks
  - CartesiaTTS: WebSocket connection to Cartesia Sonic-2 model, PCM_s16le chunks
  Both implement interruptible playback with sink.write_frame() audio output.

  Event Flow

  EventHub provides async pub/sub:
  - Pipeline layers emit events (user transcript, agent response, mic state, etc.)
  - WebSocket clients subscribe to receive real-time updates
  - Non-blocking, drop-oldest on queue overflow to prevent pipeline blocking

  Threading/Concurrency Model

  - Audio layer: Dedicated task for microphone capture and VAD processing
  - STT layer: Dedicated worker for transcription with provider fallback
  - Conversation layer: Dedicated task for LLM processing with streaming output
  - TTS layer: Dedicated task for audio playback with interruption handling
  - All layers communicate via bounded asyncio.Queues
  - Background tasks spawned per turn for STT→LLM→TTS processing
  - Main audio loop never blocks on downstream processing (uses put_nowait)
  - Barge-in detection flushes pipeline queues and cancels current generation

  File-by-File Analysis

  init.py

  - Purpose: Public interface for voice module
  - Responsibilities: Exports all public classes and functions
  - Key Classes: None (import/re-export only)
  - Key Functions: None
  - Inputs: None
  - Outputs: Exported symbols
  - Dependencies: All submodules in voice package
  - Who calls it: External modules importing voice package
  - What it calls: None (import only)
  - Critical logic paths: None
  - Risks if modified: Breaking changes to public API

  conversation.py

  - Purpose: Inbound real estate sales agent (Vishal Patel) for Pramukh Group — LLM-driven with soft stage guidance
  - Responsibilities:
    - Manage 6 inbound call stages: GREETING → DISCOVERY → PROJECT_MATCH → INVITE_VISIT → CONFIRMATION → CLOSING
    - Detect intents and language; lock language from caller's first response
    - Extract structured data (location, BHK, time, purpose) as non-blocking side effects
    - Handle universal shortcuts: farewell, objection (2-strike), gratitude, identity, language switch
    - Let LLM drive all other turns using a comprehensive system prompt (9-stage script, full portfolio, rules)
    - Soft stage advancement: progress when data is collected, not on rigid gates
  - Key Classes:
    - ConversationState: Tracks stage, language, turn count, confidence, discovery_turns, purpose, project_recommended
    - ConversationRouter: Main processing with minimal deterministic routing; LLM as primary responder
  - Key Functions:
    - detect_intent(): Domain-specific intent classification (unchanged)
    - detect_language_v3(): Script → pattern → LLM fallback language detection (unchanged)
    - _extract_data_from_input(): Side-effect extraction of location/BHK/time/purpose per turn
    - _maybe_advance_stage(): Soft stage progression based on collected data
    - _build_system_prompt(): Rich inbound prompt (brand, portfolio, stage goal, collected info, rules)
    - process_stream(): Simplified routing — shortcuts only, then LLM for everything else
    - generate_opening(): Inbound warm welcome (bilingual EN+HI)
  - Stage constants: STAGE_GREETING, STAGE_DISCOVERY, STAGE_PROJECT_MATCH, STAGE_INVITE_VISIT,
    STAGE_CONFIRMATION, STAGE_CLOSING
  - Removed (outbound-only): STAGE_PROJECT_CONFIRM, STAGE_PROPERTY_STATUS, STAGE_RENT_OR_BUY,
    STAGE_PROPERTY_INFO, STAGE_INTEREST_CONFIRM, STAGE_INTEREST_RECONFIRM, STAGE_LOCATION,
    STAGE_PROPERTY_TYPE, STAGE_SCHEDULING; and all rigid per-stage deterministic routing
  - Inputs: User transcript text, conversation state
  - Outputs: EngineResult (text, is_shortcut, intent)
  - Dependencies:
    - memory.py (ConversationMemory, BookingData)
    - stt.post_processor (match_location, match_bhk)
    - openai (AsyncOpenAI)
  - Who calls it:
    - pipeline.py (via ConversationRouter)
  - What it calls:
    - LLM API (via _call_llm_stream)
    - Memory system (add_user, add_assistant)
  - Critical logic paths:
    - Language locking on first caller response to bilingual greeting
    - Universal shortcuts (farewell/objection/gratitude/identity/language_switch)
    - _extract_data_from_input() side effects on every non-shortcut turn
    - _maybe_advance_stage() soft progression
    - _build_system_prompt() — must include accurate stage goal and collected data
  - Risks if modified:
    - Language detection regressions
    - System prompt quality degradation (LLM is now primary responder)
    - Stage advancement logic (too eager or too slow to advance)
    - Intent misclassification (shortcuts firing incorrectly)

  conversation_engine.py

  - Purpose: Abstract base class for conversation engines
  - Responsibilities:
    - Define interface for LLM conversation systems
    - Provide mock implementation for testing
  - Key Classes:
    - ConversationEngine: Abstract base with respond() method
    - MockConversationEngine: Deterministic keyword-based responses
  - Key Functions:
    - respond(): Generate response and update history
    - add_message(): Add message to history
  - Inputs: System prompt (constructor), user text (respond)
  - Outputs: Assistant response text
  - Dependencies:
    - abc (ABC, abstractmethod)
    - dataclasses
    - logging
  - Who calls it:
    - main_loop.py (dependency injection)
    - pipeline.py (via ConversationEngine type)
  - What it calls: None (abstract/mock)
  - Critical logic paths:
    - Mock engine keyword matching
  - Risks if modified:
    - Breaking interface contract
    - Affecting testability

  conversation_engine_llm.py

  - Purpose: Production LLM conversation engine using OpenAI GPT-4o-mini
  - Responsibilities:
    - Generate responses with stage-aware system prompts
    - Implement sliding window memory (last N turns)
    - Non-blocking async API calls
  - Key Classes:
    - LLMConversationEngine: Extends ConversationEngine
  - Key Functions:
    - build_system_prompt(): Construct role/goal/stage+language+memory prompt
    - _build_messages(): Combine system prompt with conversation window
    - respond(): Main LLM call with error handling
  - Inputs: User text, stage, language, memory
  - Outputs: LLM response text
  - Dependencies:
    - openai (AsyncOpenAI)
    - conversation_engine.py
    - memory.py (ConversationMemory)
    - os, logging
  - Who calls it:
    - main_loop.py (when OPENAI_API_KEY available)
    - pipeline.py (via ConversationEngine)
  - What it calls:
    - OpenAI API (chat.completions.create)
    - Memory system (get_history_for_llm)
  - Critical logic paths:
    - System prompt injection with booking state
    - Sliding window history management
    - Language enforcement in system prompt
    - Error handling and fallback responses
  - Risks if modified:
    - Breaking LLM interaction
    - Incorrect system prompt leading to off-topic responses
    - History window too small/large affecting context
    - API key handling issues

  event_hub.py

  - Purpose: Async pub/sub broadcaster for pipeline events
  - Responsibilities:
    - Decouple pipeline producer from WebSocket consumers
    - Provide bounded queues with drop-oldest overflow handling
    - Enable non-blocking event broadcasting
  - Key Classes:
    - EventHub: Manage subscriber queues
  - Key Functions:
    - subscribe(): Create and register subscriber queue
    - unsubscribe(): Remove subscriber
    - emit_nowait(): Broadcast event to all subscribers without blocking
  - Inputs: Event dictionaries
  - Outputs: None (side effect: queueing events)
  - Dependencies:
    - asyncio
    - logging
    - typing
  - Who calls it:
    - pipeline.py (event emission)
    - Potentially WebSocket clients (subscription)
  - What it calls:
    - asyncio.Queue operations (put_nowait, get_nowait)
  - Critical logic paths:
    - Queue full handling (drop oldest then retry)
    - Event broadcasting to multiple subscribers
  - Risks if modified:
    - Breaking event delivery to monitors
    - Queue size misconfiguration causing memory issues
    - Blocking the pipeline if emit becomes synchronous

  main_loop.py

  - Purpose: Main voice loop orchestrator for real-time pipeline
  - Responsibilities:
    - Coordinate audio input → VAD → turn detection → STT → LLM → TTS
    - Handle barge-in detection with cooldown
    - Manage background task lifecycle per turn
    - Provide entry point and signal handling
    - Support test mode for VAD calibration
  - Key Classes:
    - VoiceLoop: Main orchestration logic
  - Key Functions:
    - _process_frame(): Process each audio frame (VAD, buffering, barge-in)
    - _check_turn_trigger(): Detect turn boundaries
    - _process_turn(): Background task for STT→LLM→TTS processing
    - _speak() and _cancel_tts(): TTS playback management
    - run(): Main async loop
    - _run_test(): VAD calibration test mode
    - main(): Entry point
  - Inputs: Audio frames from microphone
  - Outputs: Audio output to speaker, events to hub
  - Dependencies:
    - All voice submodules (audio_input, vad, stt.router, tts_stream, turn_manager, conversation_engine, shortcuts)
    - audio_transport modules
    - asyncio, logging, signal, sys, time
  - Who calls it:
    - Direct execution (python -m voice.main_loop)
    - External process managers
  - What it calls:
    - Audio input frames() iterator
    - VAD.is_speech()
    - STTRouter.transcribe() (background)
    - conversation.respond() (background)
    - tts.speak() and tts.stop()
    - turn_manager.update_text() and should_trigger()
    - shortcuts.check_shortcut()
  - Critical logic paths:
    - Barge-in detection during TTS playback
    - Turn triggering logic (silence + semantic completion)
    - Background task management and cleanup
    - Audio buffer overflow handling
    - Frame processing pipeline (VAD → buffering → turn detection)
  - Risks if modified:
    - Breaking real-time audio pipeline
    - Incorrect barge-in leading to clipped audio or missed interruptions
    - Turn detection too sensitive/insensitive causing choppy flow
    - Resource leaks from uncleared background tasks
    - Audio buffer overflow causing distortion

  memory.py

  - Purpose: Conversation memory system (short-term + context + booking data)
  - Responsibilities:
    - Manage sliding window of recent turns
    - Extract structured context (user name, intent, topic, sentiment)
    - Track Pramukh Group booking state (location, property type, dates, etc.)
    - Provide context for LLM prompts
  - Key Classes:
    - Turn: Single conversation turn (role + text)
    - BookingData: Structured booking state with summary and missing field detection
    - ContextMemory: Extracted conversation context
    - ConversationMemory: Main manager coordinating all three layers
  - Key Functions:
    - add_user() / add_assistant(): Add turns and extract context
    - get_history_for_llm(): Format history for LLM consumption
    - _extract_context(): Parse user text for name, intent, topic, sentiment
    - BookingData.summary() and next_missing_field(): Booking state introspection
    - reset(): Clear all memory
  - Inputs: User and assistant text turns
  - Outputs:
    - Formatted history for LLM
    - Context strings for system prompts
    - Booking state summary
  - Dependencies:
    - re (regular expressions)
    - logging
    - dataclasses
  - Who calls it:
    - conversation.py (memory injection)
    - conversation_engine_llm.py (history for LLM)
    - pipeline.py (via ConversationRouter)
  - What it calls:
    - re module for pattern matching
    - logging for extraction events
  - Critical logic paths:
    - Name extraction (blocklist to avoid false positives)
    - Intent/topic detection via keyword mapping
    - Sentiment analysis (positive/negative word sets)
    - Booking data extraction and validation
  - Risks if modified:
    - Incorrect context extraction leading to poor LLM responses
    - Booking state corruption causing flow errors
    - Memory leaks from uncleared history
    - Over-aggressive trimming losing relevant context

  pipeline.py

  - Purpose: Production voice pipeline with independent async layers
  - Responsibilities:
    - Orchestrate audio → VAD → STT → conversation → TTS via async queues
    - Ensure audio loop never blocks on downstream processing
    - Provide failure isolation (one layer failure doesn't crash others)
    - Handle barge-in, echo cancellation, and telephony mode tuning
    - Warm up STT connections to reduce first-turn latency
  - Key Classes:
    - VoicePipeline: Main pipeline orchestrator
    - AudioSegment, Transcript, Response: Data transfer objects between layers
  - Key Functions:
    - _audio_vad_layer(): Audio capture, VAD, turn detection
    - _stt_worker(): Transcription with primary/fallback providers
    - _conversation_layer(): Transcript processing with streaming LLM output
    - _tts_layer(): Response playback with barge-in handling
    - _process_vad_frame(): Core VAD processing with barge-in and echo logic
    - _flush_pipeline_queues(): Queue draining for barge-in
    - start() / stop() / run_until_shutdown(): Pipeline lifecycle
    - _generate_keyboard_sound(): Generates ~700ms of typing-click PCM (currently DISABLED)
    - _play_keyboard_loop(): Loops keyboard sound while LLM processes (currently DISABLED via kb_stop.set())
  - Inputs: Audio from microphone, configuration via constructor/environment
  - Outputs:
    - Audio output to speaker
    - Events to EventHub
    - Logs for monitoring
  - Dependencies:
    - All voice submodules (audio_input, vad, stt.router, tts_*, turn_manager, conversation, memory, event_hub)
    - audio_transport.local_* modules
    - asyncio, logging, os, signal, time, dataclasses, json, httpx, sounddevice, numpy, websockets
    - Environment variables (OPENAI_API_KEY, SARVAMAI_API_KEY, CARTESIA_API_KEY, ENABLE_BARGE_IN, TELEPHONY_MODE)
  - Who calls it:
    - main_loop.py (indirectly via VoiceLoop)
    - External process managers
  - What it calls:
    - Audio source frames() iterator
    - VAD.is_speech() and barge_in_threshold
    - STTRouter.transcribe()
    - conversation.process_stream() and generate_opening()
    - tts.speak() and tts.stop()
    - turn_manager.should_trigger() and update_text()
    - shortcuts.check_shortcut()
    - EventHub.emit_nowait()
    - Audio sink start()/write_frame()/stop()/flush()
  - Key tuning constants (updated 2026-05-29):
    - MIN_SPEECH_MS = 300ms (was 700ms) — short caller responses like "haan"/"yes"/"ha" now reach STT
    - BARGE_IN_MIN_FRAMES = 3 (was 5) — fewer consecutive loud frames needed to interrupt agent
    - Barge-in elapsed cooldown = 250ms (was 600ms) — caller can speak sooner after agent starts
    - SPEECH_ONSET_MIN_FRAMES = 2 (was 3) — speech detection triggers one frame sooner
  - Critical logic paths:
    - Barge-in detection during TTS (BARGE_IN_MIN_FRAMES + 250ms elapsed)
    - Echo cooldown after TTS playback (TTS_ECHO_COOLDOWN_MS = 80ms)
    - Telephony mode VAD tuning (higher thresholds when TELEPHONY_MODE=true)
    - Queue full handling (drop oldest then retry)
    - Connection warm-up for STT providers
    - Pipeline queue draining on barge-in
    - MIN_SPEECH_MS gate before committing audio segment to STT
  - Risks if modified:
    - Breaking real-time pipeline synchronization
    - Incorrect barge-in leading to audio artifacts or missed interruptions
    - MIN_SPEECH_MS too low → garbage single-word STT noise reaches conversation layer
    - MIN_SPEECH_MS too high → short valid responses dropped before STT
    - Queue size misconfiguration causing latency or memory issues
    - VAD calibration errors in telephony mode

  stt/init.py

  - Purpose: Public interface for STT submodule
  - Responsibilities: Export STT providers, router, and post-processor
  - Key Classes: None (import/re-export only)
  - Key Functions: None
  - Inputs: None
  - Outputs: Exported symbols
  - Dependencies: All STT submodules
  - Who calls it:
    - voice module imports (pipeline.py, main_loop.py, conversation.py)
  - What it calls: None (import only)
  - Critical logic paths: None
  - Risks if modified: Breaking changes to STT public API

  stt/base.py

  - Purpose: Base interface for STT providers
  - Responsibilities:
    - Define STTProvider abstract base class
    - Provide WAV conversion utility (pcm_to_wav)
    - Define STTResult data class
  - Key Classes:
    - STTProvider: Abstract base with transcribe() method
    - STTResult: Transcript with confidence score
  - Key Functions:
    - pcm_to_wav(): Convert PCM16 to WAV in memory
  - Inputs: PCM16 audio bytes, sample rate, language hint
  - Outputs: STTResult (text, confidence)
  - Dependencies:
    - io, wave (for WAV conversion)
    - abc (ABC, abstractmethod)
    - dataclasses
    - logging
  - Who calls it:
    - STT implementations (SarvamSTT, OpenAIWhisperSTT)
    - STTRouter (via provider.transcribe())
  - What it calls:
    - wave module for WAV creation
    - io.BytesIO for in-memory buffer
  - Critical logic paths:
    - WAV header creation with correct parameters
    - PCM16 to WAV conversion
  - Risks if modified:
    - Breaking STT provider interface
    - Incorrect WAV causing provider failures
    - Removing confidence score breaking downstream filtering

  stt/sarvam.py

  - Purpose: Sarvam AI Speech-to-Text provider
  - Responsibilities:
    - Transcribe audio using Sarvam REST API
    - Convert PCM to WAV for multipart upload
    - Map language codes to Sarvam format (xx-IN)
    - Provide connection warming and cleanup
  - Key Classes:
    - SarvamSTT: Extends STTProvider
  - Key Functions:
    - transcribe(): Main transcription with WAV conversion and API call
    - warmup(): Pre-open TLS connection
    - close(): Close HTTP client
  - Inputs: PCM16 audio bytes, sample rate, language hint
  - Outputs: STTResult (transcript, confidence from Sarvam response)
  - Dependencies:
    - httpx (AsyncClient)
    - os (environment variable for API key)
    - stt.base (STTProvider, STTResult, pcm_to_wav)
    - logging
  - Who calls it:
    - STTRouter (as primary provider)
  - What it calls:
    - Sarvam API endpoint (/speech-to-text)
    - httpx.AsyncClient for HTTP requests
    - pcm_to_wav for audio conversion
  - Critical logic paths:
    - WAV conversion and multipart upload
    - Language code mapping (unknown → auto-detect, xx → xx-IN)
    - HTTP error handling and response parsing
    - Connection warming for reduced first-call latency
  - Risks if modified:
    - Breaking Sarvam API integration
    - Incorrect language mapping causing transcription failures
    - WAV conversion errors
    - API key handling issues
    - Missing error handling causing pipeline crashes

  stt/openai_whisper.py

  - Purpose: OpenAI Whisper Speech-to-Text provider (fallback)
  - Responsibilities:
    - Transcribe audio using OpenAI Whisper API
    - Convert PCM to WAV for API upload
    - Map language codes to Whisper format (ISO-639-1)
  - Key Classes:
    - OpenAIWhisperSTT: Extends STTProvider
  - Key Functions:
    - transcribe(): Main transcription with WAV conversion and API call
  - Inputs: PCM16 audio bytes, sample rate, language hint
  - Outputs: STTResult (transcript, confidence=1.0)
  - Dependencies:
    - openai (AsyncOpenAI)
    - os (environment variable for API key)
    - stt.base (STTProvider, STTResult, pcm_to_wav)
    - logging
  - Who calls it:
    - STTRouter (as fallback provider)
  - What it calls:
    - OpenAI API (audio.transcriptions.create)
    - AsyncOpenAI client
    - pcm_to_wav for audio conversion
  - Critical logic paths:
    - WAV conversion for API upload
    - Language code handling (unknown → omit parameter)
    - Error handling for API failures
  - Risks if modified:
    - Breaking Whisper API integration
    - Incorrect WAV causing API rejection
    - API key handling issues
    - Missing fallback causing pipeline to fail completely

  stt/router.py

  - Purpose: STT Router with primary/fallback, retries, filtering
  - Responsibilities:
    - Try primary provider (Sarvam) with retries
    - Fall back to secondary (OpenAI Whisper) on failure
    - Apply confidence threshold and transcript validation
    - Filter hallucinations, repetitions, unsupported scripts, garbage tokens
    - Apply post-processing corrections
  - Key Classes:
    - STTRouter: Main routing logic
  - Key Functions:
    - transcribe(): Main entry point with provider routing
    - _try_provider(): Attempt transcription with retries
    - _post_process(): Apply STT post-processing
    - validate_transcript(): Filter and clean transcript
  - Inputs: PCM16 audio bytes, sample rate, language hint
  - Outputs: Clean transcript string
  - Dependencies:
    - stt.base (STTProvider, STTResult)
    - stt.sarvam (SarvamSTT)
    - stt.openai_whisper (OpenAIWhisperSTT)
    - stt.post_processor (post_process_transcript)
    - re, collections.Counter, logging
    - rapidfuzz (fuzz for fuzzy location matching)
  - Who calls it:
    - pipeline.py (_stt_worker layer)
  - What it calls:
    - Provider transcribe() methods
    - validate_transcript() for filtering
    - post_process_transcript() for corrections
    - httpx/openai API calls via providers
  - Critical logic paths:
    - Confidence filtering (< MIN_CONFIDENCE rejected)
    - Validation pipeline (length, hallucinations, repetition, unsupported script, garbage)
    - Post-processing (BHK normalization, city corrections, language-specific fixes, phonetic corrections)
    - Provider retry logic (primary then fallback)
    - Fuzzy location matching in match_location()
  - Risks if modified:
    - Breaking STT pipeline causing empty transcripts
    - Over-aggressive filtering removing valid user input
    - Under-filtering allowing hallucinations/garbage to affect intent detection
    - Incorrect post-processing causing entity mis extraction
    - Provider retry logic errors causing infinite loops
    - Rapidfuzz dependency issues

  stt/post_processor.py

  - Purpose: STT post-processing for domain-specific vocabulary correction
  - Responsibilities:
    - Fix common misrecognitions of city names (Surat, Vapi, Silvassa)
    - Normalize BHK references (word-to-digit, Devanagari/Gujarati variants)
    - Apply Hindi/Gujarati common word corrections
    - Apply phonetic cross-language corrections (STT mistranscriptions)
    - Apply real estate term corrections
    - Provide fuzzy location matching
  - Key Classes: None (functional module)
  - Key Functions:
    - match_location(): Fuzzy match to canonical cities
    - match_bhk(): Extract BHK type from text
    - post_process_transcript(): Main correction pipeline
    - _apply_corrections(): Helper for dictionary-based replacements
    - _normalize_bhk(): BHK-specific normalization
  - Inputs: Raw transcript text, language hint
  - Outputs: Corrected transcript string
  - Dependencies:
    - re (regular expressions)
    - logging
    - rapidfuzz (fuzz for fuzzy matching)
  - Who calls it:
    - STTRouter (via _post_process())
  - What it calls:
    - re module for pattern matching and substitution
    - rapidfuzz.fuzz for location matching
  - Critical logic paths:
    - BHK normalization (convert variants to "bhk", word numbers to digits)
    - City name corrections (common misrecognitions to canonical names)
    - Language-specific corrections (Hindi/Gujarati common words)
    - Phonetic corrections (STT mistranscriptions like "shoe care" → "theek che")
    - Real estate corrections ("pramuk" → "pramukh", "sight visit" → "site visit")
    - Fuzzy location matching (ratio >=75 for location words)
  - Risks if modified:
    - Breaking entity extraction (location, BHK) causing flow errors
    - Over-correction changing valid user input
    - Under-correction leaving misrecognitions that affect intent detection
    - Incorrect phonetic mappings causing wrong corrections
    - Rapidfuzz performance impact

  tts_stream.py

  - Purpose: Streaming Text-to-Speech abstraction
  - Responsibilities:
    - Define StreamingTTS abstract base class
    - Provide MockStreamingTTS for testing
    - Define TTSChunk data class
  - Key Classes:
    - StreamingTTS: Async streaming TTS interface with interruption support
    - MockStreamingTTS: Simulates streaming with word-group chunks and delay
  - Key Functions:
    - speak(): Stream-synthesize and play text (must be cancellable)
    - stop(): Interrupt playback immediately
    - _reset(): Reset internal state
  - Inputs: Text to synthesize, audio sink
  - Outputs:
    - Audio chunks played via sink
    - Playing state and cancellation flags
  - Dependencies:
    - asyncio
    - logging
    - abc (ABC, abstractmethod)
    - dataclasses
    - audio_transport.base (AudioSink)
  - Who calls it:
    - pipeline.py (_tts_layer)
    - main_loop.py (via VoiceLoop)
  - What it calls:
    - Audio sink start()/write_frame()/stop()/flush()
    - asyncio.sleep() for chunk simulation (mock)
  - Critical logic paths:
    - Mock streaming: split text into word groups, play with delay
    - Interruption handling: check _cancelled flag during streaming
    - State reset: ensure clean state after playback/cancellation
  - Risks if modified:
    - Breaking TTS abstraction affecting all implementations
    - Incorrect interruption handling causing audio artifacts
    - Mock implementation not representative of real latency

  tts_sarvam.py

  - Purpose: Sarvam AI Bulbul streaming TTS via HTTP stream endpoint
  - Responsibilities:
    - Stream audio using Sarvam /text-to-speech/stream endpoint
    - Play audio through system speaker using sounddevice with true streaming
    - Minimize time-to-first-audio by writing chunks as they arrive
    - Handle connection management and retries
  - Key Classes:
    - SarvamTTS: Extends StreamingTTS
  - Key Functions:
    - speak(): Main streaming with HTTP POST and chunked playback
    - _stream_audio(): Generator yielding TTSChunks from HTTP stream
    - stop(): Interrupt playback and stop sink
    - close(): Close HTTP client
    - set_language(): Update target language for synthesis
  - Inputs: Text to synthesize, configuration (API key, model, speaker, etc.)
  - Outputs:
    - PCM16 audio chunks played via LocalSpeakerSink
    - Playing state and interruption handling
  - Dependencies:
    - httpx (AsyncClient)
    - os (environment variable for API key)
    - sounddevice (sd) and numpy for audio playback
    - tts_stream (StreamingTTS, TTSChunk)
    - audio_transport.local_speaker_sink (LocalSpeakerSink)
    - logging, asyncio, queue, threading
  - Who calls it:
    - pipeline.py (_tts_layer when TTS is SarvamTTS)
  - What it calls:
    - Sarvam API endpoint (/text-to-speech/stream)
    - httpx.AsyncClient for streaming HTTP POST
    - LocalSpeakerSink for audio playback (start, write_frame, flush, stop)
    - asyncio.sleep for brief pauses
  - Critical logic paths:
    - HTTP streaming with chunked transfer encoding
    - Language code mapping (en → en-IN, hi → hi-IN, gu → gu-IN)
    - Retry logic on stream failure (max_retries=2)
    - Chunked audio playback via sink.write_frame()
    - Barge-in handling: stop() interrupts playback and drains queues
    - Echo cooldown: vad_mute_until after natural completion
    - Connection management: single reused httpx client
  - Risks if modified:
    - Breaking Sarvam TTS integration causing no audio output
    - Incorrect HTTP streaming causing incomplete/chunked audio
    - Audio playback issues (buffer under/overflow, device errors)
    - Missing interruption handling causing inability to barge-in
    - HTTP connection leaks
    - API key handling issues

  tts_cartesia.py

  - Purpose: Cartesia streaming TTS via WebSocket
  - Responsibilities:
    - Stream audio using Cartesia WebSocket API
    - Maintain persistent WebSocket connection for low-latency
    - Stream audio chunks as they arrive from server
    - Achieve time-to-first-byte under 300ms
    - Handle reconnection and error recovery
  - Key Classes:
    - CartesiaTTS: Extends StreamingTTS
  - Key Functions:
    - speak(): Main streaming with WebSocket message handling
    - _stream_audio(): Generator yielding TTSChunks from WebSocket messages
    - stop(): Interrupt playback
    - close(): Close WebSocket connection
    - _ensure_connected(): Establish or reuse WebSocket connection
  - Inputs: Text to synthesize, configuration (API key, voice_id, model_id, sample_rate)
  - Outputs:
    - PCM16 audio chunks played via audio sink (inherited from StreamingTTS)
    - Playing state and interruption handling
  - Dependencies:
    - websockets (asyncio.client.ClientConnection)
    - os (environment variable for API key)
    - uuid (for context IDs)
    - base64, json (for WebSocket message handling)
    - tts_stream (StreamingTTS, TTSChunk)
    - logging, asyncio
  - Who calls it:
    - pipeline.py (_tts_layer when TTS is CartesiaTTS)
  - What it calls:
    - Cartesia WebSocket endpoint (wss://api.cartesia.ai/tts/websocket)
    - WebSocket connect/send/recv/close operations
    - base64.b64decode for audio chunk data
    - json.loads/json.dumps for message serialization
    - asyncio.wait_for for timeouts
    - asyncio.sleep for audio duration simulation (based on chunk length)
  - Critical logic paths:
    - WebSocket connection establishment with reconnection logic
    - Message handling: chunk (base64 audio) vs done vs error status
    - Audio playback simulation: sleep based on chunk duration and sample rate
    - Interruption handling: check _cancelled flag during streaming
    - Connection reuse: ping to check staleness, reconnect on failure
    - Context ID generation for each synthesis request
  - Risks if modified:
    - Breaking Cartesia TTS integration causing no audio output
    - WebSocket connection leaks or failure to reconnect
    - Incorrect audio playback timing (too fast/slow)
    - Missing interruption handling
    - JSON parsing errors causing stream failure
    - API key handling issues

  audio_input.py

  - Purpose: Audio input stream abstraction with noise suppression
  - Responsibilities:
    - Provide async iterator interface for audio sources
    - Implement MicrophoneStream via sounddevice
    - Include NoiseGate for adaptive spectral noise suppression
    - Support future sources (FreeSWITCH RTP, WebSocket, etc.)
  - Key Classes:
    - AudioInputStream: Base async audio source
    - MicrophoneStream: Real-time mic capture using sounddevice
    - NoiseGate: Adaptive spectral noise suppressor
  - Key Functions:
    - frames(): Async iterator yielding PCM16 frames
    - _read_frame(): Read next frame from source (implemented by subclasses)
    - NoiseGate.process(): Apply noise suppression to frame
    - NoiseGate._finish_calibration(): Compute noise floor from calibration frames
    - MicrophoneStream._audio_callback(): PortAudio callback for mic input
    - MicrophoneStream.start() / stop(): Manage sounddevice stream
  - Inputs: Audio from microphone or other sources
  - Outputs:
    - PCM16 frames (bytes) via async iterator
    - Noise-suppressed frames when NoiseGate enabled
  - Dependencies:
    - asyncio
    - logging
    - numpy (for audio processing in NoiseGate)
    - sounddevice (for MicrophoneStream)
    - typing (AsyncIterator)
  - Who calls it:
    - pipeline.py (audio_vad layer)
    - main_loop.py (VoiceLoop)
  - What it calls:
    - sounddevice.InputStream for microphone access
    - numpy.fft.rfft/irfft for frequency domain processing (NoiseGate)
    - asyncio.Queue for thread-safe frame queuing (MicrophoneStream)
    - asyncio.wait_for for frame reading timeout
  - Critical logic paths:
    - Noise gate: adaptive spectral subtraction with smoothing
    - Microphone callback: normalizing float32/int16 input to int16 PCM
    - Frame queuing: call_soon_threadsafe from audio thread to asyncio loop
    - Queue overflow handling: drop frames and log warnings
    - Stream start/stop: proper sounddevice resource management
    - Calibration: learn noise floor from first N frames
  - Risks if modified:
    - Breaking audio input causing no audio or distorted audio
    - Incorrect noise suppression leading to speech distortion or noise leakage
    - Microphone stream leaks (unclosed sounddevice streams)
    - Queue overflow causing audio dropouts
    - Calibration errors causing poor VAD performance
    - Thread-safety issues in audio callback

  vad.py

  - Purpose: Voice Activity Detection
  - Responsibilities:
    - Detect speech vs silence using energy-based threshold
    - Adaptive calibration of threshold from noise floor
    - Provide barge-in threshold for interruption detection
    - Track last energy and calibration state
  - Key Classes:
    - VoiceActivityDetector: Main VAD implementation
  - Key Functions:
    - __init__(): Configure thresholds and calibration parameters
    - is_speech(): Main detection method with calibration logic
    - _compute_energy(): Calculate mean absolute energy of frame
    - _finish_calibration(): Compute threshold from calibration energies
    - Properties: threshold, calibrated, last_energy, barge_in_threshold
  - Inputs: PCM16 audio frame (bytes)
  - Outputs: Boolean speech/silence decision
  - Dependencies:
    - logging
    - numpy (for energy calculation and calibration)
  - Who calls it:
    - pipeline.py (_process_vad_frame in audio_vad layer)
    - main_loop.py (_process_frame in VoiceLoop)
  - What it calls:
    - numpy.frombuffer and numpy.abs for energy calculation
    - numpy.mean, numpy.percentile, numpy.sort for calibration
  - Critical logic paths:
    - Calibration phase: collect energies, compute noise_p95 * multiplier
    - Threshold capping: respect minimum/maximum thresholds
    - Speech decision: energy > threshold AND energy > minimum_rms_floor
    - Barge-in threshold: threshold * barge_in_multiplier (with floor)
    - Calibration warnings: when raw threshold exceeds maximum
  - Risks if modified:
    - Breaking VAD causing missed speech detection or false triggers
    - Incorrect calibration leading to poor threshold adaptation
    - Barge-in threshold too low/high causing false positives/negatives
    - Minimum RMS floor misconfiguration causing silence misclassification
    - Energy calculation errors

  turn_manager.py

  - Purpose: Intelligent turn-taking manager
  - Responsibilities:
    - Decide when user has finished speaking using silence + semantic completeness
    - Avoid triggering on silence alone for short fragments
    - Accumulate STT partials and manage turn state
    - Provide grace window to absorb trailing speech fragments
  - Key Classes:
    - TurnManagerConfig: Configuration dataclass
    - TurnManager: Main turn-taking logic
  - Key Functions:
    - update_text(): Replace buffer with latest STT partial
    - mark_speech(): Update last speech time on VAD speech detection
    - silence_duration_ms(): Calculate silence since last speech
    - is_semantically_complete(): Check if text meets completion criteria
    - should_trigger(): Determine if turn is ready for processing
    - flush(): Return accumulated text and reset state
  - Inputs: STT partials (via update_text()), VAD speech events (via mark_speech())
  - Outputs:
    - Boolean from should_trigger() indicating turn completion
    - Accumulated text from flush()
    - Internal state (last speech time, buffer, etc.)
  - Dependencies:
    - time (monotonic for timing)
    - logging
    - dataclasses
  - Who calls it:
    - pipeline.py (audio_vad layer for turn detection)
    - main_loop.py (VoiceLoop for _check_turn_trigger)
  - What it calls:
    - time.monotonic() for timing calculations
    - logging for debug/info output
  - Critical logic paths:
    - Semantic completion:
        - Word count ≥ min_words_long (5) → complete
      - Word count ≥ min_words_short (3) + ends with punctuation → complete
      - Ends with punctuation + word count ≥ 2 → complete
    - Trigger conditions:
        - Silence duration ≥ silence_threshold_ms (600ms) AND
      - Semantically complete AND
      - Grace window elapsed after conditions first met
    - State management:
        - Update buffer on new partial
      - Reset on speech mark and flush
      - Track trigger candidate time for grace window
  - Risks if modified:
    - Breaking turn detection causing choppy or delayed responses
    - Over-sensitive triggering on noise or speech fragments
    - Under-sensitive triggering causing long silences before response
    - Incorrect semantic completion logic causing premature triggering
    - Grace window misconfiguration causing clipped or extended turns
    - State reset errors causing cross-turn contamination

  shortcuts.py

  - Purpose: Deterministic shortcut layer for instant responses
  - Responsibilities:
    - Provide zero-latency responses for common phrases
    - Check before every LLM call to avoid unnecessary latency
    - Return None if no match (allowing LLM fallback)
    - Support prefix matching for slight variations
  - Key Classes: None (functional module)
  - Key Functions:
    - check_shortcut(): Main shortcut lookup
  - Inputs: Transcript string from STT/post-processing
  - Outputs:
    - Response string if shortcut matched
    - None if no match (allowing LLM processing)
  - Dependencies: None (pure Python dict and string operations)
  - Who calls it:
    - pipeline.py (_conversation_layer, before LLM call)
    - main_loop.py (VoiceLoop._process_turn, before conversation.respond())
  - What it calls:
    - String operations (strip, lower, rstrip, startswith)
    - Dictionary lookup (SHORTCUTS)
  - Critical logic paths:
    - Exact match lookup in SHORTCUTS dict
    - Prefix match for variations (e.g., "hello there" matching "hello")
    - Length constraint on prefix match (<8 chars extra) to avoid over-matching
  - Risks if modified:
    - Breaking shortcut matching causing increased latency for common phrases
    - Over-aggressive matching causing incorrect shortcut responses
    - Under-matching causing unnecessary LLM calls for simple phrases
    - Incorrect normalization causing missed matches
    - Dict maintenance: keeping shortcuts relevant to use case

  Production Telephony Bridge Files (not in calling-agent/backend)

  ws_audio_receiver.py (project root)

  - Purpose: Phase 15 audio normalization receiver — receives audio from FreeSWITCH via WebSocket
  - Responsibilities:
    - Accept WebSocket connections from mod_audio_stream (port 9001)
    - Decode PCMU → PCM16 (audioop.ulaw2lin)
    - Resample 8k → 16k (audioop.ratecv)
    - Create/look up TelephonySession via conversation_call_bridge.call_bridge
    - Push normalized PCM frames to the AI pipeline
    - Debug audio capture to /tmp/phase15_audio_debug/
  - Trigger for session creation: WebSocket open from FreeSWITCH
  - Env vars: TELEPHONY_INPUT_CODEC (default pcmu), TELEPHONY_TARGET_SR (default 16000)

  conversation_call_bridge.py (project root)

  - Purpose: Bridge between ws_audio_receiver and VoicePipeline — one TelephonySession per call
  - Responsibilities:
    - SessionManager.get_or_create(): idempotent session creation per conn_id
    - TelephonySession.start(): starts pipeline.run_until_shutdown(), playback loop, event loop
    - Playback loop: reads WAV paths from TelephonySink.wav_queue and sends playback_request to socket_server
    - Timing observability: tracks first_audio_t, first_transcript_t, first_response_t, first_playback_t
  - Who calls it: ws_audio_receiver.py (global call_bridge import)
  - What it calls: VoicePipeline, TelephonySource, TelephonySink, EventHub, audit_manager
  - Playback injection: TCP 127.0.0.1:9004 (4-byte BE length + JSON playback_request)
  - API keys: both STT and TTS use SARVAMAI_API_KEY (with 'I')

  socket_server.py (project root, SEPARATE PROCESS)

  - Purpose: ESL (FreeSWITCH Event Socket Layer) controller — phases 1–9
  - Responsibilities: handshake with FreeSWITCH, record_session, uuid_audio_stream start/stop, playback
  - Listens on port 9004 for playback_request from conversation_call_bridge
  - Injects playback back to FreeSWITCH via ESL bgapi uuid_audio_stream
  - Does NOT contain STT, TTS, LLM, or VAD logic

  Dependency Map

  Core Execution Path

  AudioInputStream (MicrophoneStream)
          ↓
  NoiseGate (optional)
          ↓
  VoiceActivityDetector (VAD)
          ↓
  TurnManager (speech detection + buffering)
          ↓
  On silence+semantic completion → AudioSegment
          ↓
  STTRouter (Sarvam → OpenAI Whisper fallback)
          ↓
  post_process_transcript (domain corrections)
          ↓
  ConversationRouter.process_stream()
          ↓
    Intent detection → Shortcut check (bypass LLM)
          ↓
    Parallel LLM execution (background task)
          ↓
    LLMConversationEngine → OpenAI API
          ↓
    Streaming TTS (SarvamTTS/CartesiaTTS)
          ↓
  AudioSink (LocalSpeakerSink) → Speaker output
          ↓
  EventHub (events for monitoring)

  Supporting Services

  - Memory System: ConversationMemory injected into ConversationRouter and LLM engine
    - Provides conversation history for LLM prompts
    - Tracks booking state (location, property type, etc.)
    - Extracts context (user name, intent, topic, sentiment)
  - Event System: EventHub decouples pipeline from WebSocket clients
    - Emits user transcripts, agent responses, mic state, etc.
    - Non-blocking with drop-oldest overflow handling
  - Configuration:
    - Environment variables for API keys and feature flags
    - Constants in files (timeouts, thresholds, prompts, responses)
    - TurnManagerConfig for turn detection sensitivity
  - Audio Transport:
    - LocalSpeakerSink for speaker output via sounddevice
    - LocalMicSource in pipeline for audio input abstraction
    - TelephonySource: queue-based source for telephony; accepts push_frame() from WebSocket transport
      - Updated: push_frame() now pre-buffers frames even before start() is called, preventing audio
        loss during the race between call-connect and pipeline initialization (checked via _read_frames > 0)

  Shared State

  - ConversationState (in conversation.py):
    - Tracks current stage, language, turn count, confidence, etc.
    - New inbound fields: discovery_turns (int), purpose ("self_use"/"investment"/None), project_recommended (str|None)
    - Shared between conversation router and LLM engine via constructor
  - ConversationMemory:
    - Shared between conversation router, LLM engine, and pipeline
    - Holds short-term history, context, and booking data
  - Pipeline Layer States (in pipeline.py):
    - Audio buffer, speech duration, VAD state, TTS playing state
    - Queues between layers (AudioSegment, Transcript, Response)
    - Barge-in and echo cooldown state

  Global Objects

  - None defined as true globals; all state is encapsulated in objects passed via dependency injection or maintained in
  layer-specific instances.
  - Environment variables serve as de facto global configuration.
  - Logging configuration is global but not application state.

  Event Relationships

  - Audio Layer → VAD Layer: Raw audio frames → speech/silence decisions
  - VAD Layer → Turn Manager: Speech events → update last speech time
  - Turn Manager → STT Worker: Turn trigger → AudioSegment queued
  - STT Worker → Transcript Queue: Raw transcript → validated transcript
  - Transcript Queue → Conversation Layer: Transcript → LLM processing with streaming output
  - Conversation Layer → TTS Queue: LLM sentence chunks → TTS playback
  - TTS Layer → Audio Sink: Audio chunks → Speaker output
  - All Layers → Event Hub: Events for monitoring (user, agent, mic state, etc.)

  Runtime Flow

  Session Lifecycle

  1. Initialization:
    - VoiceLoop or VoicePipeline constructed with dependencies
    - OPENAI_API_KEY checked → LLMConversationEngine or MockConversationEngine selected
    - SARVAMAI_API_KEY and CARTESIA_API_KEY checked for STT/TTS availability
    - ENABLE_BARGE_IN and TELEPHONY_MODE environment variables read
  2. Start:
    - Pipeline or loop started via start() or run_until_shutdown()
    - Audio input stream started (microphone calibration begins)
    - STT provider connections warmed (background tasks)
    - Agent delivers inbound welcome via ConversationRouter.generate_opening()
  3. Running:
    - Audio capture loop processes frames (20ms PCM16 chunks)
    - VAD detects speech/silence with calibration
    - Speech frames buffered; MIN_SPEECH_MS=300ms gate before STT commit
    - On turn trigger:
        - AudioSegment queued for STT
        - Background task spawned for STT→LLM→TTS processing
    - STT layer transcribes with primary/fallback providers
    - Transcript validated and post-processed for domain corrections
    - Conversation layer:
        - Language detection/v3 (script → pattern → LLM)
        - Intent detection (deterministic)
        - Universal shortcuts (farewell/objection/gratitude/identity/language_switch)
        - _extract_data_from_input() side effects
        - _maybe_advance_stage() soft progression
        - LLM responds with full inbound script context
    - TTS layer streams audio to speaker sink with interruption support
    - Event hub broadcasts updates for monitoring
  4. Termination:
    - SIGINT/SIGTERM received or manual stop
    - Running flag set to false
    - Audio input stream stopped
    - All background tasks cancelled and awaited
    - STT/TTS clients closed
    - Resources released

  Conversation Lifecycle (inbound, 6 stages)

  1. GREETING: Agent answers call with bilingual inbound welcome
    - Caller's first response → language detected and locked for session
    - Stage automatically advances to DISCOVERY
  2. DISCOVERY: LLM collects caller needs through soft qualification
    - One question per turn; LLM chooses question from: purpose → timeline → config → location → family → budget
    - _extract_data_from_input() captures location/BHK/purpose as side effects on every turn
    - Advances to PROJECT_MATCH when location known OR after 3 discovery turns
  3. PROJECT_MATCH: LLM recommends 1-2 portfolio projects matching caller's stated needs
    - LLM guided by quick-match guide in system prompt (budget/location/config → project)
    - Advances to INVITE_VISIT after 6 total discovery turns
  4. INVITE_VISIT: LLM builds curiosity and invites caller to site visit
    - LLM gives exactly two time options; WhatsApp number requested
    - Advances to CONFIRMATION when appointment_time extracted
  5. CONFIRMATION: LLM confirms appointment details
    - Affirmation → booking.confirmed = True → CLOSING
    - Negation → LLM offers to reschedule (stays in INVITE_VISIT)
  6. CLOSING: LLM delivers warm close using caller's name
    - Farewell intent at any stage → immediate jump to CLOSING
    - Objection (2nd time) → graceful farewell → CLOSING
  Universal shortcuts (fire at any stage before LLM):
    - farewell, 2-strike objection, gratitude, identity question, language switch

  Language Handling

  1. Detection:
    - Script detection (Gujarati/Devanagari ranges) → high confidence
    - Pattern detection (Hindi/Gujarati word lists) → medium confidence
    - LLM classification → fallback for ambiguous cases
    - Special handling for Gujarati script markers in Devanagari text
  2. Stabilization:
    - Language history tracking (last 5 turns)
    - Language locking after first response to greeting (prevents STT script flipping)
    - Lock decays after 3 turns to allow natural switching
    - Explicit language switch intent overrides lock immediately
  3. Usage:
    - Detected language used for:
        - LLM system language instruction
      - TTS language setting
      - Localized responses (prompts, fillers, objections, etc.)
      - Post-processing language-specific corrections
    - User can switch language mid-call via explicit request
    - System matches user language exactly as per critical rules

  Intent Handling

  1. Detection Order (in detect_intent()):
    - Objection (highest priority)
    - Language switch request
    - Location selection
    - Property type (BHK)
    - Time preference
    - Affirmation
    - Identity question
    - Question
    - Negation
    - Farewell
    - Greeting
    - Gratitude
    - General (fallback)
  2. Intent-Specific Handling:
    - Objection: 2-strike graceful close (deterministic shortcut)
    - Language switch: immediate language change, lock reset, localized response (deterministic shortcut)
    - Identity question: deterministic response, then LLM continues
    - Farewell: immediate CLOSING transition (deterministic shortcut)
    - Gratitude: localized response (deterministic shortcut)
    - All other intents (location, BHK, time, affirmation, negation, question, greeting, general):
        → _extract_data_from_input() side effect → _maybe_advance_stage() → LLM responds
  3. Data Extraction (non-blocking side effects via _extract_data_from_input()):
    - Location: match_location() (exact/fuzzy) → Surat/Vapi/Silvassa → booking.location
    - BHK: match_bhk() (normalization + regex) → 1-5 BHK → booking.property_type
    - Time: _extract_time_preference() → match to available slots → booking.appointment_time
    - Purpose: keyword matching → "investment"/"self_use" → state.purpose
    - Note: property_status and transaction_type extraction removed (outbound-only concepts)

  Memory Handling

  1. Short-term History:
    - Sliding window of last MAX_SHORT_TERM_TURNS (5) user/assistant pairs
    - Maintained in ConversationMemory._turns
    - Trimmed when exceeding max_messages (max_turns * 2)
    - Provided to LLM via get_history_for_llm()
  2. Context Memory:
    - Extracted per user turn via _extract_context()
    - Fields: user_name (regex patterns), intent (keyword mapping), topic (keyword mapping), sentiment
  (positive/negative words)
    - Provided to LLM via memory.context.to_prompt_string()
  3. Booking Data:
    - Updated per turn via explicit setters in conversation router
    - Fields: location, property_type, appointment_date/time, user_name, project, property_status, transaction_type,
  confirmed
    - Provided to LLM via system prompt injection (_build_system_prompt())
    - Summary available via booking.summary()
    - Missing fields detectable via booking.next_missing_field()
  4. Reset:
    - Full reset on call end or new call via memory.reset()
    - Clears turns, context, and booking data

  Audio Buffering

  1. During Speech:
    - PCM16 frames appended to _audio_buffer in audio_vad layer
    - _speech_duration_ms incremented by frame duration (20ms)
    - Turn manager updated with proxy text for VAD debugging
    - Buffer size limited to MAX_BUFFER_BYTES (overflow: drop oldest frames)
  2. On Speech End:
    - VAD detects silence → turn manager checks should_trigger()
    - If triggered:
        - AudioSegment created from buffer and duration
      - Buffer cleared and duration reset
      - Turn manager flushed
      - AudioSegment queued for STT
    - If not triggered (too short, < MIN_SPEECH_MS=300ms): buffer cleared and duration reset
  3. Barge-in/Echo Handling:
    - During TTS: _audio_buffer cleared on barge-in detection
    - After TTS: _vad_mute_until set to ignore echo audio
    - Buffer cleared during mute period

  Interruptions

  1. Barge-in Detection:
    - During TTS playback: check if VAD energy > barge_in_threshold
    - Requires BARGE_IN_MIN_FRAMES (3) consecutive loud frames (lowered from 5 for telephony)
    - Requires TTS to have played >250ms (lowered from 600ms so callers can speak sooner)
    - On detection:
        - Stop TTS playback immediately
      - Cancel current LLM generation task
      - Flush pipeline queues (drop pending audio/transcripts/response)
      - Reset VAD state
      - Set _barge_in_occurred flag
  2. Echo Cooldown:
    - After TTS completes naturally: set _vad_mute_until = now + TTS_ECHO_COOLDOWN_MS/1000
    - During this period: treat all audio as echo, clear buffer, reset VAD state
    - Prevents TTS output from being re-recognized as user input
  3. Interruptible TTS:
    - All StreamingTTS implementations support stop() to interrupt playback
    - Pipeline layers check for cancellation during processing
    - Background tasks can be cancelled mid-execution

  End-call Flow

  1. Natural Completion:
    - User affirms in CONFIRMATION stage → booking.confirmed = True
    - _maybe_advance_stage() transitions to CLOSING → state.is_closing = True
    - LLM generates warm close using caller's name
  2. User-initiated End:
    - User expresses farewell intent at any stage (deterministic shortcut)
    - Immediate transition to CLOSING, localized farewell response
  3. Graceful Close (2-strike objection):
    - First objection: polite "would another time work better?" response
    - Second objection: localized farewell → CLOSING
  4. Timeout/Inactivity:
    - No explicit timeout; system continues responding in CLOSING stage
    - Questions still answered via LLM even in CLOSING

  Root Cause Analysis Per Issue

  Issue A: "kem cho" not detected. Mic often ignores speech unless user speaks loudly.

  - Probable root cause:
    - VAD calibration threshold too high for quiet speech
    - Post-processing not normalizing "kemcho" to "kem cho" before match_location
    - Microphone gain or noise suppression too aggressive
  - Exact files involved:
    - vad.py (VoiceActivityDetector.is_speech and calibration)
    - audio_input.py (MicrophoneStream and NoiseGate)
    - stt/post_processor.py (match_location and _GUJARATI_CORRECTIONS)
    - pipeline.py (VAD and audio processing layers)
  - Exact functions involved:
    - vad.py: _finish_calibration(), is_speech()
    - audio_input.py: NoiseGate.process(), MicrophoneStream._audio_callback
    - stt/post_processor.py: match_location()
  - Execution path:
    - Audio frame → NoiseGate (if enabled) → VAD energy calculation →
    - If energy < threshold → silence detected → speech not buffered →
    - No turn trigger → user speech ignored
    - OR: speech buffered but "kem cho" not normalized → match_location returns None
  - Confidence level: 80% (multiple contributing factors)
  - Investigation steps before implementing fixes:
    a. Check VAD calibration logs to see threshold values
    b. Verify microphone gain and NoiseGate settings
    c. Test "kem cho" utterance through post_processor to see if normalized
    d. Check audio buffer levels during quiet speech
    e. Verify ENABLE_BARGE_IN and TELEPHONY_MODE settings affecting VAD

  Issue B: Agent repeatedly ends conversation. Repeated fallback: "mai apko details bhej dunga, dhanyawad"

  - Probable root cause:
    - Objection handling logic incorrectly classifying user responses as objections
    - Decline count not being reset properly on re-engagement
    - Language detection issues causing misinterpretation of user intent
  - Exact files involved:
    - conversation.py (objection handling and decline count logic)
    - stt/post_processor.py (language-specific corrections)
    - pipeline.py (audio processing affecting transcription quality)
  - Exact functions involved:
    - conversation.py: process_stream() objection handling, decline_count reset
    - conversation.py: _extract_requested_language() for language switches
    - stt/post_processor.py: _GUJARATI_CORRECTIONS and _HINDI_CORRECTIONS
  - Execution path:
    - User response → STT → post-processing →
    - detect_intent() classifies as objection (incorrectly) →
    - objection response played → decline_count incremented →
    - If decline_count ≥ 2 → graceful close triggered →
    - Conversation ends prematurely
  - Confidence level: 85% (clear logic in conversation.py)
  - Investigation steps before implementing fixes:
    a. Log detect_intent() results for user responses
    b. Check decline_count reset logic in non-objection/intention paths
    c. Verify language detection accuracy for user responses
    d. Examine objection patterns list for over-matching
    e. Check if user is actually expressing objection or just answering questions

  Issue C: User asks: "आपको मेरी आवाज आ रही है?" Agent ignores question and continues script.

  - Probable root cause:
    - Question intent not being detected due to language/script issues
    - Post-processing not handling Devanagari question properly
    - Conversation router not advancing to appropriate handling for audio quality questions
  - Exact files involved:
    - conversation.py (detect_intent and language detection)
    - stt/post_processor.py (Devanagari handling and corrections)
    - pipeline.py (STT transcription quality)
  - Exact functions involved:
    - conversation.py: detect_intent() (question detection logic)
    - conversation.py: detect_language_v3() (Devanagari/Gujarati handling)
    - stt/post_processor.py: _HINDI_CORRECTIONS and Devanagari processing
  - Execution path:
    - Devanagari audio → STT transcription →
    - Post-processing (may not correct properly) →
    - detect_intent() fails to classify as "question" due to script/language issues →
    - Falls back to "general" →
    - LLM response may not address the audio quality concern →
    - Conversation continues with scripted flow
  - Confidence level: 75% (depends on transcription and intent detection)
  - Investigation steps before implementing fixes:
    a. Check STT output for the exact phrase in Devanagari
    b. Verify detect_intent() logic for question detection in Devanagari
    c. Check language detection accuracy for Devanagari input
    d. Examine if post-processing preserves question words/meaning
    e. Test if LLM would respond appropriately if intent were correctly detected

  Issue D: Unexpected Malayalam transcript generation and unsupported script handling.

  - Probable root cause:
    - STT validation not catching Malayalam script in unsupported script filter
    - Sarvam auto-detect misfiring on noisy audio to Malayalam
    - Unicode range for Malayalam not included in _UNSUPPORTED_SCRIPT_RE
  - Exact files involved:
    - stt/router.py (validate_transcript function)
    - stt/base.py (STTResult definition)
    - pipeline.py (STT worker layer)
  - Exact functions involved:
    - stt/router.py: validate_transcript() (specifically _UNSUPPORTED_SCRIPT_RE check)
  - Execution path:
    - Noisy audio → Sarvam STT →
    - Returns Malayalam transcript (auto-detect error) →
    - validate_transcript() fails to reject because Malayalam range missing →
    - Transcript passes through to post-processing and intent detection →
    - Causes language detection issues and incorrect responses
  - Confidence level: 90% (clear missing Unicode range)
  - Investigation steps before implementing fixes:
    a. Check _UNSUPPORTED_SCRIPT_RE in router.py for missing Malayalam range (ഀ-ൿ)
    b. Verify other unsupported scripts are covered
    c. Test with known Malayalam audio to see if filtered
    d. Check if Tamil/Kannada/etc. ranges are present
    e. Verify the regex covers all Indic scripts we don't want

  Issue E: User says: "just talk like a normal person" Agent ignores instruction and continues workflow.

  - Probable root cause:
    - Instruction not matching any intent detection patterns
    - Treated as "general" → LLM response may not acknowledge request
    - No specific handling for conversational style requests in conversation router
  - Exact files involved:
    - conversation.py (detect_intent and general intent handling)
    - conversation_engine_llm.py (system prompt and LLM response)
  - Exact functions involved:
    - conversation.py: detect_intent() (general intent fallback)
    - conversation.py: process_stream() (general intent handling)
    - conversation_engine_llm.py: _build_system_prompt() (critical rules)
  - Execution path:
    - User statement → STT → post-processing →
    - detect_intent() returns "general" (no match) →
    - General intent path: LLM consumption with nudge back to flow →
    - LLM may not explicitly acknowledge the "normal person" request →
    - Continues with scripted flow without addressing user preference
  - Confidence level: 70% (depends on LLM response quality)
  - Investigation steps before implementing fixes:
    a. Check what the LLM actually responds with for this input
    b. Verify if system prompt contains guidance on conversational style
    c. Check if general intent path includes any acknowledgment logic
    d. Examine if there's a specific intent for conversational feedback
    e. Test if adding explicit handling would improve user experience

  Issue F: Long or complex user questions are not properly addressed before returning to sales flow.

  - Probable root cause:
    - Question intent handling immediately nudges back to flow after LLM response
    - No mechanism to hold conversation for multi-turn question answering
    - LLM response may be cut off or not fully address complex questions
  - Exact files involved:
    - conversation.py (question intent handling in various stages)
    - conversation_engine_llm.py (LLM response length and token limits)
    - pipeline.py (TTs layer affecting audio output length)
  - Exact functions involved:
    - conversation.py: process_stream() (question handling with nudge_back=True/False)
    - conversation_engine_llm.py: respond() (max_tokens=150 limit)
    - tts_stream.py: StreamingTTS.speak() (chunking and playback)
  - Execution path:
    - Complex question → STT → post-processing →
    - detect_intent() returns "question" →
    - Question handling: LLM response generated →
    - Immediately nudge back to flow (in most stages) →
    - User doesn't get chance to ask follow-up or hear full response →
    - Conversation returns to sales script prematurely
  - Confidence level: 80% (clear nudge back logic)
  - Investigation steps before implementing fixes:
    a. Check which stages use nudge_back=True vs False for questions
    b. Verify LLM response length and completeness for complex questions
    c. Check if TTS playback completes before nudging back
    d. Examine if there's a mechanism to hold state for question answering
    e. Test if removing nudge back improves question handling

  Issue G: Agent should answer the user's question first, then continue business objectives.

  - Probable root cause:
    - Same as Issue F: question handling prioritizes flow return over thorough answering
    - No distinction between simple clarifying questions and complex informational needs
    - Sales flow advancement happens immediately after LLM response in most cases
  - Exact files involved:
    - Same as Issue F: conversation.py, conversation_engine_llm.py, pipeline.py
  - Exact functions involved:
    - Same as Issue F
  - Execution path:
    - Identical to Issue F analysis
  - Confidence level: 80%
  - Investigation steps before implementing fixes:
    a. Same as Issue F
    b. Check if any stages delay flow advancement for questions
    c. Verify if LLM is prompted to be thorough in answers
    d. Check system prompt for guidance on question handling

  Issue H: Latency is high and first response chunk takes too long.

  - Probable root cause:
    - STT provider warm-up not happening early enough
    - LLM processing waits for full response before starting TTS (sentence-level chunking helps but still has LLM
  latency)
    - Pipeline queue depths causing buffering delays
    - First chunk delay includes STT + LLM processing time before TTS can start
  - Exact files involved:
    - pipeline.py (connection warm-up and queue depths)
    - conversation_engine_llm.py (LLM streaming and token limits)
    - tts_stream.py and implementations (chunk size and delay)
    - stt/router.py (STT retries and validation)
  - Exact functions involved:
    - pipeline.py: start() warm-up targets, queue sizes
    - pipeline.py: _stt_worker, _conversation_layer, _tts_layer latencies
    - conversation_engine_llm.py: _call_llm_stream (sentence-level chunking)
    - tts_stream.py: MockStreamingTTS chunk parameters
    - tts_sarvam.py: _stream_audio chunk reading
  - Execution path:
    - Turn trigger →
    - AudioSegment queued for STT →
    - STT processing (including retries) →
    - Transcript queued for conversation →
    - LLM processing (waiting for first sentence) →
    - First chunk queued for TTS →
    - TTS playback begins →
    - User hears first audio chunk
  - Confidence level: 75% (multiple latency contributors)
  - Investigation steps before implementing fixes:
    a. Measure STT latency with and without warm-up
    b. Check LLM time to first token vs full response
    c. Verify queue depths aren't causing unnecessary buffering
    d. Check TTS chunk size and delay parameters
    e. Measure end-to-end latency with instrumentation
    f. Test if increasing concurrency reduces latency

  Implementation Plans

  Issue A: "kem cho" not detected. Mic often ignores speech unless user speaks loudly.

  1. Problem summary: Quiet speech not detected, and "kem cho" variant not normalized for location matching.
  2. Systems involved: VAD, audio input, STT post-processing, pipeline audio processing.
  3. Files involved:
    - vad.py
    - audio_input.py
    - stt/post_processor.py
    - pipeline.py
  4. Proposed architectural solution:
    - Lower VAD minimum_threshold for better quiet speech detection
    - Improve NoiseGate to preserve quiet speech while suppressing noise
    - Add "kemcho" → "kem cho" normalization in Gujarati corrections
    - Verify microphone gain settings are appropriate
  5. Risks:
    - Increased false positives from noise if VAD threshold too low
    - NoiseGate over-processing causing speech distortion
    - Post-processing over-correction affecting other inputs
  6. Dependencies:
    - None (parameter and logic changes only)
  7. Validation strategy:
    - Test with quiet "kem cho" utterance at various volumes
    - Check VAD logs for appropriate triggering
    - Verify location detection works for quiet speech
    - Test noise rejection to ensure no false triggers
  8. Estimated implementation complexity: Low (parameter tuning and small logic addition)

  Issue B: Agent repeatedly ends conversation. Repeated fallback: "mai apko details bhej dunga, dhanyawad"

  1. Problem summary: Objection handling too aggressive, decline count not reset on re-engagement.
  2. Systems involved: Conversation routing, objection handling, state management.
  3. Files involved:
    - conversation.py
  4. Proposed architectural solution:
    - Refine objection patterns to reduce false positives
    - Ensure decline_count reset in all non-objection re-engagement paths
    - Add logging to track objection classification decisions
    - Consider contextual objection handling (e.g., only in certain stages)
  5. Risks:
    - Missing genuine objections if patterns too restrictive
    - Incorrect reset causing objection counting errors
    - Over-engineering making flow harder to follow
  6. Dependencies:
    - None (logic changes only)
  7. Validation strategy:
    - Test various user inputs to verify objection classification
    - Check decline_count behavior in different scenarios
    - Verify re-engagement resets count appropriately
    - Test with actual objection expressions to ensure they're still caught
  8. Estimated implementation complexity: Low to medium (logic refinement)

  Issue C: User asks: "आपको मेरी आवाज आ रही है?" Agent ignores question and continues script.

  1. Problem summary: Question intent not detected for Devanagari audio, causing missed audio quality concerns.
  2. Systems involved: STT transcription, language detection, intent detection, conversation handling.
  3. Files involved:
    - stt/post_processor.py (Devanagari handling)
    - conversation.py (detect_intent, detect_language_v3)
    - pipeline.py (STT quality)
  4. Proposed architectural solution:
    - Improve Devanagari handling in post-processing to preserve question words
    - Enhance detect_intent() to better catch Devanagari question patterns
    - Verify language detection accuracy for Devanagari input
    - Consider adding explicit audio quality intents if common
  5. Risks:
    - Over-correction in post-processing changing valid meaning
    - Intent detection becoming too complex or slow
    - Language detection false positives
  6. Dependencies:
    - None (logic changes only)
  7. Validation strategy:
    - Test the exact phrase through full pipeline
    - Check STT output and post-processing result
    - Verify detect_intent() returns "question"
    - Check language detection returns correct script
    - Verify conversation handles audio quality question appropriately
  8. Estimated implementation complexity: Low (logic improvements)

  Issue D: Unexpected Malayalam transcript generation and unsupported script handling.

  1. Problem summary: STT validation missing Malayalam script in unsupported script filter.
  2. Systems involved: STT validation and filtering.
  3. Files involved:
    - stt/router.py
  4. Proposed architectural solution:
    - Add Malayalam script range (ഀ-ൿ) to _UNSUPPORTED_SCRIPT_RE
    - Verify all unwanted scripts are covered
    - Consider adding a test for script rejection
  5. Risks:
    - None (adding protection, not removing)
    - Potential performance impact negligible (single regex check)
  6. Dependencies:
    - None
  7. Validation strategy:
    - Test with Malayalam audio to verify rejection
    - Check that other unsupported scripts still rejected
    - Verify wanted scripts (Latin, Devanagari, Gujarati) still pass
    - Ensure no performance regression in STT pipeline
  8. Estimated implementation complexity: Very small (regex update)

  Issue E: User says: "just talk like a normal person" Agent ignores instruction and continues workflow.

  1. Problem summary: Conversational style request not acknowledged, treated as general inquiry.
  2. Systems involved: Intent detection, LLM response generation, conversation flow.
  3. Files involved:
    - conversation.py (detect_intent, general handling)
    - conversation_engine_llm.py (system prompt)
  4. Proposed architectural solution:
    - Add specific intent for conversational feedback/requests
    - Update detect_intent() to catch phrases like "talk like a normal person"
    - Modify general intent handling to acknowledge and adapt to feedback
    - Consider updating system prompt to be more conversational by default
  5. Risks:
    - Over-matching causing unintended intent classification
    - Changing system prompt affecting overall behavior
    - Adding complexity to intent detection
  6. Dependencies:
    - None
  7. Validation strategy:
    - Test the specific phrase through pipeline
    - Verify intent detection catches it as new intent
    - Check that response acknowledges request appropriately
    - Verify general handling still works for other inputs
    - Test with various conversational feedback phrases
  8. Estimated implementation complexity: Low (adding intent and handling)

  Issue F: Long or complex user questions are not properly addressed before returning to sales flow.

  1. Problem summary: Question handling immediately returns to sales flow, not allowing proper Q&A.
  2. Systems involved: Question intent handling, conversation flow control, LLM response completeness.
  3. Files involved:
    - conversation.py (question handling in stages)
    - conversation_engine_llm.py (LLM response limits)
    - pipeline.py (TTS layer affecting perceived responsiveness)
  4. Proposed architectural solution:
    - Modify question handling to hold state for follow-up in appropriate stages
    - Only nudge back to flow after user indicates completion (e.g., affirmation or timeout)
    - Consider increasing LLM max_tokens for more thorough answers
    - Add explicit "question answering" stage or mode
  5. Risks:
    - Getting stuck in question mode if user doesn't signal completion
    - Increased LLM latency and cost
    - Complex flow logic making maintenance harder
    - Potential for user to feel ignored if not nudged back appropriately
  6. Dependencies:
    - None (logic changes, potential LLM parameter change)
  7. Validation strategy:
    - Test with complex multi-part questions
    - Verify LLM provides complete answer before flow return
    - Check that user can ask follow-up questions
    - Ensure sales flow eventually progresses after question resolution
    - Test timeout mechanisms to prevent infinite question loops
  8. Estimated implementation complexity: Medium (flow logic changes)

  Issue G: Agent should answer the user's question first, then continue business objectives.

  1. Problem summary: Same as Issue F - priority given to sales flow over thorough question answering.
  2. Systems involved: Same as Issue F
  3. Files involved:
    - Same as Issue F
  4. Proposed architectural solution:
    - Same as Issue F: modify question handling to prioritize complete answering
    - Ensure LLM is prompted to be thorough in responses
    - Consider adding verification that user understood before returning to flow
    - Update system prompt guidelines for question handling
  5. Risks:
    - Same as Issue F
  6. Dependencies:
    - Same as Issue F
  7. Validation strategy:
    - Same as Issue F
  8. Estimated implementation complexity: Medium (same as Issue F)

  Issue H: Latency is high and first response chunk takes too long.

  1. Problem summary: High latency in first audio chunk response due to STT/LLM processing delays.
  2. Systems involved: STT processing, LLM generation, TTS streaming, pipeline queuing.
  3. Files involved:
    - pipeline.py (connection warm-up, queue depths, layer timing)
    - conversation_engine_llm.py (LLM streaming and token limits)
    - tts_stream.py and implementations (chunking and playback)
    - stt/router.py (STT retries and validation)
  4. Proposed architectural solution:
    - Improve connection warm-up to happen earlier in pipeline startup
    - Optimize queue depths to balance latency and burst handling
    - Enhance LLM streaming to deliver first chunks faster (smaller sentence boundaries)
    - Consider reducing LLM max_tokens for faster first response (trade-off with completeness)
    - Improve TTS chunk size and delay for faster start
    - Add instrumentation to measure latency breakdown
  5. Risks:
    - Queue depths too small causing dropped segments under load
    - LLM streaming optimization affecting response quality
    - First chunk optimization reducing overall response completeness
    - TTS chunk size too small causing audio artifacts
    - Connection warm-up complexity increasing startup time
  6. Dependencies:
    - None (parameter and logic changes, potential LLM parameter change)
  7. Validation strategy:
    - Measure end-to-end latency with detailed breakdown (STT, LLM, TTS, queuing)
    - Test first chunk time vs complete response time
    - Verify audio quality not degraded by changes
    - Test under various load conditions (simulated)
    - Check that connection warm-up actually reduces first STT latency
  8. Estimated implementation complexity: Medium (multiple parameter optimizations)

  Latency & UX Enhancement Review

  "Play keyboard typing / thinking audio while waiting for LLM response."

  Best insertion point in architecture

  The optimal point to insert thinking audio is in the conversation layer, specifically:
  - After the LLM request is sent but before the first response chunk is received
  - During the time when the background LLM task is processing but no audio chunks have been queued for TTS
  - This avoids interfering with user speech detection and occurs during the natural "thinking" pause

  Files likely involved

  1. pipeline.py:
    - _conversation_layer(): Modify to play thinking audio when waiting for first LLM chunk
    - May need to add thinking audio player as a dependency
  2. conversation.py:
    - process_stream(): Could signal when thinking should start/end
    - Would need to expose thinking state to pipeline
  3. New audio component:
    - Thinking audio player (could reuse StreamingTTS abstraction with local audio file)
    - Or system audio playback via audio_transport

  Event flow changes required

  1. Current flow:
    - Transcript queued → Conversation layer starts LLM task →
    - First LLM chunk queued → TTS layer plays → User hears response
  2. Proposed flow:
    - Transcript queued → Conversation layer starts LLM task →
    - Play thinking audio (looped) →
    - First LLM chunk queued → Stop thinking audio →
    - TTS layer plays first chunk → User hears response after thinking audio

  Potential race conditions

  1. Audio mixing: Thinking audio and TTS output must not overlap
    - Must ensure thinking audio stops before TTS starts
    - Risk: If timing is off, user hears both simultaneously
  2. Barge-in during thinking:
    - User speaking during thinking audio should still trigger barge-in
    - Thinking audio player must be interruptible like TTS
    - Risk: Thinking audio blocks barge-in detection if not properly implemented
  3. State consistency:
    - Thinking audio state must be reset properly on interruption/cancellation
    - Risk: Audio resources not released or left in inconsistent state

  Barge-in implications

  - Thinking audio must be treated like TTS for barge-in purposes
  - VAD's barge-in detection should trigger during thinking audio playback
  - Pipeline queues should be flushed when barge-in detected during thinking
  - Thinking audio player must support stop() method for interruption
  - _vad_mute_until logic should apply to thinking audio as well to prevent echo

  Recommended design

  1. Create ThinkingAudioPlayer class:
    - Implement StreamingTTS interface (or reuse MockStreamingTTS with local file)
    - Load short typing/thinking audio loop (e.g., 2-3 seconds of keyboard sounds)
    - Support start/stop/looping with interruption handling
    - Use same audio sink as TTS for consistent output
  2. Modify pipeline.py:
    - Add thinking_audio_player dependency to VoicePipeline constructor
    - In _conversation_layer():
        - Before starting LLM task: start thinking audio player
      - When first LLM chunk received: stop thinking audio player
      - Ensure proper cleanup on cancellation/error
    - Handle thinking audio in barge-in detection logic (treat like TTS)
  3. Configuration:
    - Make thinking audio optional via environment variable
    - Allow configuration of audio file and volume
    - Default to disabled to maintain current behavior

  Alternative lower-latency approaches

  1. Increase LLM concurrency:
    - Use faster LLM model or optimize prompts for quicker first token
    - Implement speculative execution (generate multiple likely responses)
  2. Reduce LLM response length:
    - Decrease max_tokens for faster first response (trade-off with completeness)
    - Use truncation strategies to get to the point quicker
  3. Improve STT speed:
    - Use faster STT provider or optimize audio preprocessing
    - Increase connection warm-up aggressiveness
  4. Pipeline parallelism:
    - Overlap STT of next turn with LLM of current turn (requires careful state management)
    - Speculative STT processing during silence periods
  5. Client-side perception:
    - Progressive audio delivery (already implemented via sentence chunking)
    - Optimize TTS time-to-first-byte (already using streaming endpoints)
    - Cognitive techniques: fill silence with ambient sound rather than distinct typing

  Safety & Risk Review

  Fragile logic

  1. Language detection and locking (conversation.py):
    - Complex nested conditions for script/pattern/LLM detection
    - Risk of incorrect language locking causing STT script flipping
    - Mitigation: Add comprehensive test matrix for language inputs
  2. Turn manager semantic completion (turn_manager.py):
    - Heuristic-based completion logic may not match user expectations
    - Risk of premature or delayed turn triggering
    - Mitigation: Log decision factors and tune parameters based on real data
  3. Objection handling (conversation.py):
    - Broad pattern matching may classify legitimate responses as objections
    - Risk of prematurely ending conversations
    - Mitigation: Review objection patterns with domain experts, add contextual awareness

  Hidden coupling

  1. ConversationRouter → ConversationMemory → LLM system prompt:
    - Deep coupling where memory state directly affects LLM behavior
    - Risk: Memory corruption leads to nonsensical LLM responses
    - Mitigation: Validate memory state before injection, add defensive copies
  2. Pipeline layer queues → Barge-in handling:
    - Barge-in relies on flushing multiple queue types consistently
    - Risk: Incomplete queue flush leaves stale data causing audio artifacts
    - Mitigation: Centralize queue flushing, add validation that queues are empty after flush
  3. STT post-processing → Intent detection:
    - Post-processing changes affect what intents are detected
    - Risk: Over-correction leads to missed intents, under-correction causes false positives
    - Mitigation: Log original vs corrected transcript, validate with domain phrases

  State machine risks

  1. Conversation stage transitions (conversation.py):
    - Complex stage transition logic with many paths
    - Risk: Getting stuck in wrong stage or skipping critical stages
    - Mitigation: Add stage transition logging and validation, implement stage diagram tests
  2. Turn manager state (turn_manager.py):
    - Multiple time-based states (last speech, last text update, trigger candidate)
    - Risk: State desynchronization under high load or timing anomalies
    - Mitigation: Use single source of truth for timing, add state consistency checks
  3. TTS playing state (pipeline.py and tts_* implementations):
    - Distributed state between pipeline and TTS implementations
    - Risk: State mismatch causing audio to play when it shouldn't or vice versa
    - Mitigation: Centralize playing state in pipeline, use TTS only for output

  Repetition loops

  1. Objection handling decline count:
    - Two objections in a row trigger graceful close
    - Risk: Legitimate back-and-forth mistaken for objections
    - Mitigation: Make objection detection more contextual, require specific objection phrases
  2. Question handling nudge back:
    - In some states, questions trigger immediate nudge back to flow
    - Risk: User asking follow-up questions gets cut off repeatedly
    - Mitigation: Implement question state holding as proposed in Issue F/G solutions
  3. Language switching:
    - Rapid language switching could cause thrashing
    - Risk: System constantly switching languages without settling
    - Mitigation: Language lock decay already helps, consider minimum time between switches

  Language locking risks

  1. Overly aggressive locking (conversation.py):
    - Lock set after first response to greeting, decays after 3 turns
    - Risk: User wanting to switch language early is ignored
    - Mitigation: Consider shorter lock duration or earlier unlock for explicit requests
  2. Script detection false positives (conversation.py detect_language_v3):
    - Devanagari text with Gujarati markers classified as Gujarati
    - Risk: STT outputting Devanagari for Gujarati intent causing confusion
    - Mitigation: The current marker-based approach is reasonable, consider improving

  Intent classification weaknesses

  1. Over-reliance on keyword matching (conversation.py detect_intent):
    - May miss nuanced intents or misclassify similar phrases
    - Risk: Important user intents falling into general category
    - Mitigation: Regularly review intent logs, add missing patterns, consider lightweight ML for difficult cases
  2. Question detection limitations:
    - Relies on question words or ending punctuation
    - Risk: Indirect questions or statements with questioning intents missed
    - Mitigation: Consider adding question-like patterns (e.g., "I wonder if", "would it be possible")

  STT/TTS synchronization risks

  1. Barge-in timing (pipeline.py):
    - Barge-in detection during TTS relies on energy thresholds
    - Risk: Late detection causing clipped audio, early detection causing false positives
    - Mitigation: Tune barge-in_multiplier and min_frames based on audio testing
  2. Echo cooldown (pipeline.py):
    - _vad_mute_until prevents TTS echo from being recognized as speech
    - Risk: Too short → echo causes false triggers, too long → misses real speech after TTS
    - Mitigation: Tune TTS_ECHO_COOLDOWN_MS based on audio testing
  3. Audio sink sharing (pipeline.py):
    - TTS and thinking audio (if implemented) share same sink
    - Risk: Audio underruns or mixing issues if not properly coordinated
    - Mitigation: Ensure exclusive access to sink during playback, proper sequencing

  Conversation dead-end scenarios

  1. Perpetual objection handling:
    - User keeps objecting, decline count increments, but never reaches 2
    - Risk: Infinite objection loop without progression
    - Mitigation: Add maximum objection count or time-based reset
  2. Question loop without resolution:
    - User keeps asking questions, system keeps answering but never advances
    - Risk: Never reaching booking completion
    - Mitigation: Implement question state holding with timeout or completion detection
  3. Language lock preventing progression:
    - User language not detected correctly, locking to wrong language
    - Risk: Unable to understand user or progress through stages
    - Mitigation: Improve language detection, add explicit language reset mechanism
  4. Booking data blocking progression:
    - Required field never extracted, stuck in early stage
    - Risk: Never advancing past location or property type
    - Mitigation: Improve extraction algorithms, add LLM-assisted extraction for difficult cases

  Future Developer Reference Guide

  STT Changes

  - Files to modify:
    - stt/sarvam.py (provider-specific changes)
    - stt/openai_whisper.py (provider-specific changes)
    - stt/router.py (routing logic, validation, post-processing hook)
    - stt/post_processor.py (domain-specific corrections)
    - stt/base.py (interface changes - rare)
  - Considerations:
    - Maintain backward compatibility with STTProvider interface
    - Ensure validation doesn't break existing transcripts
    - Test with both providers and fallback scenarios
    - Monitor latency and accuracy trade-offs

  TTS Changes

  - Files to modify:
    - tts_sarvam.py (Sarvam-specific)
    - tts_cartesia.py (Cartesia-specific)
    - tts_stream.py (interface changes - rare)
    - tts_*.py implementations
  - Considerations:
    - Maintain StreamingTTS interface contract
    - Ensure interruption handling works correctly
    - Test with both providers
    - Consider audio quality and latency trade-offs
    - Handle resource cleanup properly (clients, connections)

  Language detection changes

  - Files to modify:
    - conversation.py (detect_language_v3, _extract_requested_language)
    - stt/post_processor.py (language-specific corrections in _apply_corrections)
    - conversation_engine_llm.py (_build_system_prompt language logic)
  - Considerations:
    - Maintain support for English, Hindi, Gujarati (script and transliterated)
    - Ensure language switching works mid-call
    - Test with mixed-language inputs
    - Verify system prompt enforces correct language output

  Prompt changes

  - Files to modify:
    - conversation.py (_build_system_prompt, all prompt dictionaries)
    - conversation_engine_llm.py (build_system_prompt function)
  - Considerations:
    - Keep prompts concise but comprehensive for LLM
    - Maintain critical rules (never refuse language, be proactive, etc.)
    - Update location/property information as business changes
    - Test LLM responses for adherence to prompt guidelines

  Conversation flow changes

  - Files to modify:
    - conversation.py (stage handling in process_stream)
    - conversation_engine_llm.py (system prompt with stage context)
    - memory.py (BookingData fields if adding new data points)
  - Considerations:
    - Maintain stage progression logic (GREETING → ... → CLOSING)
    - Ensure data extraction works for new fields
    - Test edge cases and error conditions
    - Verify booking completion logic works correctly

  Call ending logic

  - Files to modify:
    - conversation.py (closing stage handling, reengagement detection)
    - conversation_engine_llm.py (system prompt closing logic)
    - memory.py (BookingData.confirmed field usage)
  - Considerations:
    - Maintain graceful close for objections
    - Ensure reengagement detection works for all supported languages
    - Verify booking confirmation message includes all relevant details
    - Test edge cases like mid-call hangups

  Memory logic

  - Files to modify:
    - memory.py (all classes and extraction logic)
    - conversation.py (memory usage in _extract_context and system prompt)
    - conversation_engine_llm.py (history for LLM prompts)
  - Considerations:
    - Maintain short-term history sliding window
    - Ensure context extraction is robust and accurate
    - Validate booking data summary and missing field detection
    - Test memory reset and initialization

  Latency optimization

  - Files to monitor/change:
    - pipeline.py (all layer latencies, queue depths, warm-up)
    - conversation_engine_llm.py (LLM streaming, token limits, prompt size)
    - tts_* implementations (chunk size, delay, connection parameters)
    - stt/router.py (STT retries, validation complexity)
    - audio_input.py (NoiseGate parameters, frame processing)
  - Considerations:
    - Measure latency breakdown regularly (STT, LLM, TTS, queuing)
    - Balance latency with accuracy and quality trade-offs
    - Test under realistic load and network conditions
    - Consider caching and connection reuse strategies
    - Monitor resource usage (memory, CPU, file descriptors)

  Audio processing

  - Files to modify:
    - audio_input.py (MicrophoneStream, NoiseGate)
    - vad.py (VoiceActivityDetector parameters and logic)
    - pipeline.py (audio processing layers, barge-in, echo cooldown)
  - Considerations:
    - Maintain VAD calibration accuracy
    - Ensure noise suppression doesn't distort speech
    - Test with various audio environments (noisy, quiet, echoey)
    - Verify barge-in and echo handling work correctly
    - Monitor audio buffer levels and overflow handling

  Multi-language support

  - Files to modify:
    - conversation.py (all language-specific logic and responses)
    - stt/post_processor.py (language-specific corrections)
    - tts_* implementations (language setting and audio synthesis)
    - conversation_engine_llm.py (system prompt language injection)
  - Considerations:
    - Maintain parity between English, Hindi, Gujarati support
    - Ensure cultural appropriateness of prompts and responses
    - Test with native speakers for each language
    - Verify script handling (Devanagari vs Gujarati) works correctly
    - Consider adding other languages if business requires

  Summary

  This document provides a comprehensive analysis of the voice calling agent architecture, covering all aspects from
  high-level flow to detailed file-by-file examination. The system is well-designed with clear separation of concerns,
  asynchronous processing for real-time performance, and robust handling of the multi-lingual, domain-specific use case.


  The identified issues range from simple parameter tuning (VAD sensitivity, post-processing corrections) to more
  complex flow modifications (question handling language, objection logic). Most issues can be addressed with targeted
  changes to specific files without requiring architectural overhauls.

  Future development should focus on maintaining the delicate balance between latency and quality, improving the
  robustness of language handling and intent detection, and enhancing the user experience through better question
  answering and more natural conversational flows. The modular architecture makes it feasible to evolve the system while
  preserving its core strengths.
