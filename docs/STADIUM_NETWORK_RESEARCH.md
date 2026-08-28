# Stadium Network Research Lab

## Scope and current knowledge

The **Tools → Stadium Network… (EXPERIMENTAL)** window is an observation lab,
not a Stadium remote control. Reapcase currently has **no confirmed knowledge**
of the Line 6 application protocol, connection port, authentication, storage
interface, or transfer format. Nothing seen on an arbitrary LAN is identified as
a Helix Stadium without evidence supplied by an advertisement and later research.

The implementation can directly observe only:

* unsolicited mDNS/DNS-SD SRV advertisements and SSDP advertisements received
  during a bounded listening interval;
* whether a manually supplied hostname/IP resolves through the operating system;
* whether an explicitly advertised TCP endpoint accepts a connection; and
* user-entered, UTC-timestamped research markers.

Resolution is not proof that a Stadium is online. An advertisement is evidence
of a service, not proof of its vendor or purpose. Evidence remains `OBSERVED`,
`INFERRED`, or `CONFIRMED`; the software never upgrades confidence by guessing.
The passive listener sends no discovery query. Manual probes do not scan ports.

## Controlled research session

1. Put the computer and user-owned Stadium on the same trusted LAN/Wi-Fi.
2. Open **Tools → Stadium Network… (EXPERIMENTAL)** and choose **Discover
   Devices**. The four-second window listens passively for mDNS and SSDP.
3. If multicast is unavailable, enter the Stadium's known IP or hostname. This
   direct, OS-routable address is a first-class workflow, including over a private
   WireGuard/Tailscale-style VPN.
4. Select the relevant observed row (selection opts it into diagnostic export),
   or press **Probe**. With no advertised endpoint, probe performs DNS/address
   resolution only. It never guesses ports.
5. Start an external Wireshark/OS packet capture if desired. Reapcase does not
   sniff packets or request elevated privileges.
6. Open the official application and perform exactly one controlled action at a
   time. Add markers such as `Official app connected`, `Opened CLOCKSICK`,
   `Changed snapshot`, `Exported Song`, or `Imported Song` immediately around
   each action.
7. Export **Network Diagnostic** JSON. Compare its ISO-8601 UTC marker times with
   frame timestamps in the external capture. Preserve the original capture; the
   current lightweight foundation recognizes pcap/pcapng containers but does not
   parse packets without a future optional adapter.

## Diagnostic privacy

Passive multicast can reveal unrelated devices. Therefore a diagnostic contains
only devices/addresses the user explicitly selected or probed, their advertised
endpoints, lab observations, and markers. It does not inventory the LAN and does
not contain credentials. Review JSON before sharing it because selected private
addresses remain network-sensitive.

## Security boundaries

The lab performs no writes, commands, authentication, credential guessing,
fuzzing, storage enumeration, UPnP/router changes, firmware operations, packet
sniffing, or whole-LAN/port scanning. Do not expose a Stadium to the public
Internet. Future remote access must use a private VPN or an authenticated,
encrypted Reapcase Agent. Network activity runs on a dedicated worker; the Tk
thread only polls completed results, and operations have bounded timeouts.

## Replaceable future architecture

Discovery is optional convenience; direct endpoint addressing is fundamental and
does not assume a same-subnet device. Neutral evidence models and the
`StadiumTransport` boundary are separate from existing archive, workspace,
migration, and implant code. A future confirmed protocol adapter—or remote Agent
that observes its local LAN—can replace the observation-only adapter without
coupling networking to Stadium JSON. Song, audio, device-info, and storage
operations are intentionally absent until controlled captures provide evidence.
