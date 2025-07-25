import fileinput
import random
import math
import pygame
import traceback
import imp
import os
import glob
import sys
import time
import json
import helpers
import file_operations
import csv
import color_palettes
import config

class Eyesy:

    def __init__(self):
        self.VERSION = "3.0"
        # config stuff - updated paths to use home directory instead of /sdcard
        self.GRABS_PATH = os.path.expanduser("~/Grabs/")
        self.MODES_PATH = os.path.expanduser("~/Modes/")
        self.SCENES_PATH = os.path.expanduser("~/Scenes/")
        self.SYSTEM_PATH = os.path.expanduser("~/System/")

        self.COMPVIDS = ["NTSC","NTSC-J","NTSC-443","PAL","PAL-M","PAL-N","PAL60","SECAM"]

        self.RESOLUTIONS = [
            {"name": "640 x 480", "res": (640,480)},
            {"name": "720 x 480", "res": (720,480)},
            {"name": "800 x 600", "res": (800,600)},
            {"name": "1280 x 720", "res": (1280,720)},
            {"name": "1920 x 1080 - slow", "res": (1920,1080)}
        ]

        self.RES = (0,0)
        self.TRIGGER_SOURCES = ["Audio", "MIDI Note", "Audio or MIDI Note", 
                              "MIDI Clock 16th Note", "MIDI Clock 8th Note", 
                              "MIDI Clock 1/4 Note", "MIDI Clock Whole Note"]

        self.DEFAULT_CONFIG = {
            "video_resolution": 3,
            "audio_gain": .25,
            "trigger_source": 0,
            "fg_palette": 0,
            "bg_palette": 0,
            "midi_channel": 1,
            "knob1_cc": 20,
            "knob2_cc": 21,
            "knob3_cc": 22,
            "knob4_cc": 23,
            "knob5_cc": 24,
            "auto_clear_cc": 25,
            "fg_palette_cc": -1,
            "bg_palette_cc": -1,
            "mode_cc": -1,
            "notes_change_mode": False,
            "pc_map": {}
        }

        self.config = {}
        
        # Colors
        self.BLACK = (0, 0, 0)
        self.WHITE = (255, 255, 255)
        self.LGRAY = (200, 200, 200)
        self.RED = (255, 0, 0)
        self.GREEN = (0, 255, 0)
        self.BLUE = (0, 0, 255)
        self.OSDBG = (0, 0, 255)

        # Screen grabs
        self.lastgrab = None
        self.lastgrab_thumb = None
        self.tengrabs_thumbs = []
        self.grabcount = 0
        self.grabindex = 0
        self.screengrab_flag = False

        # Modes
        self.mode_names = []
        self.mode_index = 0
        self.mode = ''
        self.mode_root = ''
        self.error = ''
        self.run_setup = False

        # Scenes
        self.scenes = []
        self.scene_index = -1
        self.save_key_status = False
        self.save_key_time = 0
        self.next_numbered_scene = 1

        # Audio
        self.audio_in = [0] * 100
        self.audio_in_r = [0] * 100
        self.audio_peak = 0
        self.audio_peak_r = 0
        self.audio_scale = 1.0

        # Knobs
        self.knob1 = 0
        self.knob2 = 1
        self.knob3 = 1
        self.knob4 = 1
        self.knob5 = 1
        self.knob = [.2] * 5
        self.knob_hardware = [.2] * 5
        self.knob_hardware_last = [-1] * 5
        self.knob_snapshot = [.2] * 5
        self.knob_override = [False] * 5
        self.knob_last = [-1] * 5

        # MIDI
        self.midi_notes = [0] * 128
        self.midi_notes_last = [0] * 128
        self.midi_note_new = False
        self.midi_clk = 0
        self.new_midi = False
        self.usb_midi_name = ''
        self.usb_midi_present = False

        # System
        self.led = 0
        self.new_led = False
        self.screen = None
        self.xres = 1280
        self.yres = 720
        self.bg_color = (0, 0, 0)
        self.memory_used = 0
        self.ip = ''
        self.auto_clear = True
        self.restart = False
        self.show_osd = False
        self.menu_mode = False
        self.osd_first = False
        self.trig = False
        self.fps = 0
        self.frame_count = 0
        self.font = None
        self.running_from_usb = False
        self.usb_midi_device = None

        # Menu
        self.current_screen = None
        self.menu_screens = {}

        # Keys
        self.key1_press = False
        self.key2_press = False
        self.key3_press = False
        self.key4_press = False
        self.key5_press = False
        self.key6_press = False
        self.key7_press = False
        self.key8_press = False
        self.key9_press = False
        self.key10_press = False

        self.key2_status = False
        self.key4_status = False
        self.key5_status = False
        self.key6_status = False
        self.key7_status = False
        self.key10_status = False

        # Key repeat counters
        self.key4_td = 0
        self.key5_td = 0
        self.key6_td = 0
        self.key7_td = 0
        self.key10_td = 0

        # Color
        self.palettes = color_palettes.abcd_palettes
        self.fg_palette = 0
        self.bg_palette = 0
        self.color_lfo_inc = 0
        self.color_lfo_index = 0
        self.palettes_user_defined = False

        # Knob sequencer
        self.knob_seq = []
        self.knob_seq_last_values = [-1] * 5
        self.knob_seq_index = 0
        self.knob_seq_state = "stopped"

        # Gain control
        self.gain_knob_unlocked = False
        self.gain_knob_capture = 0
        self.gain_value_snapshot = 0

        self.clear_flags()

    def ensure_directories(self):
        """Create all required directories with fallback to home directory if permissions fail."""
        paths = {
            'GRABS': self.GRABS_PATH,
            'MODES': self.MODES_PATH,
            'SCENES': self.SCENES_PATH,
            'SYSTEM': self.SYSTEM_PATH
        }

        for name, path in paths.items():
            try:
                os.makedirs(path, exist_ok=True)
                print(f"Created directory: {path}")
            except PermissionError:
                # Fallback to home directory
                home_path = os.path.expanduser(f"~/{name.lower()}/")
                print(f"Permission denied for {path}, using {home_path} instead")
                os.makedirs(home_path, exist_ok=True)
                setattr(self, f"{name}_PATH", home_path)
            except Exception as e:
                print(f"Error creating directory {path}: {e}")

    def load_palettes(self):
        """Load color palettes with improved error handling."""
        palettes_file = os.path.join(self.SYSTEM_PATH, "palettes.json")
        
        if not os.path.exists(palettes_file):
            print(f"Using default palettes (file not found: {palettes_file})")
            self.palettes = color_palettes.abcd_palettes
            return

        try:
            with open(palettes_file, "r") as f:
                data = json.load(f)
            
            if not isinstance(data, list):
                raise ValueError("Palettes data is not a list")
                
            # Validate palette structure
            valid_palettes = []
            for palette in data:
                if isinstance(palette, dict):
                    if all(key in palette and isinstance(palette[key], list) and len(palette[key]) == 3 
                          for key in ['a', 'b', 'c', 'd']):
                        valid_palettes.append(palette)
            
            if valid_palettes:
                self.palettes = valid_palettes
                self.palettes_user_defined = True
                print(f"Loaded {len(valid_palettes)} palettes from {palettes_file}")
            else:
                print("No valid palettes found, using defaults")
                self.palettes = color_palettes.abcd_palettes
                
        except Exception as e:
            print(f"Error loading palettes: {e}, using defaults")
            self.palettes = color_palettes.abcd_palettes

    def load_config_file(self):
        """Load configuration with better error handling and validation."""
        config_file = os.path.join(self.SYSTEM_PATH, "config.json")
        
        try:
            # Create system directory if needed
            os.makedirs(self.SYSTEM_PATH, exist_ok=True)
            
            # Load or create config
            if os.path.exists(config_file):
                with open(config_file, "r") as f:
                    self.config = json.load(f)
                print("Loaded existing config")
            else:
                self.config = self.DEFAULT_CONFIG.copy()
                with open(config_file, "w") as f:
                    json.dump(self.config, f, indent=4)
                print("Created new config file")
                
            self.validate_config()
            
            # Apply config settings
            try:
                self.RES = self.RESOLUTIONS[self.config["video_resolution"]]["res"]
                self.bg_palette = self.config["bg_palette"]
                self.fg_palette = self.config["fg_palette"]
            except Exception as e:
                print(f"Error applying config: {e}")
                self.RES = self.RESOLUTIONS[0]["res"]  # Fallback to first resolution
                
        except Exception as e:
            print(f"Error loading config: {e}, using defaults")
            self.config = self.DEFAULT_CONFIG.copy()
            self.RES = self.RESOLUTIONS[0]["res"]

    def validate_config(self):
        """Validate and sanitize configuration values."""
        # Ensure all required keys exist
        for key in self.DEFAULT_CONFIG:
            if key not in self.config:
                self.config[key] = self.DEFAULT_CONFIG[key]
        
        # Validate video resolution
        if "video_resolution" in self.config:
            if not (0 <= self.config["video_resolution"] < len(self.RESOLUTIONS)):
                self.config["video_resolution"] = self.DEFAULT_CONFIG["video_resolution"]
        
        # Validate palette indices
        if "fg_palette" in self.config:
            if not isinstance(self.config["fg_palette"], int) or self.config["fg_palette"] < 0:
                self.config["fg_palette"] = self.DEFAULT_CONFIG["fg_palette"]
        
        if "bg_palette" in self.config:
            if not isinstance(self.config["bg_palette"], int) or self.config["bg_palette"] < 0:
                self.config["bg_palette"] = self.DEFAULT_CONFIG["bg_palette"]

    def clear_flags(self):
        """Reset all state flags."""
        self.new_midi = False
        self.trig = False
        self.run_setup = False
        self.screengrab_flag = False
        self.midi_note_new = False
        
        for i in range(128):
            self.midi_notes_last[i] = self.midi_notes[i]
            
        self.key1_press = False
        self.key2_press = False
        self.key3_press = False
        self.key4_press = False
        self.key5_press = False
        self.key6_press = False
        self.key7_press = False
        self.key8_press = False
        self.key9_press = False
        self.key10_press = False
        self.new_led = False

    def load_modes(self):
        """Load available modes from the modes directory."""
        try:
            mode_files = glob.glob(os.path.join(self.MODES_PATH, "*.py"))
            self.mode_names = [os.path.splitext(os.path.basename(f))[0] for f in mode_files]
            
            if not self.mode_names:
                return False
                
            print(f"Found {len(self.mode_names)} modes")
            return True
            
        except Exception as e:
            print(f"Error loading modes: {e}")
            return False

    def set_mode_by_index(self, index):
        """Set the current mode by index."""
        if 0 <= index < len(self.mode_names):
            self.mode_index = index
            self.mode = self.mode_names[index]
            self.mode_root = os.path.join(self.MODES_PATH, self.mode)
            self.run_setup = True
            return True
        return False

    def save_config(self):
        """Save current configuration to file."""
        try:
            with open(os.path.join(self.SYSTEM_PATH, "config.json"), "w") as f:
                json.dump(self.config, f, indent=4)
            return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False

    def switch_menu_screen(self, screen_name):
        """Switch to a different menu screen."""
        if screen_name in self.menu_screens:
            self.current_screen = self.menu_screens[screen_name]
            return True
        return False

    def update_knobs_and_notes(self):
        """Update knob and note states."""
        # Update knob values
        for i in range(5):
            if not self.knob_override[i]:
                self.knob[i] = self.knob_hardware[i]
        
        # Check for new MIDI notes
        self.midi_note_new = any(
            self.midi_notes[i] != self.midi_notes_last[i]
            for i in range(128)
        )

    def screengrab(self):
        """Save a screenshot of the current display."""
        try:
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            filename = os.path.join(self.GRABS_PATH, f"grab_{timestamp}.png")
            pygame.image.save(self.screen, filename)
            print(f"Saved screengrab: {filename}")
            self.screengrab_flag = False
            return True
        except Exception as e:
            print(f"Error saving screengrab: {e}")
            return False