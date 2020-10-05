"""
gnmi.py

   gNMI exporter ioam-agent

@author: K.Edeline
"""

import re
import time
import json
import grpc
from concurrent import futures
from google.protobuf import json_format
from cisco_gnmi.proto import gnmi_pb2, gnmi_pb2_grpc
from cisco_gnmi.proto.gnmi_pb2_grpc import gNMIServicer
from queue import Queue

def list_from_path(path='/'):
   if path:
      if path[0]=='/':
         if path[-1]=='/':
            return re.split('''/(?=(?:[^\[\]]|\[[^\[\]]+\])*$)''', path)[1:-1]
         else:
            return re.split('''/(?=(?:[^\[\]]|\[[^\[\]]+\])*$)''', path)[1:]
      else:
         if path[-1]=='/':
            return re.split('''/(?=(?:[^\[\]]|\[[^\[\]]+\])*$)''', path)[:-1]
         else:
            return re.split('''/(?=(?:[^\[\]]|\[[^\[\]]+\])*$)''', path)
   return []

def path_from_string(path='/'):
   mypath = []
   for e in list_from_path(path):
      eName = e.split("[", 1)[0]
      eKeys = re.findall('\[(.*?)\]', e)
      dKeys = dict(x.split('=', 1) for x in eKeys)
      mypath.append(gnmi_pb2.PathElem(name=eName, key=dKeys))
   return gnmi_pb2.Path(elem=mypath)

def test_callback():
   print("CALBACKCALBAKCK")

class IoamAgentServicer(gNMIServicer):
   def __init__(self, exporter, queue):
      super(IoamAgentServicer, self).__init__()
      self.exporter = exporter
      self.queue = queue
      self.on_change = False

   def _capabilitiesResponse(self):
      response = gnmi_pb2.CapabilityResponse()
      supModel = gnmi_pb2.ModelData(name="draft-zhou-ippm-ioam-yang",
                  organization="IETF", version="1.0")
                  
      response.supported_models.extend([supModel])
      response.gNMI_version = "0.7.0"
       
      return response
    
   def _getResponse(self, paths):
      response = gnmi_pb2.GetResponse()
      return response
      
   def _validate_subscriptions(self, request):
      """
      validate and return string-converted paths
      
      
      """
      paths, sample_intervals = [], []
      if "subscribe" not in request or "subscription" not in request["subscribe"]:
         return paths
      subscriptions = request["subscribe"]["subscription"]
      for subscription in subscriptions:
         path_str = ""
         path_elements = subscription["path"]["elem"]
         for name in path_elements:
            if "name" in name:
               path_str += "/{}".format(name["name"])
            else:
               path_str += "/"
         paths.append(path_str)
         if subscription["mode"] == "ON_CHANGE":
            self.on_change=True
            sample_intervals.append(0)
         elif subscription["mode"] == "SAMPLE":
            sample_intervals.append(subscription["sampleInterval"])
         
         #self.exporter.on_change = True
      return paths, int(sample_intervals[0])/1e9
      
   def _subscribeResponse(self, paths):
      """
      build SubscribeResponse
      
      """
      response = gnmi_pb2.SubscribeResponse()
      response.sync_response = True
      
      for path_string, val, _type in self.exporter._iterate_data(paths):
         path = path_from_string(path_string)
         # add an update message for path
         added = response.update.update.add()
         added.path.CopyFrom(path)
         if _type == int:
            added.val.int_val = val
         elif _type == str:
            added.val.string_val = val
         elif _type == float:
            added.val.float_val = val
         elif _type == "json": # grpc will base64 encode
            added.val.json_val = val.encode("utf-8")
      response.update.timestamp = time.time_ns()
      return response
      
   def _subscribeResponse_onChange(self, trace):
      """
      build SubscribeResponse
      
      """
      response = gnmi_pb2.SubscribeResponse()
      #response.sync_response = True
      
      for path_string, val, _type in self.exporter._parse_ioam_record(trace):
         path = path_from_string(path_string)
         # add an update message for path
         added = response.update.update.add()
         added.path.CopyFrom(path)
         if _type == int:
            added.val.int_val = val
         elif _type == str:
            added.val.string_val = val
         elif _type == float:
            added.val.float_val = val
         elif _type == "json": # grpc will base64 encode
            added.val.json_val = val.encode("utf-8")
      response.update.timestamp = time.time_ns()
      return response
           
   # gNMI Services Capabilities Routine
   def Capabilities(self, request, context):
      return self._capabilitiesResponse()
      
   # gNMI Services Get Routine
   def Get(self, request, context):
      return self._getResponse(request)
      
   # gNMI Services Subscribe Routine
   def Subscribe(self, requests, context):
      # register callback to avoid useless queue.put()
      if self.on_change:
         context.add_callback(self.exporter.onchange_servicer_has_closed)
      
      for request in requests:
         request_json = json.loads(json_format.MessageToJson(request))
         paths,sample_interval = self._validate_subscriptions(request_json)

         if self.on_change:
            self.exporter.on_change = True
            while True:
               trace = self.queue.get()
               response = self._subscribeResponse_onChange(trace)
               yield response              
               self.queue.task_done()
         else:
            while True:
               response = self._subscribeResponse(paths)
               yield response
               time.sleep(sample_interval)
   
