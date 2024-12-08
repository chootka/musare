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

def clean_text(text):
    if not text:
        return text
        
    # Replace common problematic characters
    replacements = {
        '~': '',      # Remove tilde
        '\x00': '',   # Remove null bytes
        '\xff': '',   # Remove xFF bytes
        '\xc2': '',   # Remove common UTF-8 artifacts
        '\xa0': ' ',  # Replace non-breaking space with regular space
    }
    
    # Clean the text
    for old, new in replacements.items():
        text = text.replace(old, new)
    
    # Remove any remaining non-printable characters
    text = ''.join(char for char in text if char.isprintable() or char.isspace())
    
    # Remove multiple spaces
    text = ' '.join(text.split())
    
    return text.strip()

def decode_wsjtx_message(data):
    try:
        # Unpack the header using three unsigned ints
        magic, schema, pkt_type = struct.unpack('>III', data[:12])

        print(f"Magic: {magic}, Schema: {schema}, Pkt Type: {pkt_type}")
        
        if magic != FT8_MAGIC:
            print(f"Invalid magic number: {magic}")
            return None

        # Handle heartbeat packet (type 0)
        if pkt_type == 0:
            offset = 12
            # Get ID string length
            id_len = struct.unpack('>I', data[offset:offset+4])[0]
            offset += 4
            # Get ID string
            id_str = clean_text(data[offset:offset+id_len].decode('utf-8', errors='replace')) if id_len > 0 else None
            # Get maximum schema number
            offset += id_len
            max_schema = struct.unpack('>I', data[offset:offset+4])[0]
            # Get version string length 
            offset += 4
            version_len = struct.unpack('>I', data[offset:offset+4])[0]
            offset += 4
            # Get version string
            version = clean_text(data[offset:offset+version_len].decode('utf-8', errors='replace')) if version_len > 0 else None
            
            return {
                'callsign': id_str,
                'raw_decode': f"Heartbeat from {version}",
                'pkt_type': pkt_type
            }

        # Handle status packet (type 1)
        elif pkt_type == 1:
            offset = 12
            # Skip the ID field (first string)
            id_len = struct.unpack('>I', data[offset:offset+4])[0]
            offset += 4 + id_len
            # Get dial frequency
            dial_freq = struct.unpack('>Q', data[offset:offset+8])[0]
            offset += 8
            # Get mode
            mode_len = struct.unpack('>I', data[offset:offset+4])[0]
            offset += 4
            mode = clean_text(data[offset:offset+mode_len].decode('utf-8', errors='replace'))
            offset += mode_len
            # Get DX call
            dx_call_len = struct.unpack('>I', data[offset:offset+4])[0]
            offset += 4
            dx_call = clean_text(data[offset:offset+dx_call_len].decode('utf-8', errors='replace')) if dx_call_len > 0 else None
            
            return {
                'callsign': dx_call,
                'raw_decode': f"Status: {mode} {dial_freq/1e6:.3f}MHz",
                'pkt_type': pkt_type
            }
            
        # Handle decoded packet (type 2)
        elif pkt_type == 2:
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
            message = clean_text(data[offset:offset+msg_len].decode('utf-8', errors='replace'))
            
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
                'raw_decode': message,
                'pkt_type': pkt_type
            }
        else:
            print(f"Unknown packet type: {pkt_type}")
            return None
            
    except Exception as e:
        print(f"Error decoding WSJT-X message: {e}")
        print(f"Raw data: {[hex(b) for b in data]}")  # Add hex dump for debugging
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
            message = clean_text(data[offset:offset+msg_len].decode('utf-8', errors='replace'))
            
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
        
        # Set non-blocking mode
        wsjtx_sock.setblocking(False)
        js8_sock.setblocking(False)
        
        wsjtx_sock.bind(('0.0.0.0', WSJTX_PORT))
        js8_sock.bind(('0.0.0.0', JS8_PORT))
        print("Connected to UDP servers successfully on ports {} and {}".format(WSJTX_PORT, JS8_PORT))
        
        while True:
            # Check FT8
            try:
                data, addr = wsjtx_sock.recvfrom(1024)
                print(f"Received FT8 UDP data from {addr}: {len(data)} bytes")
                print(f"Raw data: {[hex(b) for b in data]}")
                
                # First decode the message
                decoded = decode_wsjtx_message(data)
                
                # Then create the raw packet using the decoded result
                timestamp = datetime.now().timestamp()
                raw_packet = {
                    'type': 'ft8',
                    'timestamp': timestamp,
                    'raw_data': [b for b in data],
                    'size': len(data),
                    'callsign': 'UNKNOWN',
                    'decoded': 'Raw Packet',
                    'pkt_type': decoded.get('pkt_type') if decoded else None
                }
                
                # Update packet with decoded information if available
                if decoded:
                    raw_packet['callsign'] = decoded['callsign']
                    raw_packet['decoded'] = decoded['raw_decode']
                
                ft8_packets.append(raw_packet)
                if len(ft8_packets) > 100:
                    ft8_packets.pop(0)
                message_queue.put(raw_packet)
            except BlockingIOError:
                pass  # No FT8 data available
            except Exception as e:
                print(f"Error receiving FT8 data: {e}")
            
            # Check JS8
            try:
                data, addr = js8_sock.recvfrom(1024)
                print(f"Received JS8 UDP data from {addr}: {len(data)} bytes")
                print(f"Raw JS8 data: {[hex(b) for b in data]}")
                
                timestamp = datetime.now().timestamp()
                raw_packet = {
                    'type': 'js8',
                    'timestamp': timestamp,
                    'raw_data': [b for b in data],
                    'size': len(data),
                    'callsign': 'UNKNOWN',
                    'decoded': 'Raw Packet'
                }
                
                decoded = decode_js8_message(data)
                if decoded:
                    raw_packet['callsign'] = decoded['callsign']
                    raw_packet['decoded'] = decoded['raw_decode']
                
                js8_packets.append(raw_packet)
                if len(js8_packets) > 100:
                    js8_packets.pop(0)
                message_queue.put(raw_packet)
            except BlockingIOError:
                pass  # No JS8 data available
            except Exception as e:
                print(f"Error receiving JS8 data: {e}")
            
            # Small sleep to prevent CPU spinning
            time.sleep(0.001)
                
    except Exception as e:
        print(f"Could not set up UDP listeners: {e}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/stream')
def stream():
    mode = request.args.get('mode', 'ft8')
    print(f"New client connected, requesting mode: {mode}")
    
    def generate():
        while True:
            packet = message_queue.get()
            print(f"Sending packet to client: {packet}")
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
