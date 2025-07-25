import os
from multiprocessing import Process, Array, Value, Lock
from ctypes import c_float
import time
import sys
import psutil
import math
import traceback
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

class OSCManager:
    def __init__(self, eyesy):
        self.eyesy = eyesy
        self.dispatcher = dispatcher.Dispatcher()
        self.server = None
        self.client = None
        
    def init(self):
        # Setup OSC server
        self.dispatcher.map("/knob/*", self.handle_knob)
        self.dispatcher.map("/led", self.handle_led)
        self.dispatcher.map("/mode", self.handle_mode)
        
        try:
            self.server = osc_server.ThreadingOSCUDPServer(
                ("0.0.0.0", 12345),  # Listen on all interfaces
                self.dispatcher
            )
            self.client = udp_client.SimpleUDPClient("127.0.0.1", 12346)  # For sending
            
            # Start server in a thread
            import threading
            server_thread = threading.Thread(target=self.server.serve_forever)
            server_thread.daemon = True
            server_thread.start()
            return True
        except Exception as e:
            print(f"OSC init failed: {str(e)}")
            return False
    
    def handle_knob(self, address, *args):
        try:
            knob_num = int(address.split('/')[-1])
            if 0 <= knob_num < len(self.eyesy.knobs):
                self.eyesy.knobs[knob_num] = args[0]
        except (IndexError, ValueError):
            pass
    
    def handle_led(self, address, value):
        self.eyesy.led = int(value)
    
    def handle_mode(self, address, value):
        self.eyesy.set_mode_by_index(int(value))
    
    def send(self, address, value):
        if self.client:
            self.client.send_message(address, value)
    
    def recv(self):
        # Handled automatically by the server thread
        pass
    
    def close(self):
        if self.server:
            self.server.shutdown()

def exitexit(code):
    print("EXIT exiting\n")
    pygame.display.quit()
    pygame.quit()
    print("stopping audio process")
    if audio_process.is_alive():  # Check if the process is still running
        audio_process.terminate()  # Terminate the process
        audio_process.join()       # Ensure the process has fully terminated
    print("closing audio")
    audio_process.close()  # Now it's safe to close the process
    print("closing midi")
    midi.close()
    print("closing osc")
    osc.close()
    print("exiting...")
    sys.exit(code)

print("starting...")

# create eyesy object
eyesy = eyesy.Eyesy()

# Initialize OSC
osc = OSCManager(eyesy)

# begin init
try:
    # see if there is a USB drive and we can run from there
    if usbdrive.mount_usb():
        print("found USB drive, checking for modes")
        if os.path.exists("/usbdrive/Modes"):
            print("found USB drive with modes, using USB")
            eyesy.GRABS_PATH =  "/usbdrive/Grabs/"
            eyesy.MODES_PATH =  "/usbdrive/Modes/"
            eyesy.SCENES_PATH = "/usbdrive/Scenes/"
            eyesy.SYSTEM_PATH = "/usbdrive/System/"
            eyesy.running_from_usb = True
        else:
            print("no modes found on USB drive, using internal")
    else:
        print("no USB found, using internal")

    eyesy.ensure_directories()

    # load config
    eyesy.load_config_file()

    # load palettes
    eyesy.load_palettes()

    # setup osc
    if not osc.init():
        raise Exception("Failed to initialize OSC")

    # midi
    print("init midi")
    midi.init()
    eyesy.usb_midi_device = midi.input_port_usb
    print(eyesy.usb_midi_device)

    # setup alsa sound shared resources
    print("init audio")
    BUFFER_SIZE = 100
    shared_buffer = Array(c_float, BUFFER_SIZE, lock=True)
    shared_buffer_r = Array(c_float, BUFFER_SIZE, lock=True)
    write_index = Value('i', 0)
    gain = Value('f', 0)
    peak = Value('f', 0)
    peak_r = Value('f', 0)
    lock = Lock()

    # Start the audio processing in a separate process
    audio_process = Process(target=sound.audio_processing, args=(shared_buffer, shared_buffer_r, write_index, gain, peak, peak_r, lock))
    audio_process.start()

    # init pygame
    pygame.init()
    pygame.mouse.set_visible(False)
    clocker = pygame.time.Clock()

    print("pygame version " + pygame.version.ver)

    # set led to running
    osc.send("/led", 7)

    # init fb and main surface hwscreen
    print("opening frame buffer...")
    hwscreen = pygame.display.set_mode(eyesy.RES)
    eyesy.xres = hwscreen.get_width()
    eyesy.yres = hwscreen.get_height()
    print("opened screen at: " + str(hwscreen.get_size()))
    hwscreen.fill((0,0,0))
    pygame.display.flip()

    # screen for mode to draw on
    mode_screen = pygame.Surface((eyesy.xres, eyesy.yres))
    eyesy.screen = mode_screen

    # load modes, post banner if none found
    if not eyesy.load_modes():
        print("no modes found.")
        osd.loading_banner(hwscreen, "No Modes found. Insert USB drive with Modes folder and restart.")
        while True:
            for event in pygame.event.get():
                if event.type == QUIT:
                    exitexit(0)
                elif event.type == KEYDOWN:
                    if event.key == K_ESCAPE:
                        exitexit(0)
            time.sleep(1)

    # run setup functions if modes have them
    print("running setup...")
    for i in range(0, len(eyesy.mode_names)):
        print(eyesy.mode_root)
        try:
            eyesy.set_mode_by_index(i)
            mode = sys.modules[eyesy.mode]
        except AttributeError:
            print("mode not found, or has error")
            continue
        try:
            osd.loading_banner(hwscreen, "Loading " + str(eyesy.mode))
            print("setup " + str(eyesy.mode))
            mode.setup(hwscreen, eyesy)
            eyesy.memory_used = psutil.virtual_memory()[2]
        except Exception as e:
            print("error in setup, or setup not found")
            print(traceback.format_exc())
            continue

    # load screen grabs
    eyesy.load_grabs()

    # load scenes
    eyesy.load_scenes()

    # set font for system stuff
    eyesy.font = pygame.font.Font("font.ttf", 16)

    # get total memory consumed
    eyesy.memory_used = psutil.virtual_memory()[2]
    eyesy.memory_used = (eyesy.memory_used / 75) * 100
    if eyesy.memory_used > 100:
        eyesy.memory_used = 100

    # set initial mode
    eyesy.set_mode_by_index(0)
    mode = sys.modules[eyesy.mode]

    # menu screens
    eyesy.menu_screens["home"] = ScreenMainMenu(eyesy)
    eyesy.menu_screens["test"] = ScreenTest(eyesy)
    eyesy.menu_screens["video_settings"] = ScreenVideoSettings(eyesy)
    eyesy.menu_screens["palette"] = ScreenPalette(eyesy)
    eyesy.menu_screens["wifi"] = ScreenWiFi(eyesy)
    eyesy.menu_screens["applogs"] = ScreenApplogs(eyesy)
    eyesy.menu_screens["midi_settings"] = ScreenMIDISettings(eyesy)
    eyesy.menu_screens["midi_pc_mapping"] = ScreenMIDIPCMapping(eyesy)
    eyesy.menu_screens["flashdrive"] = ScreenFlashDrive(eyesy)
    eyesy.switch_menu_screen("home")

    # used to measure fps
    start = time.time()

