import asyncio
import websockets
import time
import os
import json
import base64

async def test_stream():
    url = "ws://127.0.0.1:9001/test-conn-1234"
    async with websockets.connect(url) as ws:
        print("Connected to", url)
        # Send a metadata text frame
        await ws.send(json.dumps({"event": "connected"}))
        
        # Send 100 binary frames (2 seconds at 50fps)
        print("Sending binary frames...")
        for i in range(100):
            # 160 bytes of dummy pcmu audio per frame (20ms at 8000Hz)
            frame = b'\xff' * 160
            await ws.send(frame)
            await asyncio.sleep(0.020) # 20ms pacing
            
        print("Sending JSON base64 frame...")
        # Send JSON base64 frame
        frame2 = b'\x00' * 160
        b64_audio = base64.b64encode(frame2).decode('ascii')
        await ws.send(json.dumps({"audio": {"data": b64_audio}}))
        
        await asyncio.sleep(0.5)
        print("Done.")

if __name__ == "__main__":
    asyncio.run(test_stream())
