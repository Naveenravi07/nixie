#!/usr/bin/env python3
"""Always-on PipeWire microphone listener with local Whisper transcription."""

from __future__ import annotations

import argparse
import queue
import signal
import socket
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

import numpy as np

from app.environment import load_environment
from app.event_log import log_event
from app.config import load_config
from app.voice import audio as voice_audio
from app.voice import recognition as voice_recognition
from app.voice.popup_control import PopupController as VoicePopupController
from app.voice.server_client import NixiServerClient, ServerRequestError
from app.voice.stt import SarvamRealtimeTranscriber
from app.voice.tts import SarvamSpeaker

class NixiVoiceDaemon:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config = load_config(config_path)
        self.voice = self.config.voice
        self.segmenter = voice_audio.UtteranceSegmenter(self.voice)
        self.recorder = voice_audio.PipeWireRecorder(self.voice)
        self.popup = VoicePopupController()
        self.sarvam_transcriber = SarvamRealtimeTranscriber(
            self.config.stt,
            self.voice.sample_rate,
        )
        self.speaker = SarvamSpeaker(self.config.tts)
        self.server_client = NixiServerClient(self.config.server)
        self.awaiting_command = False
        self.command_deadline = 0.0
        self.running = True
        self.trigger_event = threading.Event()
        self.manual_recording = False
        self.manual_frames: list[np.ndarray] = []
        self.manual_recording_start = 0.0
        self.popup_close_timer: threading.Timer | None = None
        self.followup_stop_event: threading.Event | None = None
        self._start_trigger_server()

    def _start_trigger_server(self) -> None:
        def server_loop() -> None:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind(("127.0.0.1", 8768))
                    s.listen()
                    while self.running:
                        s.settimeout(0.5)
                        try:
                            conn, addr = s.accept()
                        except socket.timeout:
                            continue
                        with conn:
                            data = conn.recv(1024)
                            if b"trigger" in data:
                                self.trigger_event.set()
                except Exception as e:
                    print(f"Trigger server error: {e}", file=sys.stderr)
        
        t = threading.Thread(target=server_loop, daemon=True, name="nixi-trigger-server")
        t.start()

    def run(self) -> None:
        self.recorder.start()
        print(
            "Nixi trigger listener ready. Use the Google command/shortcut to start/stop recording.",
            flush=True,
        )
        try:
            while self.running:
                if self.trigger_event.is_set():
                    self.trigger_event.clear()
                    self._handle_manual_trigger()
                
                if self.manual_recording:
                    if time.monotonic() - self.manual_recording_start >= 45.0:
                        print("Max recording duration reached (45s). Automatically stopping...", flush=True)
                        self._handle_manual_trigger()  # Emulate user toggle stop
                        continue
                    
                    try:
                        frame = self.recorder.frame_queue.get(timeout=0.1)
                        if frame is not None:
                            self.manual_frames.append(frame)
                    except queue.Empty:
                        continue
                else:
                    time.sleep(0.05)
        finally:
            self.popup.close()
            self.recorder.stop()

    def _handle_manual_trigger(self) -> None:
        if not self.manual_recording:
            # Interrupt active speaker and stop any ongoing auto follow-up
            if self.followup_stop_event is not None:
                self.followup_stop_event.set()
                self.followup_stop_event = None
            if self.popup_close_timer is not None:
                self.popup_close_timer.cancel()
                self.popup_close_timer = None
            self.speaker.stop()
            self.recorder.discard_pending()
            self.manual_recording = True
            self.manual_recording_start = time.monotonic()
            self.manual_frames = []
            self.popup.open()
            print("Recording started...", flush=True)
        else:
            # Stop recording and process
            self.manual_recording = False
            print("Recording stopped. Processing...", flush=True)
            if self.manual_frames:
                utterance = np.concatenate(self.manual_frames)
                self.manual_frames = []
                # Process the command in a background thread to keep trigger responsive
                t = threading.Thread(
                    target=self._process_manual_utterance,
                    args=(utterance,),
                    daemon=True,
                )
                t.start()

    def _schedule_popup_close(self) -> None:
        if self.popup_close_timer is not None:
            self.popup_close_timer.cancel()
        
        def close_action() -> None:
            if not self.manual_recording and self.followup_stop_event is None:
                print("No follow-up detected. Closing popup.", flush=True)
                self.popup.close()
        
        self.popup_close_timer = threading.Timer(
            self.voice.command_timeout_seconds,
            close_action,
        )
        self.popup_close_timer.daemon = True
        self.popup_close_timer.start()

    def _check_exit_command(self, command: str) -> bool:
        normalized = voice_recognition.normalize_transcript(command)
        exit_phrases = {
            "shut up",
            "you may close now",
            "you can leave now",
            "you may leave now",
            "close now",
            "stop listening",
            "exit nixi",
            "quit",
            "exit",
            "stop",
            "close",
            "bye bye",
            "bye",
            "goodbye",
        }
        if any(phrase in normalized for phrase in exit_phrases):
            print("Exit command detected. Returning to idle state...", flush=True)
            self.popup.close()
            # Cancel any pending popup closes and reset the states
            if self.popup_close_timer is not None:
                self.popup_close_timer.cancel()
                self.popup_close_timer = None
            if self.followup_stop_event is not None:
                self.followup_stop_event.set()
                self.followup_stop_event = None
            self.speaker.stop()
            self.recorder.discard_pending()
            self.segmenter.reset()
            return True
        return False

    def _process_manual_utterance(self, utterance: np.ndarray) -> None:
        request_id = uuid.uuid4().hex[:12]
        try:
            print("Transcribing with Sarvam...", flush=True)
            command = self.sarvam_transcriber.transcribe_pcm(utterance, request_id)
        except Exception as error:
            log_event("voice", "stt.failed", request_id, error=str(error))
            print(f"STT failed: {error}", file=sys.stderr)
            self._schedule_popup_close()
            return

        command = command.strip()
        if not command:
            print("No speech detected.", flush=True)
            self._schedule_popup_close()
            return

        print(f"Heard: {command}", flush=True)
        if self._check_exit_command(command):
            return

        response_text = self._request_response(command, request_id)
        if response_text is None:
            self._schedule_popup_close()
            return

        print(f"Speaking response: {response_text}", flush=True)
        self.speaker.speak(response_text, request_id=request_id)
        self._schedule_popup_close()

    def _request_response(self, command: str, request_id: str) -> str | None:
        server_started = time.perf_counter()
        try:
            return self.server_client.send_message(command, request_id)
        except ServerRequestError as error:
            print(str(error), file=sys.stderr)
            log_event(
                "voice",
                "server_request.failed",
                request_id,
                duration_ms=round((time.perf_counter() - server_started) * 1000),
                status=error.status,
                error=str(error),
            )
            return None

    def stop(self) -> None:
        self.running = False
        self.recorder.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the always-on local Nixi voice daemon.")
    parser.add_argument("--config", type=Path, help="path to nixi.toml")
    parser.add_argument("--trigger", action="store_true", help="Send a trigger to the running voice daemon")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.trigger:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect(("127.0.0.1", 8768))
                s.sendall(b"trigger\n")
            print("Trigger sent successfully.", flush=True)
            return
        except Exception as error:
            print(f"Failed to send trigger to voice daemon: {error}", file=sys.stderr)
            sys.exit(1)

    load_environment()
    try:
        daemon = NixiVoiceDaemon(args.config)
    except RuntimeError as error:
        raise SystemExit(f"nixi-voice: {error}") from error

    signal.signal(signal.SIGINT, daemon.stop)
    signal.signal(signal.SIGTERM, daemon.stop)
    daemon.run()


if __name__ == "__main__":
    main()
