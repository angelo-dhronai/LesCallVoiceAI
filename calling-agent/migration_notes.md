# Migration Notes

## Entry Points
- **Primary Launcher**: `backend/run.py`
  - CLI Mode: `python backend/run.py`
  - Web Mode: `python backend/run.py --web`
- **Web UI**: `http://127.0.0.1:8000` (when running with `--web`)

## Required Environment Variables
The following keys should be defined in a `.env` file in the root or `backend/` directory:
- `SARVAM_API_KEY`: Required for Sarvam STT/TTS.
- `OPENAI_API_KEY`: Required for OpenAI Whisper (fallback) and LLM.
- `CARTESIA_API_KEY`: (Optional) If using Cartesia TTS.

## Required Linux System Packages (apt)
```bash
sudo apt-get update
sudo apt-get install -y 
    python3.11-venv 
    python3-dev 
    build-essential 
    libportaudio2 
    libasound2-dev 
    libwebrtc-audio-processing-dev
```

## Linux Compatibility Concerns
1. **Audio Device**: The current implementation uses `sounddevice` which defaults to the system microphone. On a headless Linux VM, you may need to:
   - Install `pulseaudio` or `alsa-utils`.
   - Use a virtual audio loopback (`snd-aloop`) if no hardware mic is present.
   - For production FreeSWITCH integration, `MicrophoneStream` in `backend/app/voice/audio_input.py` will likely need to be replaced with an RTP or ESL-based stream.
2. **Web Server Host**: `backend/run.py` currently binds to `127.0.0.1`. For external access to the Web UI on a VM, change this to `0.0.0.0` or use a reverse proxy like Nginx.
3. **Port 8000**: Ensure port 8000 is open in the VM firewall if Web UI is needed.

## Startup Order
1. Ensure `.env` is populated.
2. Create virtual environment: `python3.11 -m venv venv`
3. Activate venv: `source venv/bin/activate`
4. Install dependencies: `pip install -r requirements.freeze.txt`
5. Run application: `python backend/run.py --web`