class IoamAgentExporter():
   def __init__(self,
                target_url="0.0.0.0:50051",
                certs_dir="certs/",
                tls_enabled=True):
      self.data = {}
      self.queue = Queue()
      self.on_change = False
      self.target_url = target_url
      
      pkeypath = certs_dir+"/device.key"
      certpath = certs_dir+"/device.crt"
      
      self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
      gnmi_pb2_grpc.add_gNMIServicer_to_server(
           IoamAgentServicer(self, self.queue), self._server)
      if tls_enabled:
         with open(pkeypath, 'rb') as f:
            privateKey = f.read()
         with open(certpath, 'rb') as f:
            certChain = f.read()
         credentials = grpc.ssl_server_credentials(((privateKey, certChain, ), ))
         self._server.add_secure_port(self.target_url, credentials)         
      else:
         self._server.add_insecure_port(self.target_url)
       
   def onchange_servicer_has_closed(self):
      self.on_change=False
      self.queue.queue.clear()
       
   def run(self, wait=False):
      if not self.target_url:
         return
      self._server.start()
      if wait:
         self._server.wait_for_termination()      

   def input_ioam_record(self, trace):
      """
      store last trace to dict using (namespace, Node.Id) as index
      
      """
      if self.on_change:
         self.queue.put(trace)
      else:
         namespace = trace.NamespaceId
         node_id = trace.Nodes[0].Id
         full_id = (namespace, node_id)
         self.data[full_id] = trace

   def _parse_ioam_record(self, trace):
      namespace, node_id = trace.NamespaceId, trace.Nodes[0].Id
      namespace_prefix = "/ioam[id={}]".format(namespace)
      
      for node in trace.Nodes:
         node_prefix = namespace_prefix+"/Node[id={}]".format(node_id)
         
         yield namespace_prefix+"/BitField", trace.BitField, int
         yield node_prefix+"/HopLimit",      node.HopLimit, int
         yield node_prefix+"/IngressId",     node.IngressId, int
         yield node_prefix+"/EgressId",      node.EgressId, int
         yield node_prefix+"/Timestamp",     node.Timestamp, int
         yield node_prefix+"/TimestampSub",  node.TimestampSub, int

   def _iterate_data(self, subscribed):
      """

      @param subscribed the list of subscribed paths
             / 
             
            NamespaceId: 123
            BitField: 4227858432
            Nodes {
              HopLimit: 63
              Id: 1
              IngressId: 65535
              EgressId: 11
              Timestamp: 1601895709
              TimestampSub: 60552
            }
      """
      if "/" in subscribed:
         for k,d in self.data.items():
            yield from self._parse_ioam_record(d)