except Exception as e:
    print(traceback.format_exc())
    print("error with EYESY init")
    exitexit(0)

# Main loop
while True:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            exitexit(0)
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                exitexit(0)

    try:
        # Process OSC messages (handled automatically by server thread)
        
        # Check MIDI
        midi.recv_ttymidi(eyesy)
        midi.recv_usbmidi(eyesy)

        # Update knobs and notes
        eyesy.update_knobs_and_notes()
        eyesy.update_key_repeater()
        eyesy.check_gain_knob()
        eyesy.knob_seq_run()
        eyesy.set_knobs()

        # Measure FPS
        eyesy.frame_count += 1
        if (eyesy.frame_count % 30) == 0:
            now = time.time()
            eyesy.fps = 1 / ((now - start) / 30)
            start = now

        # Update LED if changed
        if eyesy.new_led:
            osc.send("/led", eyesy.led)

        # Audio processing
        if not eyesy.key10_status:
            with lock:
                eyesy.audio_in[:] = shared_buffer[:]
                eyesy.audio_in_r[:] = shared_buffer_r[:]
                g = eyesy.config["audio_gain"]
                gain.value = float((g * g * 50) + 1)
                eyesy.audio_peak = peak.value
                eyesy.audio_peak_r = peak_r.value
                if eyesy.config["trigger_source"] in (0, 2):
                    if eyesy.audio_peak > 20000 or eyesy.audio_peak_r > 20000:
                        eyesy.trig = True
        else:
            if not eyesy.menu_mode:
                undulate_p += .005
                undulate = ((math.sin(undulate_p * 2 * math.pi) + 1) * 2) + .5
                for i in range(len(eyesy.audio_in)):
                    eyesy.audio_in[i] = int(math.sin((i / 100) * 2 * math.pi * undulate) * 25000)
                    eyesy.audio_in_r[i] = eyesy.audio_in[i]
                eyesy.audio_peak = 25000
                eyesy.audio_peak_r = 25000

        # Handle current mode
        try:
            mode = sys.modules[eyesy.mode]
        except:
            eyesy.error = f"Mode {eyesy.mode} not loaded, probably has errors."
            print(eyesy.error)
            pygame.time.wait(200)

        if eyesy.screengrab_flag:
            eyesy.screengrab()

        eyesy.update_scene_save_key()

        if eyesy.auto_clear:
            mode_screen.fill(eyesy.bg_color)

        if eyesy.run_setup:
            eyesy.error = ''
            try:
                mode.setup(hwscreen, eyesy)
            except Exception as e:
                eyesy.error = traceback.format_exc()
                print("error with setup: " + eyesy.error)

        # Draw current mode
        if not eyesy.menu_mode:
            try:
                mode.draw(mode_screen, eyesy)
            except Exception as e:
                eyesy.error = traceback.format_exc()
                print("error with draw: " + eyesy.error)
                pygame.time.wait(200)

            hwscreen.blit(mode_screen, (0, 0))

        # OSD
        if eyesy.show_osd and not eyesy.menu_mode:
            try:
                osd.render_overlay_480(hwscreen, eyesy)
            except Exception as e:
                eyesy.error = traceback.format_exc()
                print("error with OSD: " + eyesy.error)
                pygame.time.wait(200)

        # Menu system
        if eyesy.menu_mode:
            try:
                eyesy.current_screen.handle_events()
                eyesy.current_screen.render_with_title(hwscreen)
            except Exception as e:
                eyesy.error = traceback.format_exc()
                print("error with Menu: " + eyesy.error)
                pygame.time.wait(200)
            
            if eyesy.restart:
                print("restart requested from menu, restarting")
                exitexit(1)
                
            if not eyesy.menu_mode:
                hwscreen.fill(eyesy.bg_color)

        pygame.display.flip()
        eyesy.clear_flags()

    except Exception as e:
        eyesy.clear_flags()
        eyesy.error = traceback.format_exc()
        print("problem in main loop")
        print(eyesy.error)
        pygame.time.wait(200)

    # Limit to 30 FPS
    clocker.tick(30)

print("Quit")
