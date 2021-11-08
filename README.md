# IOAM Agent for Python3

1) Install dependencies:
```
sudo pip3 install --upgrade bitstruct grpcio grpcio-tools protobuf
```

Note: `sudo` is required since the IOAM agent requires root privileges.

2) Compile the [IOAM API](https://github.com/Advanced-Observability/ioam-api):
```[bash]
git clone https://github.com/Advanced-Observability/ioam-api.git
python3 -m grpc_tools.protoc -Iioam-api/ --python_out=. --grpc_python_out=. ioam-api/ioam_api.proto
```

3) Run it:
```[bash]
sudo python3 ioam-agent.py -i <interface> [-o]
```

### Output Mode

Use `-o`.

This mode will print IOAM traces in the console.

### Report Mode

Default mode.

This mode will report IOAM traces to a collector via grpc. **The collector must be defined as an environment variable**:
```[bash]
IOAM_COLLECTOR=address:port
```
