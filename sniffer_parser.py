# # library downloads:
# #   pip install pyshark
# #
# # REQUIREMENTS (Windows):
# #   - Install Wireshark (includes tshark.exe)
# #   - Install Npcap (Wireshark installer can do this)
# #
# # Quick checks:
# #   "C:\Program Files\Wireshark\tshark.exe" -v
# #   "C:\Program Files\Wireshark\tshark.exe" -D   (then set LIVE_INTERFACE)

# import json
# import time
# import os
# from datetime import datetime, timezone

# import pyshark

# # ------------------------------
# # CONFIG
# # ------------------------------

# # Output file consumed by app.py (JSON Lines)
# OUT_JSONL = "zigbee_paths.jsonl"

# # Choose ONE of these modes:
# PCAP_FILE = None          # e.g. "capture.pcapng"
# LIVE_INTERFACE = "1"      # tshark interface id/name; run tshark -D to confirm

# # Point PyShark to tshark.exe explicitly (fixes "TShark not found")
# # Change this path if Wireshark is installed elsewhere.
# TSHARK_PATH = r"C:\Program Files\Wireshark\tshark.exe"
# if not os.path.exists(TSHARK_PATH):
#     # fallback to x86 install
#     alt = r"C:\Program Files (x86)\Wireshark\tshark.exe"
#     if os.path.exists(alt):
#         TSHARK_PATH = alt

# # Correlation / flush tuning
# GROUP_WINDOW_SEC = 1.0    # hops for same message must appear within this window
# IDLE_FLUSH_SEC = 0.25     # if no new hop for this message, flush it


# # ------------------------------
# # HELPERS
# # ------------------------------
# def iso_utc(ts_float: float) -> str:
#     dt = datetime.fromtimestamp(ts_float, tz=timezone.utc)
#     return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


# def norm_ieee(addr: str) -> str:
#     if not addr:
#         return None
#     s = str(addr).strip().lower()
#     # ensure 0x prefix when address is 16 hex chars
#     if not s.startswith("0x") and len(s) == 16:
#         s = "0x" + s
#     return s


# def safe_int(x):
#     try:
#         sx = str(x)
#         # allow "0x.." parsing
#         return int(sx, 0) if sx.startswith("0x") else int(sx)
#     except Exception:
#         return None


# # Track in-flight groups: key -> entry
# inflight = {}


# def make_key(nwk_src, aps_counter, nwk_seq):
#     # Prefer APS counter; fallback to NWK seq
#     if aps_counter is not None:
#         return (str(nwk_src), "aps", int(aps_counter))
#     if nwk_seq is not None:
#         return (str(nwk_src), "nwk", int(nwk_seq))
#     # last resort (bad): just by nwk_src
#     return (str(nwk_src), "nwk", -1)


# def flush_if_needed(out, now_ts):
#     to_flush = []
#     for k, entry in inflight.items():
#         if now_ts - entry["last_seen_ts"] > IDLE_FLUSH_SEC:
#             to_flush.append(k)

#     for k in to_flush:
#         entry = inflight.pop(k, None)
#         if entry and len(entry.get("path", [])) >= 1:
#             out.write(json.dumps(entry, ensure_ascii=False) + "\n")
#             out.flush()


# def extract_fields(pkt):
#     """
#     Extract:
#       - timestamp
#       - nwk_src (short address)
#       - aps_counter (if visible)
#       - nwk_seq (if visible)
#       - tx_ieee, rx_ieee (from wpan src64/dst64 when available)
#       - rssi (if available)

#     Field names vary by capture & dissector; this tries common ones.
#     """
#     ts = float(pkt.sniff_timestamp)

#     nwk_src = None
#     if hasattr(pkt, "zbee_nwk"):
#         nwk_src = getattr(pkt.zbee_nwk, "src", None)

#     # counters
#     aps_counter = None
#     if hasattr(pkt, "zbee_aps"):
#         aps_counter = getattr(pkt.zbee_aps, "counter", None)
#         aps_counter = safe_int(aps_counter)

#     nwk_seq = None
#     if hasattr(pkt, "zbee_nwk"):
#         # common name: seqno
#         nwk_seq = getattr(pkt.zbee_nwk, "seqno", None)
#         nwk_seq = safe_int(nwk_seq)

