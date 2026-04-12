"""
Traffic Monitoring and Statistics Collector
SDN Project - UE24CS252B Computer Networks
PES University

Features:
  - Learning Switch (Forwarding)
  - Firewall / Blocking Rules (h1 <-> h4 blocked)
  - Periodic Traffic Statistics Collection
  - Summary Report Generation
"""

from pox.core import core
from pox.lib.util import dpid_to_str
from pox.lib.recoco import Timer
from pox.lib.addresses import EthAddr
import pox.openflow.libopenflow_01 as of
from pox.lib.revent import *
import datetime

log = core.getLogger()

# ── Firewall Rules: these pairs are BLOCKED ──────────────────────────────────
# h1 = 10.0.0.1, h4 = 10.0.0.4
BLOCKED_PAIRS = [
    ("10.0.0.1", "10.0.0.4"),
    ("10.0.0.4", "10.0.0.1"),
]

class TrafficMonitor(EventMixin):

    def __init__(self):
        self.mac_to_port = {}
        self.mac_to_ip   = {}       # Track MAC -> IP for firewall
        self.ip_to_mac   = {}       # Track IP -> MAC for firewall
        self.flow_stats  = {}
        self.connections = {}
        self.blocked_count = 0      # Count how many packets were blocked

        core.openflow.addListeners(self)
        Timer(10, self._request_stats, recurring=True)

        log.info("=== Traffic Monitor Started ===")
        log.info("Blocked pairs: %s", BLOCKED_PAIRS)

    # ── Switch connects ───────────────────────────────────────────────────────
    def _handle_ConnectionUp(self, event):
        dpid = dpid_to_str(event.dpid)
        self.connections[dpid] = event.connection
        self.mac_to_port[dpid] = {}
        log.info("Switch connected: %s", dpid)

        # Install DROP rules for blocked pairs immediately
        self._install_firewall_rules(event.connection)

        # Table-miss: send unknown packets to controller
        msg = of.ofp_flow_mod()
        msg.priority = 0
        msg.actions.append(of.ofp_action_output(port=of.OFPP_CONTROLLER))
        event.connection.send(msg)

        log.info("Firewall rules installed on switch %s", dpid)

    # ── Switch disconnects ────────────────────────────────────────────────────
    def _handle_ConnectionDown(self, event):
        dpid = dpid_to_str(event.dpid)
        self.connections.pop(dpid, None)
        self.flow_stats.pop(dpid, None)
        log.info("Switch disconnected: %s", dpid)

    # ── Install firewall DROP rules at high priority ──────────────────────────
    def _install_firewall_rules(self, connection):
        for src_ip, dst_ip in BLOCKED_PAIRS:
            msg = of.ofp_flow_mod()
            msg.match.dl_type = 0x0800          # IPv4
            msg.match.nw_src  = src_ip
            msg.match.nw_dst  = dst_ip
            msg.priority      = 100             # Higher than forwarding rules
            msg.actions       = []              # Empty = DROP
            connection.send(msg)
            log.info("FIREWALL: DROP rule installed  %s --> %s", src_ip, dst_ip)

    # ── Check if a packet should be blocked ───────────────────────────────────
    def _is_blocked(self, src_ip, dst_ip):
        return (src_ip, dst_ip) in BLOCKED_PAIRS

    # ── Packet-In: Learning Switch + Firewall Check ───────────────────────────
    def _handle_PacketIn(self, event):
        from pox.lib.packet import ipv4 as ipv4_pkt, arp as arp_pkt

        pkt        = event.parsed
        dpid       = dpid_to_str(event.dpid)
        in_port    = event.port
        connection = event.connection

        if not pkt.parsed:
            return

        src_mac = str(pkt.src)
        dst_mac = str(pkt.dst)

        # Learn MAC -> port
        self.mac_to_port[dpid][src_mac] = in_port

        # Extract IP addresses if IPv4 packet
        src_ip = None
        dst_ip = None
        ip_layer = pkt.find('ipv4')
        if ip_layer:
            src_ip = str(ip_layer.srcip)
            dst_ip = str(ip_layer.dstip)
            self.ip_to_mac[src_ip] = src_mac

        # ── Firewall check ────────────────────────────────────────────────────
        if src_ip and dst_ip and self._is_blocked(src_ip, dst_ip):
            self.blocked_count += 1
            log.warning("FIREWALL: BLOCKED packet  %s --> %s  (total blocked: %d)",
                        src_ip, dst_ip, self.blocked_count)
            return      # Drop packet — do not forward

        # ── Learning switch: forward packet ───────────────────────────────────
        if dst_mac in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst_mac]

            # Install flow rule for this pair
            msg = of.ofp_flow_mod()
            msg.match.in_port = in_port
            msg.match.dl_src  = pkt.src
            msg.match.dl_dst  = pkt.dst
            msg.priority      = 10
            msg.idle_timeout  = 30
            msg.hard_timeout  = 0
            msg.actions.append(of.ofp_action_output(port=out_port))
            connection.send(msg)

            log.debug("FORWARD: %s --> %s via port %s", src_mac, dst_mac, out_port)
        else:
            out_port = of.OFPP_FLOOD

        # Send packet out
        msg = of.ofp_packet_out()
        msg.data    = event.ofp
        msg.in_port = in_port
        msg.actions.append(of.ofp_action_output(port=out_port))
        connection.send(msg)

    # ── Request flow stats from all switches ──────────────────────────────────
    def _request_stats(self):
        for dpid, conn in self.connections.items():
            log.debug("Requesting stats from switch %s", dpid)
            req = of.ofp_stats_request(body=of.ofp_flow_stats_request())
            conn.send(req)

    # ── Receive and display flow statistics ───────────────────────────────────
    def _handle_FlowStatsReceived(self, event):
        dpid      = dpid_to_str(event.connection.dpid)
        stats     = event.stats
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self.flow_stats[dpid] = stats

        total_packets = 0
        total_bytes   = 0
        active_flows  = 0

        print("\n" + "=" * 70)
        print("  TRAFFIC MONITORING REPORT")
        print(f"  Switch  : {dpid}")
        print(f"  Time    : {timestamp}")
        print(f"  Blocked : {self.blocked_count} packets dropped by firewall")
        print("=" * 70)
        print(f"{'Priority':>8}  {'In-Port':>7}  {'Src IP / MAC':>20}  "
              f"{'Pkts':>8}  {'Bytes':>10}  {'Duration':>10}")
        print("-" * 70)

        for flow in sorted(stats, key=lambda f: f.priority, reverse=True):
            if flow.priority == 0:
                continue

            in_port  = flow.match.in_port or "*"
            nw_src   = str(flow.match.nw_src) if flow.match.nw_src else \
                       str(flow.match.dl_src)  if flow.match.dl_src  else "*"
            pkts     = flow.packet_count
            byt      = flow.byte_count
            duration = flow.duration_sec

            # Label firewall DROP rules
            label = " [DROP]" if flow.priority == 100 else ""

            total_packets += pkts
            total_bytes   += byt
            active_flows  += 1

            print(f"{flow.priority:>8}  {str(in_port):>7}  {nw_src:>20}  "
                  f"{pkts:>8}  {byt:>10}  {duration:>8}s{label}")

        print("-" * 70)
        print(f"  SUMMARY")
        print(f"  Active Flows   : {active_flows}")
        print(f"  Total Packets  : {total_packets}")
        print(f"  Total Bytes    : {total_bytes}")
        print(f"  Packets Blocked: {self.blocked_count}")
        print("=" * 70 + "\n")


def launch():
    core.openflow.addListenerByName(
        "FlowStatsReceived",
        TrafficMonitor()._handle_FlowStatsReceived
    )
    core.registerNew(TrafficMonitor)
    log.info("Traffic Monitor module loaded.")