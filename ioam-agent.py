import sys
import os
import os.path
import getopt
import socket
import ioam_api_pb2
from bitstruct import unpack
from gnmi import IoamAgentExporter

ETH_P_IPV6 = 0x86DD

IPV6_TLV_IOAM = 49
IOAM_PREALLOC_TRACE = 0

RUN_MODE_DEFAULT = 0			# default mode = gNMI
RUN_MODE_OUTPUT = 1

TRACE_TYPE_BIT0_MASK  = 1 << 23	# Hop_Lim + Node Id (short)
TRACE_TYPE_BIT1_MASK  = 1 << 22	# Ingress/Egress Ids (short)
TRACE_TYPE_BIT2_MASK  = 1 << 21	# Timestamp seconds
TRACE_TYPE_BIT3_MASK  = 1 << 20	# Timestamp fraction
TRACE_TYPE_BIT4_MASK  = 1 << 19	# Transit Delay
TRACE_TYPE_BIT5_MASK  = 1 << 18	# Namespace Data (short)
TRACE_TYPE_BIT6_MASK  = 1 << 17	# Queue depth
TRACE_TYPE_BIT7_MASK  = 1 << 16	# Checksum Complement
TRACE_TYPE_BIT8_MASK  = 1 << 15	# Hop_Lim + Node Id (wide)
TRACE_TYPE_BIT9_MASK  = 1 << 14	# Ingress/Egress Ids (wide)
TRACE_TYPE_BIT10_MASK = 1 << 13	# Namespace Data (wide)
TRACE_TYPE_BIT11_MASK = 1 << 12	# Buffer Occupancy
TRACE_TYPE_BIT22_MASK = 1 << 1	# Opaque State Snapshot

def parse_node_data(p, ttype):
	node = ioam_api_pb2.IOAMNode()

	i = 0
	if ttype & TRACE_TYPE_BIT0_MASK:
		node.HopLimit, node.Id = unpack(">u8u24", p[i:i+4])
		i += 4
	if ttype & TRACE_TYPE_BIT1_MASK:
		node.IngressId, node.EgressId = unpack(">u16u16", p[i:i+4])
		i += 4
	if ttype & TRACE_TYPE_BIT2_MASK:
		node.TimestampSecs = unpack(">u32", p[i:i+4])[0]
		i += 4
	if ttype & TRACE_TYPE_BIT3_MASK:
		node.TimestampFrac = unpack(">u32", p[i:i+4])[0]
		i += 4
	if ttype & TRACE_TYPE_BIT4_MASK:
		node.TransitDelay = unpack(">u32", p[i:i+4])[0]
		i += 4
	if ttype & TRACE_TYPE_BIT5_MASK:
		node.NamespaceData = unpack(">r32", p[i:i+4])[0]
		i += 4
	if ttype & TRACE_TYPE_BIT6_MASK:
		node.QueueDepth = unpack(">u32", p[i:i+4])[0]
		i += 4
	if ttype & TRACE_TYPE_BIT7_MASK:
		node.CsumComp = unpack(">u32", p[i:i+4])[0]
		i += 4
	if ttype & TRACE_TYPE_BIT8_MASK:
		node.HopLimit, node.IdWide = unpack(">u8u56", p[i:i+8])
		i += 8
	if ttype & TRACE_TYPE_BIT9_MASK:
		node.IngressIdWide, node.EgressIdWide = unpack(">u32u32", p[i:i+8])
		i += 8
	if ttype & TRACE_TYPE_BIT10_MASK:
		node.NamespaceDataWide = unpack(">r64", p[i:i+8])[0]
		i += 8
	if ttype & TRACE_TYPE_BIT11_MASK:
		node.BufferOccupancy = unpack(">u32", p[i:i+4])[0]
		i += 4

	return node

def parse_ioam_trace(p):
	try:
		ns, nodelen, _, remlen, ttype = unpack(">u16u5u4u7u24", p[:8])

		nodes = []
		i = 8 + remlen * 4

		while i < len(p):
			node = parse_node_data(p[i:i+nodelen*4], ttype)
			i += nodelen * 4

			if ttype & TRACE_TYPE_BIT22_MASK:
				opaque_len, node.OSS.SchemaId = unpack(">u8u24",
									p[i:i+4])
				if opaque_len > 0:
					node.OSS.Data = p[i+4:i+4+opaque_len*4]

				i += 4 + opaque_len * 4

			nodes.insert(0, node)

		trace = ioam_api_pb2.IOAMTrace()
		trace.BitField = ttype << 8
		trace.NamespaceId = ns
		trace.Nodes.extend(nodes)

		return trace
	except:
		return None

def parse(p):
	try:
		nextHdr = p[6]
		if nextHdr != socket.IPPROTO_HOPOPTS:
			return None

		hbh_len = (p[41] + 1) << 3
		i = 42

		traces = []
		while hbh_len > 0:
			opt_type, opt_len = unpack(">u8u8", p[i:i+2])
			opt_len += 2

			if (opt_type == IPV6_TLV_IOAM and
			    p[i+3] == IOAM_PREALLOC_TRACE):

				trace = parse_ioam_trace(p[i+4:i+opt_len])
				if trace is not None:
					traces.append(trace)

			i += opt_len
			hbh_len -= opt_len

		return traces
	except:
		return None

def report_ioam(func, traces):
	try:
		for trace in traces:
			func(trace)
	except:
		pass

def listen(interface, mode):
	try:
		sock, func = None, None

		sock = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM,
				     socket.htons(ETH_P_IPV6))

		sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
				interface.encode())

		if mode == RUN_MODE_OUTPUT:
			func = print
			print("[IOAM Agent] Printing IOAM traces...")
		elif mode == RUN_MODE_DEFAULT:
			exporter = IoamAgentExporter()
			func = exporter.input_ioam_record
			exporter.run()
			print("[IOAM Agent] Waiting for gNMI inbound requests...")

		while True:
			traces = parse(sock.recv(2048))
			if traces is not None and len(traces) > 0:
				report_ioam(func, traces)
	except KeyboardInterrupt:
		print("[IOAM Agent] Closing...")
	except Exception as e:
		print("[IOAM Agent] Closing on unexpected error: "+ str(e))
	finally:
		if sock is not None:
			sock.close()

def help():
	print("Syntax: "+ os.path.basename(__file__) +" -i <interface> [-o]")

def help_str(err):
	print(err)
	help()

def interface_exists(interface):
	try:
		socket.if_nametoindex(interface)
		return True
	except OSError:
		return False

def main(script, argv):
	try:
		opts, args = getopt.getopt(argv, "hi:o",
					   ["help", "interface=", "output"])
	except getopt.GetoptError:
		help()
		sys.exit(1)

	interface = ""
	mode = RUN_MODE_DEFAULT

	for opt, arg in opts:
		if opt in ("-h", "--help"):
			help()
			sys.exit()

		if opt in ("-i", "--interface"):
			interface = arg

		if opt in ("-o", "--output"):
			mode = RUN_MODE_OUTPUT

	if not interface_exists(interface):
		help_str("Unknown interface "+ interface)
		sys.exit(1)

	listen(interface, mode)

if __name__ == "__main__":
	main(sys.argv[0], sys.argv[1:])

