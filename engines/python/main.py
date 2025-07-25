#!/usr/bin/env python3
import os
import sys
import time
import signal
import traceback
import logging
import gc
from multiprocessing import Process, Array, Value, Lock
from ctypes import c_float
import math
import psutil

import pygame
from pygame.locals import *
import midi
import eyesy
from pythonosc import osc_server, dispatcher, udp_client
from pythonosc.osc_message_builder import OscMessageBuilder

import sound
import osd
import usbdrive
from screen_main_menu import ScreenMainMenu
from screen_test import ScreenTest
from screen_video_settings import ScreenVideoSettings
from screen_palette import ScreenPalette
from screen_wifi import ScreenWiFi
from screen_applogs import ScreenApplogs
from screen_midi_settings import ScreenMIDISettings
from screen_midi_pc_mapping import ScreenMIDIPCMapping
from screen_flash_drive import ScreenFlashDrive

# Configure logging
logging.basicConfig(
    filename=os.path.join(os.path.dirname(__file__), 'eyesy.log'),
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('eyesy')

class OSCManager:
    def __init__(self, eyesy):
        self.eyesy = eyesy
        self.dispatcher = dispatcher.Dispatcher()
        self.server = None
        self.client = None
        self.running = False
        
    def init(self):
        """Initialize OSC server and client"""
        try:
            self.dispatcher.map("/knob/*", self.handle_knob)
            self.dispatcher.map("/led", self.handle_led)
            self.dispatcher.map("/mode", self.handle_mode)
            
            self.server = osc_server.ThreadingOSCUDPServer(
                ("0.0.0.0", 12345), self.dispatcher)
            self.client = udp_client.SimpleUDPClient("127.0.0.1", 12346)
            
            import threading
            self.server_thread = threading.Thread(target=self.server.serve_forever)
            self.server_thread.daemon = True
            self.running = True
            self.server_thread.start()
            logger.info("OSC initialized successfully")
            return True
        except Exception as e:
            logger.error(f"OSC init failed: {str(e)}")
            return False
    
    def handle_knob(self, address, *args):
        try:
            knob_num = int(address.split('/')[-1])
            if 0 <= knob_num < len(self.eyesy.knobs):
                self.eyesy.knobs[knob_num] = args[0]
        except (IndexError, ValueError) as e:
            logger.warning(f"Invalid knob message: {address} {args}")

    def handle_led(self, address, value):
        try:
            self.eyesy.led = int(value)
            self.eyesy.new_led = True
        except ValueError:
            logger.warning(f"Invalid LED value: {value}")

    def handle_mode(self, address, value):
        try:
            self.eyesy.set_mode_by_index(int(value))
        except ValueError:
            logger.warning(f"Invalid mode index: {value}")

    def send(self, address, value):
        if self.client:
            try:
                self.client.send_message(address, value)
            except Exception as e:
                logger.error(f"OSC send failed: {str(e)}")

    def close(self):
        self.running = False
        if self.server:
            self.server.shutdown()
            self.server_thread.join(timeout=1.0)
        logger.info("OSC closed")

def handle_sigterm(signum, frame):
    """Handle graceful shutdown on SIGTERM"""
    logger.info("Received SIGTERM, shutting down")
    exitexit(0)

def exitexit(code):
    """Cleanup all resources and exit"""
    logger.info(f"Beginning shutdown (code: {code})")
    
    print("\nShutting down EYESY...")
    pygame.display.quit()
    pygame.quit()
    
    # Audio process cleanup
    if 'audio_process' in globals():
        print("Stopping audio process...")
        if audio_process.is_alive():
            audio_process.terminate()
            audio_process.join(timeout=1.0)
            if audio_process.is_alive():
                audio_process.kill()
            audio_process.close()
    
    # Cleanup other components
    if 'midi' in globals():
        print("Closing MIDI...")
        midi.close()
    
    if 'osc' in globals():
        print("Closing OSC...")
        osc.close()
    
    logger.info("Clean shutdown complete")
    sys.exit(code)

def initialize_system():
    """Initialize all system components"""
    logger.info("Initializing EYESY system")
    
    # Create eyesy object
    eyesy = eyesy.Eyesy()
    
    # Initialize OSC
    osc = OSCManager(eyesy)
    if not osc.init():
        raise RuntimeError("Failed to initialize OSC")
    
    # Check for USB drive
    if usbdrive.mount_usb():
        logger.info("USB drive detected")
        if os.path.exists("/usbdrive/Modes"):
            logger.info("Using modes from USB drive")
            eyesy.GRABS_PATH = "/usbdrive/Grabs/"
            eyesy.MODES_PATH = "/usbdrive/Modes/"
            eyesy.SCENES_PATH = "/usbdrive/Scenes/"
            eyesy.SYSTEM_PATH = "/usbdrive/System/"
            eyesy.running_from_usb = True
    
    # Ensure directories exist
    eyesy.ensure_directories()
    
    # Load configuration
    try:
        eyesy.load_config_file()
        if not hasattr(eyesy, 'RES') or eyesy.RES == (0,0):
            eyesy.RES = (1280, 720)  # Default fallback
    except Exception as e:
        logger.error(f"Config load failed: {e}, using defaults")
        eyesy.config = eyesy.DEFAULT_CONFIG
        eyesy.RES = (1280, 720)
    
    # Initialize pygame
    pygame.init()
    pygame.mouse.set_visible(False)
    clocker = pygame.time.Clock()
    
    # Initialize display
    try:
        hwscreen = pygame.display.set_mode(eyesy.RES)
        eyesy.xres, eyesy.yres = hwscreen.get_size()
        logger.info(f"Display initialized at {eyesy.xres}x{eyesy.yres}")
    except pygame.error as e:
        logger.critical(f"Display init failed: {e}")
        raise
    
    # Initialize audio
    try:
        BUFFER_SIZE = 100
        shared_buffer = Array(c_float, BUFFER_SIZE, lock=True)
        shared_buffer_r = Array(c_float, BUFFER_SIZE, lock=True)
        write_index = Value('i', 0)
        gain = Value('f', 0)
        peak = Value('f', 0)
        peak_r = Value('f', 0)
        lock = Lock()
        
        audio_process = Process(
            target=sound.audio_processing,
            args=(shared_buffer, shared_buffer_r, write_index, gain, peak, peak_r, lock)
        )
        audio_process.start()
        logger.info("Audio process started")
    except Exception as e:
        logger.error(f"Audio init failed: {e}")
        raise
    
    return eyesy, osc, hwscreen, audio_process, shared_buffer, shared_buffer_r, gain, peak, peak_r, lock, clocker

def main():
    signal.signal(signal.SIGTERM, handle_sigterm)
    
    try:
        # Initialize all components
        eyesy, osc, hwscreen, audio_process, \
        shared_buffer, shared_buffer_r, gain, \
        peak, peak_r, lock, clocker = initialize_system()
        
        # Main surfaces
        mode_screen = pygame.Surface((eyesy.xres, eyesy.yres))
        eyesy.screen = mode_screen
        
        # Load modes
        if not eyesy.load_modes():
            logger.error("No modes found")
            osd.loading_banner(hwscreen, "No Modes found. Insert USB drive with Modes folder and restart.")
            while True:
                for event in pygame.event.get():
                    if event.type == QUIT or (event.type == KEYDOWN and event.key == K_ESCAPE):
                        exitexit(0)
                time.sleep(1)
        
        # Initialize menu system
        init_menu_system(eyesy)
        
        # Main loop
        run_main_loop(eyesy, osc, hwscreen, mode_screen, 
                     shared_buffer, shared_buffer_r,
                     gain, peak, peak_r, lock, clocker)
        
    except Exception as e:
        logger.critical(f"Fatal error: {traceback.format_exc()}")
        exitexit(1)

def init_menu_system(eyesy):
    """Initialize all menu screens"""
    eyesy.menu_screens = {
        "home": ScreenMainMenu(eyesy),
        "test": ScreenTest(eyesy),
        "video_settings": ScreenVideoSettings(eyesy),
        "palette": ScreenPalette(eyesy),
        "wifi": ScreenWiFi(eyesy),
        "applogs": ScreenApplogs(eyesy),
        "midi_settings": ScreenMIDISettings(eyesy),
        "midi_pc_mapping": ScreenMIDIPCMapping(eyesy),
        "flashdrive": ScreenFlashDrive(eyesy)
    }
    eyesy.switch_menu_screen("home")

def run_main_loop(eyesy, osc, hwscreen, mode_screen, 
                 shared_buffer, shared_buffer_r,
                 gain, peak, peak_r, lock, clocker):
    """Main rendering and event loop"""
    start_time = time.time()
    last_usb_check = 0
    
    while True:
        current_time = time.time()
        eyesy.frame_count += 1
        
        # Handle events
        for event in pygame.event.get():
            if event.type == QUIT or (event.type == KEYDOWN and event.key == K_ESCAPE):
                exitexit(0)
        
        # Periodic USB check (every 30 seconds)
        if current_time - last_usb_check > 30:
            if usbdrive.check_usb() and not eyesy.running_from_usb:
                logger.info("New USB detected, restarting...")
                exitexit(1)  # Restart to load from USB
            last_usb_check = current_time
        
        # Update system state
        update_system_state(eyesy, osc, shared_buffer, shared_buffer_r, gain, peak, peak_r, lock)
        
        # Handle mode rendering
        handle_mode_rendering(eyesy, hwscreen, mode_screen)
        
        # Handle OSD
        if eyesy.show_osd and not eyesy.menu_mode:
            try:
                osd.render_overlay_480(hwscreen, eyesy)
            except Exception as e:
                logger.error(f"OSD error: {e}")
        
        # Handle menu system
        if eyesy.menu_mode:
            handle_menu_system(eyesy, hwscreen)
        
        # Update display
        pygame.display.flip()
        
        # Calculate FPS
        if eyesy.frame_count % 30 == 0:
            elapsed = current_time - start_time
            eyesy.fps = 30 / elapsed if elapsed > 0 else 0
            start_time = current_time
        
        # Periodic garbage collection
        if eyesy.frame_count % 300 == 0:
            gc.collect()
        
        # Maintain frame rate
        clocker.tick(30)

def update_system_state(eyesy, osc, shared_buffer, shared_buffer_r, gain, peak, peak_r, lock):
    """Update all system inputs and state"""
    # Process MIDI
    midi.recv_ttymidi(eyesy)
    midi.recv_usbmidi(eyesy)
    
    # Update controls
    eyesy.update_knobs_and_notes()
    eyesy.update_key_repeater()
    eyesy.check_gain_knob()
    eyesy.knob_seq_run()
    eyesy.set_knobs()
    
    # Update LED
    if eyesy.new_led:
        osc.send("/led", eyesy.led)
        eyesy.new_led = False
    
    # Process audio
    process_audio(eyesy, shared_buffer, shared_buffer_r, gain, peak, peak_r, lock)

def process_audio(eyesy, shared_buffer, shared_buffer_r, gain, peak, peak_r, lock):
    """Handle audio processing and triggering"""
    if not eyesy.key10_status:  # Normal audio mode
        with lock:
            eyesy.audio_in[:] = shared_buffer[:]
            eyesy.audio_in_r[:] = shared_buffer_r[:]
            g = eyesy.config["audio_gain"]
            gain.value = float((g * g * 50) + 1)
            eyesy.audio_peak = peak.value
            eyesy.audio_peak_r = peak_r.value
            
            if eyesy.config["trigger_source"] in (0, 2):  # Audio or Audio+MIDI trigger
                if eyesy.audio_peak > 20000 or eyesy.audio_peak_r > 20000:
                    eyesy.trig = True
    else:  # Test audio mode
        if not eyesy.menu_mode:
            eyesy.undulate_p += 0.005
            undulate = ((math.sin(eyes.undulate_p * 2 * math.pi) + 1) * 2) + 0.5
            for i in range(len(eyesy.audio_in)):
                eyesy.audio_in[i] = int(math.sin((i / 100) * 2 * math.pi * undulate) * 25000)
                eyesy.audio_in_r[i] = eyesy.audio_in[i]
            eyesy.audio_peak = 25000
            eyesy.audio_peak_r = 25000

def handle_mode_rendering(eyesy, hwscreen, mode_screen):
    """Handle the current mode's rendering"""
    if not eyesy.menu_mode:
        try:
            # Get current mode
            mode = sys.modules.get(eyesy.mode)
            if mode is None:
                raise ImportError(f"Mode {eyesy.mode} not loaded")
            
            # Clear screen if needed
            if eyesy.auto_clear:
                mode_screen.fill(eyesy.bg_color)
            
            # Run setup if requested
            if eyesy.run_setup:
                try:
                    mode.setup(hwscreen, eyesy)
                except Exception as e:
                    logger.error(f"Mode setup failed: {e}")
                eyesy.run_setup = False
            
            # Draw mode
            try:
                mode.draw(mode_screen, eyesy)
            except Exception as e:
                logger.error(f"Mode draw failed: {e}")
                # Fallback display
                mode_screen.fill((50, 50, 50))
                font = pygame.font.SysFont(None, 48)
                text = font.render("Mode Error", True, (255, 0, 0))
                mode_screen.blit(text, (50, 50))
            
            # Blit to main screen
            hwscreen.blit(mode_screen, (0, 0))
            
        except Exception as e:
            logger.error(f"Mode handling failed: {e}")
            eyesy.error = str(e)

def handle_menu_system(eyesy, hwscreen):
    """Handle menu rendering and input"""
    try:
        eyesy.current_screen.handle_events()
        eyesy.current_screen.render_with_title(hwscreen)
        
        if eyesy.restart:
            logger.info("Restart requested from menu")
            exitexit(1)
            
    except Exception as e:
        logger.error(f"Menu error: {e}")
        eyesy.error = str(e)
        hwscreen.fill(eyesy.bg_color)

if __name__ == "__main__":
    print("Starting EYESY...")
    main()