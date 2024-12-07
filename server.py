from flask import Flask, jsonify, Response, render_template, request
import socket
import json
from datetime import datetime
from queue import Queue
import threading
import time
import struct

app = Flask(__name__)
message_queue = Queue()
ft8_packets = []
js8_packets = []
JS8_PORT = 2242
WSJTX_PORT = 2237  # Default WSJT-X UDP port
FT8_MAGIC = 0xadbccbda  # WSJT-X magic number
JS8_MAGIC = 0x2a4d5347  # JS8Call magic number
SCHEMA = 2  # Current schema version

def load_sample_data():
    try:
        with open('data/sample_packets.json', 'r') as f:
            data = json.load(f)
            print(f"Loaded {len(data['packets'])} sample packets")
            for packet in data['packets']:
                message_queue.put(packet)
                time.sleep(0.5)
            print("Finished loading sample data")
    except Exception as e:
        print(f"Error loading sample data: {e}")

def decode_wsjtx_message(data):
    try:
        # Unpack the header
        magic, schema, pkt_type = struct.unpack('>IIL', data[:12])

        print(f"Magic: {magic}, Schema: {schema}, Pkt Type: {pkt_type}")
        
        if magic != FT8_MAGIC:
            return None
            
        if pkt_type == 2:  # Decode packet
            # Extract the decoded text from the message
            # Format varies by schema version, this is for schema 2
            offset = 12
            # Skip the ID field (first string)
            id_len = struct.unpack('>I', data[offset:offset+4])[0]
            offset += 4 + id_len
            # Skip new field
            new = struct.unpack('>B', data[offset:offset+1])[0]
            offset += 1
            # Skip time field
            time = struct.unpack('>I', data[offset:offset+4])[0]
            offset += 4
            # Skip snr field
            snr = struct.unpack('>h', data[offset:offset+2])[0]
            offset += 2
            # Skip delta time field
            delta_time = struct.unpack('>f', data[offset:offset+4])[0]
            offset += 4
            # Skip delta frequency field
            delta_freq = struct.unpack('>I', data[offset:offset+4])[0]
            offset += 4
            # Skip mode field
            mode_len = struct.unpack('>I', data[offset:offset+4])[0]
            offset += 4 + mode_len
            # Get message
            msg_len = struct.unpack('>I', data[offset:offset+4])[0]
            offset += 4
            message = data[offset:offset+msg_len].decode('utf-8')
            
            # Try to extract callsign from the message
            # FT8 messages typically follow patterns like "CQ K1ABC" or "K1ABC K2XYZ"
            parts = message.split()
            callsign = None
            if parts and parts[0] == "CQ":
                callsign = parts[1] if len(parts) > 1 else None
            else:
                callsign = parts[0] if parts else None

            print(f"Decoded message: {message}, Callsign: {callsign}")
                
            return {
                'callsign': callsign,
                'raw_decode': message
            }
            
    except Exception as e:
        print(f"Error decoding WSJT-X message: {e}")
        return None

def decode_js8_message(data):
    try:
        # Unpack the header
        magic, schema, pkt_type = struct.unpack('>IIL', data[:12])
        
        if magic != JS8_MAGIC:
            return None
            
        if pkt_type == 2:  # Decode packet
            offset = 12
            # Get message length
            msg_len = struct.unpack('>I', data[offset:offset+4])[0]
            offset += 4
            # Get the actual message
            message = data[offset:offset+msg_len].decode('utf-8')
            
            # JS8 messages often have format "@CALLSIGN: message" or "CALLSIGN: message"
            parts = message.split(':')[0].strip('@').strip()
            callsign = parts if parts else None
                
            return {
                'callsign': callsign,
                'raw_decode': message
            }
            
    except Exception as e:
        print(f"Error decoding JS8Call message: {e}")
        return None

def listen_for_packets():
    try:
        print("Starting UDP listeners...")
        wsjtx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        js8_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        wsjtx_sock.bind(('0.0.0.0', WSJTX_PORT))
        js8_sock.bind(('0.0.0.0', JS8_PORT))
        print("Connected to UDP servers successfully")
        
        while True:
            try:
                # FT8 packets
                data, addr = wsjtx_sock.recvfrom(1024)
                timestamp = datetime.now().timestamp()

                print(f"Received FT8 data: {data}")
                
                decoded = decode_wsjtx_message(data)
                if decoded:
                    packet = {
                        'type': 'ft8',
                        'timestamp': timestamp,
                        'raw_data': [b for b in data],
                        'size': len(data),
                        'callsign': decoded['callsign'],
                        'decoded': decoded['raw_decode']
                    }
                    print(f"Received FT8 packet: {packet}")
                    ft8_packets.append(packet)
                    if len(ft8_packets) > 100:
                        ft8_packets.pop(0)
                    message_queue.put(packet)
                
                # JS8 packets
                data, addr = js8_sock.recvfrom(1024)
                timestamp = datetime.now().timestamp()
                
                decoded = decode_js8_message(data)
                if decoded:
                    packet = {
                        'type': 'js8',
                        'timestamp': timestamp,
                        'raw_data': [b for b in data],
                        'size': len(data),
                        'callsign': decoded['callsign'],
                        'decoded': decoded['raw_decode']
                    }
                    js8_packets.append(packet)
                    if len(js8_packets) > 100:
                        js8_packets.pop(0)
                    message_queue.put(packet)
                    
            except Exception as e:
                print(f"Error receiving data: {e}")
                time.sleep(1)
                
    except Exception as e:
        print(f"Could not set up UDP listeners: {e}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/stream')
def stream():
    mode = request.args.get('mode', 'ft8')
    
    def generate():
        while True:
            packet = message_queue.get()
            if packet['type'] == mode:
                yield f"data: {json.dumps(packet)}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')

@app.route('/ft8')
def get_ft8_packets():
    return jsonify(ft8_packets)

@app.route('/js8')
def get_js8_packets():
    return jsonify(js8_packets)

if __name__ == '__main__':
    print("Starting server...")
    listener_thread = threading.Thread(target=listen_for_packets)
    listener_thread.daemon = True
    listener_thread.start()
    app.run(debug=True, use_reloader=False)
