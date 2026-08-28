"""Guided official-app capture experiment UI (no Stadium I/O)."""

from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..stadium_network.experiment import inspect_research_wav


class StadiumCaptureWizard(tk.Toplevel):
    """One-action-at-a-time wizard that records externally observed events."""

    def __init__(self, parent, session, selected_address="", on_marker=None):
        super().__init__(parent)
        self.session = session
        self.selected_address = selected_address.strip()
        self.on_marker = on_marker or (lambda marker: None)
        self.experiment = None
        self._countdown_job = None
        self.title("Helix Stadium Network Research Session")
        self.geometry("600x480")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.transient(parent)
        self._frame = ttk.Frame(self, padding=24)
        self._frame.pack(fill="both", expand=True)
        self._intro()

    def _clear(self):
        if self._countdown_job:
            self.after_cancel(self._countdown_job); self._countdown_job = None
        for child in self._frame.winfo_children():
            child.destroy()

    def _page(self, heading, body, buttons, accent=False):
        self._clear()
        ttk.Label(self._frame, text=heading, font=("TkDefaultFont", 16, "bold"),
                  foreground="#b42318" if accent else "").pack(anchor="w", pady=(0, 18))
        ttk.Label(self._frame, text=body, justify="left", wraplength=540).pack(anchor="w")
        row = ttk.Frame(self._frame); row.pack(side="bottom", fill="x", pady=(20, 0))
        for label, command in buttons:
            ttk.Button(row, text=label, command=command).pack(side="right", padx=(8, 0))

    def _mark(self, name, **details):
        marker = self.experiment.mark(name, **details)
        self.on_marker(marker)
        return marker

    def _intro(self):
        self._page("HELIX STADIUM NETWORK RESEARCH — EXPERIMENTAL",
                   "This guided session will capture one controlled Song creation performed "
                   "with the official Helix Stadium application.\n\n"
                   "Reapcase itself will NOT send commands to or modify Stadium during this "
                   "observation-only experiment.\n\nYou will need:\n\n"
                   "• Helix Stadium connected to Wi-Fi/LAN\n• official Stadium application\n"
                   "• computer on the same network\n• Wireshark or another packet capture tool\n"
                   "• one small WAV test file",
                   [("Start Session", self._parameters), ("Cancel", self.destroy)])

    def _parameters(self):
        self.experiment = self.session.start_official_create_song_experiment()
        self._mark("SESSION_STARTED")
        self._clear()
        ttk.Label(self._frame, text="EXPERIMENT PARAMETERS", font=("TkDefaultFont", 16, "bold")).pack(anchor="w")
        form = ttk.Frame(self._frame); form.pack(fill="x", pady=18)
        self.song = tk.StringVar(value=self.experiment.song_name)
        self.tempo = tk.StringVar(value=str(self.experiment.tempo))
        self.wav = tk.StringVar()
        self.wav_info = tk.StringVar(value="Choose one small WAV test file.")
        self.target = tk.StringVar(value=self.selected_address)
        for row, (label, variable) in enumerate((("Song name", self.song), ("Tempo (BPM)", self.tempo),
                                                  ("Research target (optional)", self.target))):
            ttk.Label(form, text=label + ":").grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(form, textvariable=variable, width=46).grid(row=row, column=1, sticky="ew", padx=8)
        ttk.Label(form, text="Audio:").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Button(form, text="Choose WAV…", command=self._choose_wav).grid(row=3, column=1, sticky="w", padx=8)
        ttk.Label(form, textvariable=self.wav_info, wraplength=430).grid(row=4, column=1, sticky="w", padx=8)
        form.columnconfigure(1, weight=1)
        row = ttk.Frame(self._frame); row.pack(side="bottom", fill="x")
        ttk.Button(row, text="Continue", command=self._accept_parameters).pack(side="right")
        ttk.Button(row, text="Cancel Session", command=self._cancel).pack(side="right", padx=8)

    def _choose_wav(self):
        path = filedialog.askopenfilename(parent=self, title="Choose small research WAV",
                                          filetypes=(("WAV audio", "*.wav"),))
        if not path:
            return
        try:
            audio = inspect_research_wav(Path(path))
        except (OSError, wave.Error) as exc:  # wave is imported below for Python 3.9 clarity
            messagebox.showerror("Cannot read WAV", str(exc), parent=self); return
        self.experiment.audio = [audio]
        self.wav.set(path)
        self.wav_info.set(f"{audio.filename} — {audio.size:,} bytes — {audio.duration:.2f}s — "
                          f"{audio.sample_rate} Hz — {audio.channels} channel(s)\nSHA-256: {audio.sha256}")

    def _accept_parameters(self):
        try:
            tempo = int(self.tempo.get())
            if not 1 <= tempo <= 999:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid tempo", "Enter a whole-number BPM from 1 to 999.", parent=self); return
        if not self.song.get().strip() or not self.experiment.audio:
            messagebox.showerror("Parameters required", "Enter a Song name and choose a WAV file.", parent=self); return
        self.experiment.song_name = self.song.get().strip(); self.experiment.tempo = tempo
        if self.target.get().strip():
            address = self.target.get().strip()
            self.session.select_address(address)
            self.experiment.target = {"address": address, "device_evidence": "USER_SELECTED"}
        self._step1()

    def _step1(self):
        self._mark("CLEAN_START_REQUESTED")
        self._page("STEP 1 / 13 — CLEAN START", "Close the official Helix Stadium application if it is currently running.\n\n"
                   "Leave Stadium powered on and connected to the network.\n\nDo not start Wireshark capture yet.",
                   [("Ready", lambda: (self._mark("CLEAN_START_CONFIRMED"), self._step2())), ("Cancel Session", self._cancel)])

    def _step2(self):
        self._mark("CAPTURE_TOOL_PREPARE_REQUESTED")
        self._page("STEP 2 / 13 — PREPARE PACKET CAPTURE", "Open Wireshark.\n\nSelect the network interface used to communicate with Stadium.\n\n"
                   "Do NOT start capture yet. Return here when Wireshark is ready.",
                   [("Ready", lambda: (self._mark("CAPTURE_TOOL_READY"), self._step3())), ("Cancel Session", self._cancel)])

    def _step3(self):
        self._mark("CAPTURE_START_REQUESTED")
        self._page("STEP 3 / 13 — START CAPTURE", "Start Wireshark capture NOW.\n\nDo not open the official Stadium app yet.\n\n"
                   "As soon as capture has started, return here.",
                   [("Capture Started", lambda: (self._mark("CAPTURE_STARTED"), self._baseline("STEP 4 / 13 — BASELINE", "PRE_APP_BASELINE", self._step5))),
                    ("Cancel Session", self._cancel)])

    def _baseline(self, heading, marker_prefix, next_page):
        def begin():
            self._mark(marker_prefix + "_STARTED")
            self._countdown(5, marker_prefix, next_page)
        self._page(heading, "Leave everything untouched for 5 seconds.\n\nThis gives us a clean network baseline.",
                   [("Begin Baseline", begin), ("Cancel Session", self._cancel)])

    def _countdown(self, value, prefix, next_page):
        self._page(self._frame.winfo_children()[0].cget("text"),
                   f"Recording observation-only idle traffic…\n\n{value}", [("Cancel Session", self._cancel)])
        if value:
            self._countdown_job = self.after(1000, lambda: self._countdown(value - 1, prefix, next_page))
        else:
            self._countdown_job = None; self._mark(prefix + "_COMPLETE"); next_page()

    def _step5(self):
        self._mark("OFFICIAL_APP_LAUNCH_REQUESTED")
        self._page("STEP 5 / 13 — OPEN OFFICIAL APP", "Open the official Helix Stadium application NOW.\n\n"
                   "Do not perform any other action yet. Once the application is fully open:",
                   [("App Open", lambda: (self._mark("OFFICIAL_APP_OPEN_CONFIRMED"), self._step6())), ("Cancel Session", self._cancel)])

    def _step6(self):
        self._mark("CONNECT_ACTION_REQUESTED")
        self._page("STEP 6 / 13 — CONNECT TO STADIUM", "In the official application, connect to your Helix Stadium.\n\n"
                   "Do not perform any other operation. When the application reports that Stadium is connected, respond immediately.",
                   [("Stadium Connected", lambda: (self._mark("CONNECT_CONFIRMED"), self._post_connect())), ("Cancel Session", self._cancel)])

    def _post_connect(self):
        self._mark("POST_CONNECT_BASELINE_STARTED")
        self._countdown(5, "POST_CONNECT_BASELINE", self._step8)

    def _step8(self):
        audio = "\n        ".join(item.filename for item in self.experiment.audio)
        self._mark("CREATE_FORM_PREPARE_REQUESTED")
        self._page("STEP 8 / 13 — PREPARE TEST SONG", "In the official Stadium application, open the Create Song workflow.\n\nEnter EXACTLY:\n\n"
                   f"Song:  {self.experiment.song_name}\nTempo:  {self.experiment.tempo} BPM\nAudio:  {audio}\n\n"
                   "Fill in everything but DO NOT press the final Create / Send / Transfer button yet.",
                   [("Ready To Create", lambda: (self._mark("CREATE_FORM_READY"), self._step9())), ("Cancel Session", self._cancel)])

    def _step9(self):
        self._page("STEP 9 / 13 — CREATE SONG", "READY TO CAPTURE.\n\nWhen you press MARK & GO:\n\n1. this popup will close immediately\n"
                   "2. switch to the official Stadium app\n3. IMMEDIATELY press Create / Send\n4. do nothing else until transfer finishes",
                   [("MARK & GO", self._mark_and_go), ("Cancel Session", self._cancel)], accent=True)

    def _mark_and_go(self):
        self._mark("CREATE_SONG_TRIGGER"); self._mark("TRANSFER_OBSERVATION_STARTED")
        self.attributes("-topmost", False); self.transient(""); self._page("STADIUM TEST TRANSFER IN PROGRESS",
            "Let the official application finish.\n\nDo not perform other Stadium actions.\n\n"
            "When the official application reports that creation/transfer has completed:",
            [("Transfer Complete", self._transfer_complete)])

    def _transfer_complete(self):
        marker = self._mark("TRANSFER_COMPLETE_CONFIRMED")
        trigger = next(m for m in self.experiment.markers if m.name == "CREATE_SONG_TRIGGER")
        self.experiment.user_observed_operation_duration = marker.elapsed_seconds - trigger.elapsed_seconds
        self._verify()

    def _verify(self):
        self._clear()
        ttk.Label(self._frame, text="STEP 11 / 13 — VERIFY ON STADIUM", font=("TkDefaultFont", 16, "bold")).pack(anchor="w")
        ttk.Label(self._frame, text="On the physical Stadium, verify that the new Song exists. If practical, confirm its name, tempo, and audio association.", wraplength=540).pack(anchor="w", pady=18)
        self.note = tk.StringVar()
        ttk.Label(self._frame, text="Optional short note:").pack(anchor="w")
        ttk.Entry(self._frame, textvariable=self.note).pack(fill="x", pady=6)
        row = ttk.Frame(self._frame); row.pack(side="bottom", fill="x")
        for label, result in (("Success", "SUCCESS"), ("Song Exists But Problem", "PROBLEM"), ("Failed", "FAILED")):
            ttk.Button(row, text=label, command=lambda r=result: self._verified(r)).pack(side="right", padx=4)

    def _verified(self, result):
        self.experiment.result = result; self.experiment.notes = self.note.get().strip()
        self._mark("STADIUM_VERIFICATION", result=result, note=self.experiment.notes)
        self._mark("POST_TRANSFER_BASELINE_STARTED")
        self._countdown(5, "POST_TRANSFER_BASELINE", self._step13)

    def _step13(self):
        self._mark("CAPTURE_STOP_REQUESTED")
        suggested = f"reapcase_stadium_create_{self.experiment.session_id}.pcapng"
        self._page("STEP 13 / 13 — STOP CAPTURE", "Stop Wireshark capture NOW. Save the capture as .pcapng if possible.\n\nSuggested filename:\n\n" + suggested,
                   [("Capture Stopped", lambda: (self._mark("CAPTURE_STOPPED"), self._capture())), ("Cancel Session", self._cancel)])

    def _capture(self):
        self._page("ASSOCIATE PACKET CAPTURE",
                   "Associate the saved .pcap or .pcapng with this session?\n\n"
                   "Reapcase validates and references the file. It will not parse or silently copy it.",
                   [("Choose .pcap / .pcapng…", self._choose_capture),
                    ("Skip", self._export_session)])

    def _choose_capture(self):
        path = filedialog.askopenfilename(parent=self, title="Associate packet capture (or Cancel to skip)",
                                          filetypes=(("Packet captures", "*.pcap *.pcapng"),))
        if not path:
            return
        try:
            self.experiment.set_capture(Path(path))
        except (OSError, ValueError) as exc:
            messagebox.showerror("Invalid packet capture", str(exc), parent=self); return
        self._export_session()

    def _export_session(self):
        self.experiment.finish()
        parent = filedialog.askdirectory(parent=self, title="Choose folder for StadiumNetworkResearch")
        if not parent:
            messagebox.showwarning("Session not exported", "Choose a folder to save session.json and README.txt.", parent=self); return
        try:
            folder = self.experiment.export_folder(Path(parent))
        except OSError as exc:
            messagebox.showerror("Cannot export session", str(exc), parent=self); return
        self._finish(folder)

    def _finish(self, folder):
        self._page("RESEARCH SESSION COMPLETE", f"Session saved to:\n{folder}\n\nThe capture was referenced, not copied. Reapcase sent no commands to Stadium.",
                   [("Finish", self.destroy), ("Run Comparison Test…", self._comparison)])

    def _comparison(self):
        old = self.experiment
        self.experiment = self.session.start_official_create_song_experiment()
        self.experiment.song_name = old.song_name; self.experiment.tempo = old.tempo
        self.experiment.audio = list(old.audio); self.experiment.target = old.target
        self._mark("SESSION_STARTED", comparison_of=old.session_id)
        self._step1()

    def _cancel(self):
        if self.experiment:
            self._mark("SESSION_CANCELLED"); self.experiment.finish()
        self.destroy()


# ``wave.Error`` is intentionally caught only around user-selected research WAVs.
import wave