#     # MAC-layer 64-bit addresses (best for hop reconstruction)
#     tx_ieee = None
#     rx_ieee = None
#     if hasattr(pkt, "wpan"):
#         tx_ieee = getattr(pkt.wpan, "src64", None)
#         rx_ieee = getattr(pkt.wpan, "dst64", None)
#         tx_ieee = norm_ieee(tx_ieee)
#         rx_ieee = norm_ieee(rx_ieee)

#     # RSSI (varies by sniffer)
#     rssi = None
#     for attr in ("wpan_rssi", "rssi", "radiotap_dbm_antsignal"):
#         if hasattr(pkt, attr):
#             rssi = getattr(pkt, attr)
#             break
#     rssi = safe_int(rssi)

#     return ts, nwk_src, aps_counter, nwk_seq, tx_ieee, rx_ieee, rssi


# # ------------------------------
# # MAIN
# # ------------------------------
# def main():
#     # Hard fail early with a clear message if tshark is missing
#     if not os.path.exists(TSHARK_PATH):
#         raise RuntimeError(
#             "tshark.exe not found. Install Wireshark or set TSHARK_PATH to the correct location.\n"
#             f"Current TSHARK_PATH: {TSHARK_PATH}"
#         )

#     # Open capture source
#     if PCAP_FILE:
#         print(f"[SNIFFER] reading PCAP: {PCAP_FILE}")
#         cap = pyshark.FileCapture(
#             PCAP_FILE,
#             keep_packets=False,
#             tshark_path=TSHARK_PATH
#         )
#     else:
#         if not LIVE_INTERFACE:
#             raise RuntimeError("LIVE_INTERFACE is None/empty and PCAP_FILE is None. Choose one mode.")
#         print(f"[SNIFFER] live capture on interface: {LIVE_INTERFACE}")
#         cap = pyshark.LiveCapture(
#             interface=LIVE_INTERFACE,
#             tshark_path=TSHARK_PATH
#         )

#     print(f"[SNIFFER] writing JSONL to: {os.path.abspath(OUT_JSONL)}")

#     with open(OUT_JSONL, "a", encoding="utf-8") as out:
#         last_cleanup = time.time()

#         for pkt in cap.sniff_continuously():
#             try:
#                 # Must have Zigbee NWK layer
#                 if not hasattr(pkt, "zbee_nwk"):
#                     continue

#                 ts, nwk_src, aps_counter, nwk_seq, tx_ieee, rx_ieee, rssi = extract_fields(pkt)
#                 now_ts = ts

#                 # Periodic cleanup flush
#                 if time.time() - last_cleanup > 0.5:
#                     flush_if_needed(out, now_ts)
#                     last_cleanup = time.time()

#                 key = make_key(nwk_src, aps_counter, nwk_seq)

#                 # Start / update group
#                 entry = inflight.get(key)
#                 if entry is None:
#                     entry = {
#                         "ts": iso_utc(ts),
#                         "nwk_src": str(nwk_src) if nwk_src is not None else None,
#                         "aps_counter": aps_counter,
#                         "nwk_seq": nwk_seq,
#                         "path": [],
#                         "first_seen_ts": now_ts,
#                         "last_seen_ts": now_ts,
#                     }
#                     inflight[key] = entry
#                 else:
#                     # If this group is too old, flush and restart
#                     if now_ts - entry["first_seen_ts"] > GROUP_WINDOW_SEC:
#                         out.write(json.dumps(entry, ensure_ascii=False) + "\n")
#                         out.flush()
#                         entry = {
#                             "ts": iso_utc(ts),
#                             "nwk_src": str(nwk_src) if nwk_src is not None else None,
#                             "aps_counter": aps_counter,
#                             "nwk_seq": nwk_seq,
#                             "path": [],
#                             "first_seen_ts": now_ts,
#                             "last_seen_ts": now_ts,
#                         }
#                         inflight[key] = entry

#                 # Add hop if we have MAC addresses
#                 hop = {"tx_ieee": tx_ieee, "rx_ieee": rx_ieee, "rssi": rssi}

#                 # avoid duplicates
#                 if not entry["path"] or entry["path"][-1] != hop:
#                     entry["path"].append(hop)

#                 entry["last_seen_ts"] = now_ts

#             except KeyboardInterrupt:
#                 print("[SNIFFER] stopped by user")
#                 break
#             except Exception:
#                 # swallow packet-level decode issues
#                 continue


# if __name__ == "__main__":
#     main()
