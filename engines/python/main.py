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
        try:
            self.dispatcher.map("/knob/*", self.handle_knob)
            self.dispatcher.map("/led", self.handle_led)
            self.dispatcher.map("/mode", self.handle_mode)

            self.server = osc_server.ThreadingOSCUDPServer(("0.0.0.0", 12345), self.dispatcher)
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
        except (IndexError, ValueError):
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
    logger.info("Received SIGTERM, shutting down")
    exitexit(0)

def exitexit(code):
    logger.info(f"Beginning shutdown (code: {code})")
    print("\nShutting down EYESY...")
    pygame.display.quit()
    pygame.quit()

    if 'audio_process' in globals():
        print("Stopping audio process...")
        if audio_process.is_alive():
            audio_process.terminate()
            audio_process.join(timeout=1.0)
            if audio_process.is_alive():
                audio_process.kill()
            audio_process.close()

    if 'midi' in globals():
        print("Closing MIDI...")
        midi.close()

    if 'osc' in globals():
        print("Closing OSC...")
        osc.close()

    logger.info("Clean shutdown complete")
    sys.exit(code)

def initialize_system():
    logger.info("Initializing EYESY system")
    eyesy_obj = eyesy.Eyesy()
    osc = OSCManager(eyesy_obj)
    if not osc.init():
        raise RuntimeError("Failed to initialize OSC")

    if usbdrive.mount_usb():
        logger.info("USB drive detected")
        if os.path.exists("/usbdrive/Modes"):
            logger.info("Using modes from USB drive")
            eyesy_obj.GRABS_PATH = "/usbdrive/Grabs/"
            eyesy_obj.MODES_PATH = "/usbdrive/Modes/"
            eyesy_obj.SCENES_PATH = "/usbdrive/Scenes/"
            eyesy_obj.SYSTEM_PATH = "/usbdrive/System/"
            eyesy_obj.running_from_usb = True

    eyesy_obj.ensure_directories()
    try:
        eyesy_obj.load_config_file()
        if not hasattr(eyesy_obj, 'RES') or eyesy_obj.RES == (0,0):
            eyesy_obj.RES = (1280, 720)
    except Exception as e:
        logger.error(f"Config load failed: {e}, using defaults")
        eyesy_obj.config = eyesy_obj.DEFAULT_CONFIG
        eyesy_obj.RES = (1280, 720)

    pygame.init()
    pygame.mouse.set_visible(False)
    clocker = pygame.time.Clock()

    try:
        hwscreen = pygame.display.set_mode(eyesy_obj.RES)
        eyesy_obj.xres, eyesy_obj.yres = hwscreen.get_size()
        logger.info(f"Display initialized at {eyesy_obj.xres}x{eyesy_obj.yres}")
    except pygame.error as e:
        logger.critical(f"Display init failed: {e}")
        raise

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

    return eyesy_obj, osc, hwscreen, audio_process, shared_buffer, shared_buffer_r, gain, peak, peak_r, lock, clocker

def main():
    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        eyesy_obj, osc, hwscreen, audio_process, shared_buffer, shared_buffer_r, gain, peak, peak_r, lock, clocker = initialize_system()

        mode_screen = pygame.Surface((eyesy_obj.xres, eyesy_obj.yres))
        eyesy_obj.screen = mode_screen

        if not eyesy_obj.load_modes():
            logger.error("No modes found")
            osd.loading_banner(hwscreen, "No Modes found. Insert USB drive with Modes folder and restart.")
            while True:
                for event in pygame.event.get():
                    if event.type == QUIT or (event.type == KEYDOWN and event.key == K_ESCAPE):
                        exitexit(0)
                time.sleep(1)

        init_menu_system(eyesy_obj)
        run_main_loop(eyesy_obj, osc, hwscreen, mode_screen, shared_buffer, shared_buffer_r, gain, peak, peak_r, lock, clocker)

    except Exception as e:
        logger.critical(f"Fatal error: {traceback.format_exc()}")
        exitexit(1)

def init_menu_system(eyesy_obj):
    eyesy_obj.menu_screens = {
        "home": ScreenMainMenu(eyesy_obj),
        "test": ScreenTest(eyesy_obj),
        "video_settings": ScreenVideoSettings(eyesy_obj),
        "palette": ScreenPalette(eyesy_obj),
        "wifi": ScreenWiFi(eyesy_obj),
        "applogs": ScreenApplogs(eyesy_obj),
        "midi_settings": ScreenMIDISettings(eyesy_obj),
        "midi_pc_mapping": ScreenMIDIPCMapping(eyesy_obj),
        "flashdrive": ScreenFlashDrive(eyesy_obj)
    }
    eyesy_obj.switch_menu_screen("home")

