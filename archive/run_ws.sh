#!/bin/bash
export $(grep -v '^#' /home/angelo/lescall/calling-agent/.env | xargs)
/home/angelo/lescall/calling-agent/venv/bin/python /home/angelo/lescall/ws_audio_receiver.py
