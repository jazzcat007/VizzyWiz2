import pygame
import random

def setup(screen, eyesy):
    eyesy.bg_color = (0, 0, 0)  # Black background
    print("Default mode loaded!")

def draw(screen, eyesy):
    # Audio-reactive circles
    screen.fill(eyesy.bg_color)
    width, height = screen.get_size()
    
    # Center circle reacts to audio peak
    pygame.draw.circle(screen, (255, 0, 0),
                      (width//2, height//2),
                      50 + (eyesy.audio_peak/500))
    
    # Random smaller circles
    for i in range(5):
        x = random.randint(0, width)
        y = random.randint(0, height)
        size = 10 + (eyesy.audio_in[i*20]/1000)
        pygame.draw.circle(screen, (0, 255, 255), (x,y), abs(int(size)))