def run_main_loop(eyesy_obj, osc, hwscreen, mode_screen, shared_buffer, shared_buffer_r, gain, peak, peak_r, lock, clocker):
    start_time = time.time()
    last_usb_check = 0
    last_mode_switch = time.time()
    MODE_SLIDE_INTERVAL = 20

    while True:
        current_time = time.time()
        eyesy_obj.frame_count += 1

        for event in pygame.event.get():
            if event.type == QUIT or (event.type == KEYDOWN and event.key == K_ESCAPE):
                exitexit(0)

        if current_time - last_usb_check > 30:
            if usbdrive.check_usb() and not eyesy_obj.running_from_usb:
                logger.info("New USB detected, restarting...")
                exitexit(1)
            last_usb_check = current_time

        update_system_state(eyesy_obj, osc, shared_buffer, shared_buffer_r, gain, peak, peak_r, lock)

        if not eyesy_obj.menu_mode and (current_time - last_mode_switch) > MODE_SLIDE_INTERVAL:
            eyesy_obj.mode_index = (eyesy_obj.mode_index + 1) % len(eyesy_obj.mode_names)
            eyesy_obj.set_mode_by_index(eyesy_obj.mode_index)
            last_mode_switch = current_time
            logger.info(f"Auto-switched to mode: {eyesy_obj.mode_names[eyesy_obj.mode_index]}")

        handle_mode_rendering(eyesy_obj, hwscreen, mode_screen)

        if eyesy_obj.show_osd and not eyesy_obj.menu_mode:
            try:
                osd.render_overlay_480(hwscreen, eyesy_obj)
            except Exception as e:
                logger.error(f"OSD error: {e}")

        if eyesy_obj.menu_mode:
            handle_menu_system(eyesy_obj, hwscreen)

        pygame.display.flip()

        if eyesy_obj.frame_count % 30 == 0:
            elapsed = current_time - start_time
            eyesy_obj.fps = 30 / elapsed if elapsed > 0 else 0
            start_time = current_time

        if eyesy_obj.frame_count % 300 == 0:
            gc.collect()

        clocker.tick(30)

def update_system_state(eyesy_obj, osc, shared_buffer, shared_buffer_r, gain, peak, peak_r, lock):
    eyesy_obj.update_knobs_and_notes()
    eyesy_obj.check_gain_knob()
    eyesy_obj.knob_seq_run()
    eyesy_obj.set_knobs()

    if eyesy_obj.new_led:
        osc.send("/led", eyesy_obj.led)
        eyesy_obj.new_led = False

    process_audio(eyesy_obj, shared_buffer, shared_buffer_r, gain, peak, peak_r, lock)

def process_audio(eyesy_obj, shared_buffer, shared_buffer_r, gain, peak, peak_r, lock):
    if not eyesy_obj.key10_status:
        with lock:
            eyesy_obj.audio_in[:] = shared_buffer[:]
            eyesy_obj.audio_in_r[:] = shared_buffer_r[:]
            g = eyesy_obj.config["audio_gain"]
            gain.value = float((g * g * 50) + 1)
            eyesy_obj.audio_peak = peak.value
            eyesy_obj.audio_peak_r = peak_r.value

            if eyesy_obj.config["trigger_source"] in (0, 2):
                if eyesy_obj.audio_peak > 20000 or eyesy_obj.audio_peak_r > 20000:
                    eyesy_obj.trig = True

def handle_mode_rendering(eyesy_obj, hwscreen, mode_screen):
    if not eyesy_obj.menu_mode:
        try:
            mode = sys.modules.get(eyesy_obj.mode)
            if mode is None:
                raise ImportError(f"Mode {eyesy_obj.mode} not loaded")

            if eyesy_obj.auto_clear:
                mode_screen.fill(eyesy_obj.bg_color)

            if eyesy_obj.run_setup:
                try:
                    mode.setup(hwscreen, eyesy_obj)
                except Exception as e:
                    logger.error(f"Mode setup failed: {e}")
                eyesy_obj.run_setup = False

            try:
                mode.draw(mode_screen, eyesy_obj)
            except Exception as e:
                logger.error(f"Mode draw failed: {e}")
                mode_screen.fill((50, 50, 50))
                font = pygame.font.SysFont(None, 48)
                text = font.render("Mode Error", True, (255, 0, 0))
                mode_screen.blit(text, (50, 50))

            hwscreen.blit(mode_screen, (0, 0))

        except Exception as e:
            logger.error(f"Mode handling failed: {e}")
            eyesy_obj.error = str(e)

def handle_menu_system(eyesy_obj, hwscreen):
    try:
        eyesy_obj.current_screen.handle_events()
        eyesy_obj.current_screen.render_with_title(hwscreen)

        if eyesy_obj.restart:
            logger.info("Restart requested from menu")
            exitexit(1)

    except Exception as e:
        logger.error(f"Menu error: {e}")
        eyesy_obj.error = str(e)
        hwscreen.fill(eyesy_obj.bg_color)

if __name__ == "__main__":
    print("Starting EYESY...")
    main()
