"""Central semantic text for timeline badges (position is drawn separately)."""

from ..timeline import TimelineEvent


def badge_text(event: TimelineEvent) -> str:
    if event.source.type == "LIGHTS":
        return event.data.get("name", "LIGHTS")
    source, data = event.source, event.data
    alias = data.get("rig_alias", {})
    if alias.get("system") == "video":
        return f"VIDEO {alias.get('video', '')} {alias['action'].replace('_', ' ').upper()}".replace("  ", " ")
    if alias.get("system") == "second_helix":
        if alias.get("action") == "expression":
            percentage = 0 if alias["value"] == 0 else 100
            return f"EXP{alias['expression']} {percentage}%"
        if alias.get("action") == "snapshot":
            return f"BASS SNAP {alias['snapshot']}"
        return f"BASS {alias['action'].upper()}"
    if source.type == "TIME":
        return (f"{data.get('tempo', '?'):g} BPM · "
                f"{data.get('time_signature_numerator', '?')}/{data.get('time_signature_denominator', '?')}")
    if source.type == "LOOPER":
        return f"LOOPER {data.get('action', '').upper()}".strip()
    if source.type == "PRESETSNAP" and data.get("snapshot"):
        snapshot = str(data["snapshot"])
        return snapshot.upper() if snapshot.lower().startswith("snap ") else f"SNAP {snapshot}"
    human = data.get("name") or data.get("label")
    return str(human or source.type).strip()
