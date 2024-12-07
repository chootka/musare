from flask import Flask, jsonify, Response, render_template, request
import socket
import json
from datetime import datetime
from queue import Queue
import threading
import time

app = Flask(__name__)
message_queue = Queue()
ft8_packets = []
js8_packets = []
JS8_PORT = 2242
FT8_PORT = 2237

def load_sample_data():
    try:
        with open('data/sample_packets.json', 'r') as f:
            data = json.load(f)
            print(f"Loaded {len(data['packets'])} sample packets")
            for packet in data['packets']:
                latest_packets.append(packet)
                message_queue.put(packet)
                time.sleep(0.5)
            print("Finished loading sample data")
    except Exception as e:
        print(f"Error loading sample data: {e}")

def listen_for_packets():
    try:
        print("Starting listeners...")
        ft8_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        js8_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        ft8_sock.bind(('0.0.0.0', FT8_PORT))
        js8_sock.bind(('0.0.0.0', JS8_PORT))
        print("Connected to UDP servers successfully")
        
        while True:
            try:
                # FT8 packets
                ft8_data = ft8_sock.recv(1024)
                timestamp = datetime.now().timestamp()
                ft8_packet = {
                    'type': 'ft8',
                    'timestamp': timestamp,
                    'raw_data': [b for b in ft8_data],
                    'size': len(ft8_data)
                }
                print(f"FT8 Packet data: {ft8_packet}")
                ft8_packets.append(ft8_packet)
                if len(ft8_packets) > 100:
                    ft8_packets.pop(0)
                message_queue.put(ft8_packet)
                
                # JS8 packets
                js8_data = js8_sock.recv(1024)
                timestamp = datetime.now().timestamp()
                js8_packet = {
                    'type': 'js8',
                    'timestamp': timestamp,
                    'raw_data': [b for b in js8_data],
                    'size': len(js8_data)
                }
                print(f"JS8 Packet data: {js8_packet}")
                js8_packets.append(js8_packet)
                if len(js8_packets) > 100:
                    js8_packets.pop(0)
                message_queue.put(js8_packet)
                
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
