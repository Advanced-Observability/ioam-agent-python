import getopt
import grpc
import ioam_api_pb2
import ioam_api_pb2_grpc
import os
import os.path
import sys

from bitstruct import unpack
from enum import Enum
from pyroute2.netlink import genlmsg
from pyroute2.netlink.event import EventSocket
from pyroute2.netlink.nlsocket import Marshal

IOAM6_GENL_NAME = 'IOAM6'

class IoamGenlEvent(Enum):
	IOAM6_EVENT_UNSPEC = 0
	IOAM6_EVENT_TRACE = 1

class ioam_msg(genlmsg):
	nla_map = (
		('IOAM6_EVENT_ATTR_UNSPEC', 'none'),
		('IOAM6_EVENT_ATTR_TRACE_NAMESPACE', 'uint16'),
		('IOAM6_EVENT_ATTR_TRACE_NODELEN', 'uint8'),
		('IOAM6_EVENT_ATTR_TRACE_TYPE', 'uint32'),
		('IOAM6_EVENT_ATTR_TRACE_DATA', 'cdata'),
	)

class MarshalIoamEvent(Marshal):
	msg_map = { x.value: ioam_msg for x in IoamGenlEvent }

class IoamEventSocket(EventSocket):
	marshal_class = MarshalIoamEvent
	genl_family = IOAM6_GENL_NAME

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

def parse_trace_node(p, ttype):
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

def parse_ioam_trace(event):
	try:
		namespace_id = event.get('IOAM6_EVENT_ATTR_TRACE_NAMESPACE')
		node_len = event.get('IOAM6_EVENT_ATTR_TRACE_NODELEN')
		ttype = event.get('IOAM6_EVENT_ATTR_TRACE_TYPE') >> 8
		tdata = event.get('IOAM6_EVENT_ATTR_TRACE_DATA')

		nodes = []

		i = 0
		while i < len(tdata):
			node = parse_trace_node(tdata[i:i+node_len*4], ttype)
			i += node_len*4

			if ttype & TRACE_TYPE_BIT22_MASK:
				oss_len, node.OSS.SchemaId = unpack(
					">u8u24", tdata[i:i+4])

				if oss_len > 0:
					node.OSS.Data = tdata[i+4:i+4+oss_len*4]

				i += 4+oss_len*4

			nodes.insert(0, node)

		trace = ioam_api_pb2.IOAMTrace()
		trace.BitField = ttype
		trace.NamespaceId = namespace_id
		trace.Nodes.extend(nodes)

		return trace
	except:
		return None

def report_event(func, event):
	try:
		if event.get('cmd') == IoamGenlEvent.IOAM6_EVENT_TRACE.value:
			parsed_event = parse_ioam_trace(event)
		else:
			parsed_event = None

		if parsed_event is not None:
			func(parsed_event)
	except grpc.RpcError as e:
		# TODO IOAM collector is probably not online
		pass

def receive_ioam_events(collector):
	stub, func = None, None

	if collector is None:
		func = print
		print("[IOAM Agent] Printing IOAM events...")
	else:
		channel = grpc.insecure_channel(collector)
		stub = ioam_api_pb2_grpc.IOAMServiceStub(channel)
		func = stub.Report
		print("[IOAM Agent] Reporting to the IOAM collector...")

	ioam_events = IoamEventSocket()

	while True:
		try:
			for event in ioam_events.get():
				report_event(func, event)
		except KeyboardInterrupt:
			print("[IOAM Agent] Closing...")
			break
		except OSError as e:
			if e.args[0] == 105: #no buffer space available
				print(str(e))
				continue
			print("[IOAM Agent] Closing on error: "+ str(e))
			break
		except Exception as e:
			print("[IOAM Agent] Closing on error: "+ str(e))
			break

	if stub is not None:
		channel.close()

def help():
	print("Syntax: "+ os.path.basename(__file__) +" [-o]")

def main(script, argv):
	try:
		opts, args = getopt.getopt(argv, "ho", ["help", "output"])
	except getopt.GetoptError:
		help()
		sys.exit(1)

	output = False

	for opt, arg in opts:
		if opt in ("-h", "--help"):
			help()
			sys.exit()

		if opt in ("-o", "--output"):
			output = True

	try:
		collector = os.environ['IOAM_COLLECTOR'] if not output else None
		receive_ioam_events(collector)
	except KeyError:
		print("IOAM collector is not defined")
		sys.exit(1)

if __name__ == "__main__":
	main(sys.argv[0], sys.argv[1:])

