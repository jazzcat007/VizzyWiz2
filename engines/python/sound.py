from multiprocessing import Process, Array, Value, Lock
from ctypes import c_float
import alsaaudio
import struct
import pygame
import time
import numpy as np

BUFFER_SIZE = 100  # Size of the circular buffer

def audio_processing(shared_buffer, shared_buffer_r, write_index, gain, peak, peak_r, lock):
    def find_input_device():
        """Find the first available input device"""
        cards = alsaaudio.cards()
        for card in cards:
            devices = alsaaudio.pcms(card=card)
            for device in devices:
                if 'capture' in device.lower():
                    return (card, device)
        raise RuntimeError("No suitable input device found")

    try:
        # Find and use the first available input device
        card, device = find_input_device()
        print(f"Using audio device: {device} on card {card}")
        
        # PCM configuration
        channels = 2  # Stereo input
        rate = 44100
        format = alsaaudio.PCM_FORMAT_S16_LE
        period_size = 32
        
        inp = alsaaudio.PCM(alsaaudio.PCM_CAPTURE, alsaaudio.PCM_NORMAL,
                           device=device,
                           channels=channels,
                           rate=rate,
                           format=format,
                           periodsize=period_size)
        
        print(f"Audio input opened: {inp.dumpinfo()}")
        
        # Smoothing variables
        smooth_window = 8
        samples_l = np.zeros(smooth_window)
        samples_r = np.zeros(smooth_window)
        ptr = 0
        
        while True:
            length, data = inp.read()
            
            if length > 0:
                # Convert bytes to numpy array of int16 samples
                samples = np.frombuffer(data, dtype='<i2')
                
                # Deinterleave stereo channels
                samples_l_new = samples[0::2]
                samples_r_new = samples[1::2]
                
                # Process samples in chunks
                for i in range(0, len(samples_l_new), smooth_window):
                    chunk_l = samples_l_new[i:i+smooth_window]
                    chunk_r = samples_r_new[i:i+smooth_window]
                    
                    if len(chunk_l) == smooth_window:
                        # Apply gain and convert to float
                        chunk_l = chunk_l * gain.value
                        chunk_r = chunk_r * gain.value
                        
                        # Calculate RMS (root mean square) for the chunk
                        rms_l = np.sqrt(np.mean(chunk_l**2))
                        rms_r = np.sqrt(np.mean(chunk_r**2))
                        
                        # Update circular buffer
                        with lock:
                            shared_buffer[write_index.value] = rms_l
                            shared_buffer_r[write_index.value] = rms_r
                            write_index.value = (write_index.value + 1) % BUFFER_SIZE
                            
                            # Update peak values
                            peak.value = max(peak.value, rms_l)
                            peak_r.value = max(peak_r.value, rms_r)
                            
    except Exception as e:
        print(f"Audio processing error: {str(e)}")
    finally:
        if 'inp' in locals():
            inp.close()
