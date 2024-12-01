from flask import Flask, jsonify, Response, render_template
import socket
import json
from datetime import datetime
from queue import Queue
import threading

app = Flask(__name__)
message_queue = Queue()
latest_packets = []  # Store recent packets for new clients

def listen_for_wsjtx():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_address = ('127.0.0.1', 2237)
    sock.connect(server_address)
    
    while True:
        try:
            data = sock.recv(1024)
            timestamp = datetime.now().timestamp()
            packet_data = {
                'timestamp': timestamp,
                'raw_data': [b for b in data],  # Convert bytes to list of integers
                'size': len(data)
            }
            latest_packets.append(packet_data)
            if len(latest_packets) > 100:  # Keep last 100 packets
                latest_packets.pop(0)
            message_queue.put(packet_data)
        except Exception as e:
            print(f"Error: {e}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/stream')
def stream():
    def generate():
        while True:
            packet = message_queue.get()
            yield f"data: {json.dumps(packet)}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')

if __name__ == '__main__':
    listener_thread = threading.Thread(target=listen_for_wsjtx)
    listener_thread.daemon = True
    listener_thread.start()
    app.run(debug=True)
