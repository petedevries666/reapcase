"""Tk presentation for the isolated, observation-only Stadium Network lab."""

from __future__ import annotations

from datetime import timezone
from threading import Event
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..stadium_network.discovery import PassiveDiscovery
from ..stadium_network.probe import SafeProbe, validate_address
from ..stadium_network.models import Confidence, ProtocolObservation
from ..stadium_network.session import NetworkResearchSession
from .background_operations import BackgroundOperations


class StadiumNetworkWindow(tk.Toplevel):
    def __init__(self, parent, version="0.1.0"):
        super().__init__(parent)
        self.title("Stadium Network — EXPERIMENTAL")
        self.geometry("860x650")
        self.minsize(700, 500)
        self.session = NetworkResearchSession(version)
        self.discovery = PassiveDiscovery()
        self.prober = SafeProbe()
        self.worker = BackgroundOperations("stadium-network")
        self.cancel = Event()
        self.status = tk.StringVar(value="Not connected — observation only")
        self.address = tk.StringVar()
        self.marker = tk.StringVar()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(50, self._poll)

    def _build(self):
        outer = ttk.Frame(self, padding=12); outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="STADIUM NETWORK LAB — EXPERIMENTAL",
                  font=("TkDefaultFont", 13, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Passive discovery and non-destructive observations only. No device writes.").pack(anchor="w", pady=(0, 8))
        connection = ttk.LabelFrame(outer, text="Connection", padding=8); connection.pack(fill="x")
        ttk.Label(connection, textvariable=self.status).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Button(connection, text="Discover Devices", command=self._discover).grid(row=1, column=0, pady=6, sticky="w")
        ttk.Button(connection, text="Cancel", command=self._cancel).grid(row=1, column=2, pady=6)
        ttk.Label(connection, text="Stadium IP / hostname:").grid(row=2, column=0, sticky="w")
        ttk.Entry(connection, textvariable=self.address, width=38).grid(row=2, column=1, padx=6, sticky="ew")
        ttk.Button(connection, text="Probe", command=self._probe).grid(row=2, column=2)
        connection.columnconfigure(1, weight=1)
        devices = ttk.LabelFrame(outer, text="Observed Devices (select to include in diagnostic)", padding=8)
        devices.pack(fill="both", expand=True, pady=8)
        self.tree = ttk.Treeview(devices, columns=("name", "address", "services"), show="headings", height=7)
        for key, label, width in (("name", "Name", 220), ("address", "Address", 170), ("services", "Advertised services", 360)):
            self.tree.heading(key, text=label); self.tree.column(key, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True); self.tree.bind("<<TreeviewSelect>>", self._select)
        lab = ttk.LabelFrame(outer, text="Network Log / Research Markers", padding=8); lab.pack(fill="both", expand=True)
        marker_row = ttk.Frame(lab); marker_row.pack(fill="x")
        ttk.Entry(marker_row, textvariable=self.marker).pack(side="left", fill="x", expand=True)
        ttk.Button(marker_row, text="Add MARK", command=self._add_marker).pack(side="left", padx=5)
        ttk.Button(marker_row, text="Export Network Diagnostic...", command=self._export).pack(side="right")
        self.log = tk.Text(lab, height=9, state="disabled", wrap="word"); self.log.pack(fill="both", expand=True, pady=(6, 0))

    def _append(self, text, timestamp=None):
        stamp = (timestamp or self.session._clock()).astimezone(timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"
        self.log.configure(state="normal"); self.log.insert("end", f"{stamp}  {text}\n")
        self.log.see("end"); self.log.configure(state="disabled")

    def _discover(self):
        self.cancel.clear()
        if self.worker.start("discovery", lambda: self.discovery.discover(timeout=4.0, cancel=self.cancel)):
            self.status.set("Listening passively for mDNS / SSDP advertisements…")

    def _probe(self):
        try:
            address = validate_address(self.address.get())
        except ValueError as exc:
            messagebox.showerror("Invalid address", str(exc), parent=self); return
        self.session.select_address(address)
        endpoint = self.session.devices.get(address)
        endpoint = endpoint.services[0] if endpoint and endpoint.services else None
        self.cancel.clear()
        if self.worker.start("probe", lambda: self.prober.probe(address, endpoint, timeout=2.0,
                                                                 cancel=self.cancel)):
            self.status.set(f"Safely probing {address}…")

    def _cancel(self):
        self.cancel.set()
        self.status.set("Cancellation requested…")

    def _poll(self):
        if self.worker.closed:
            return
        result = self.worker.poll()
        if result:
            if result.error:
                self.status.set(f"{result.name.title()} failed"); self._append(f"ERROR: {result.error}")
            elif result.name == "discovery":
                for device in result.value:
                    self.session.record_discovery(device)
                    services = ", ".join(f"{e.service_name}:{e.port}/{e.transport}" for e in device.services)
                    self.tree.insert("", "end", iid=device.address,
                                     values=(device.display_name, device.address, services or device.txt_metadata.get("service", "")))
                    self._append(f"discovered service host={device.hostname or '?'} address={device.address} services={services or 'generic'}")
                self.status.set(f"Passive discovery complete: {len(result.value)} device(s) observed")
            else:
                probe = result.value
                self.session.observations.append(ProtocolObservation(
                    self.session._clock(), "outbound", probe.endpoint, None,
                    "DNS" if probe.endpoint is None else probe.endpoint.transport,
                    f"Probe {probe.address}: {probe.detail}", Confidence.OBSERVED))
                self.status.set(probe.detail); self._append(f"probe {probe.address}: {probe.detail}")
        self.after(50, self._poll)

    def _select(self, _event=None):
        selection = self.tree.selection()
        if selection:
            address = selection[0]; self.address.set(address); self.session.select_address(address)

    def _add_marker(self):
        try:
            marker = self.session.add_marker(self.marker.get())
        except ValueError:
            return
        self._append(f"MARK: {marker.annotation}", marker.timestamp); self.marker.set("")

    def _export(self):
        path = filedialog.asksaveasfilename(parent=self, title="Export Network Diagnostic",
                                            defaultextension=".json", filetypes=(("JSON", "*.json"),))
        if path:
            self.session.export(__import__("pathlib").Path(path)); self._append(f"exported diagnostic: {path}")

    def _close(self):
        self.cancel.set(); self.worker.close(); self.destroy()
