import pygame
import math
import threading
from .player import Player
from .platform import Platform
from .hazard import Hazard

WHITE      = (255, 255, 255)
BLACK      = (0,   0,   0)
BROWN      = (150, 100, 60)
RED        = (220, 60,  60)
GREEN      = (0,   200, 0)
DARK_OVER  = (20,  20,  20, 180)  # semi-transparent overlay colour

# Difficulty presets: (gravity, jump_strength, player_speed)
# Hard uses the same gravity/jump as Medium — only hazard count differs.
DIFFICULTIES = {
    "Easy":   (0.4,  -14, 5),
    "Medium": (0.55, -12, 4),
    "Hard":   (0.55, -12, 4),
}

# ── Sound helpers ────────────────────────────────────────────────────────────
# Pure stdlib — no numpy required.
# Builds a minimal PCM WAV in memory and loads it via pygame.mixer.Sound.

import array as _array
import struct as _struct
import random as _random

_SAMPLE_RATE = 44100

def _build_wav(samples: "_array.array") -> bytes:
    """Wrap a signed-16-bit mono sample array in a WAV header."""
    data = samples.tobytes()
    return _struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(data), b"WAVE",
        b"fmt ", 16,
        1,               # PCM
        1,               # mono
        _SAMPLE_RATE,
        _SAMPLE_RATE * 2,  # byte rate
        2,               # block align
        16,              # bits per sample
        b"data", len(data),
    ) + data

def _make_sound_from_samples(samples: "_array.array") -> "pygame.mixer.Sound":
    return pygame.mixer.Sound(buffer=_build_wav(samples))

def _make_jump_sound() -> "pygame.mixer.Sound":
    """Short frequency-rising blip (chirp)."""
    n   = int(_SAMPLE_RATE * 0.12)
    buf = _array.array("h")
    for i in range(n):
        freq = 300 + 700 * (i / n)        # 300 Hz → 1000 Hz
        env  = 1.0 - (i / n) * 0.3        # slight fade
        val  = int(28000 * env * math.sin(2 * math.pi * freq * i / _SAMPLE_RATE))
        buf.append(max(-32768, min(32767, val)))
    return _make_sound_from_samples(buf)

def _make_goal_sound() -> "pygame.mixer.Sound":
    """Four-note ascending arpeggio."""
    freqs = [523, 659, 784, 1047]          # C5 E5 G5 C6
    seg   = int(_SAMPLE_RATE * 0.07)       # ~70 ms per note
    buf   = _array.array("h")
    for freq in freqs:
        for i in range(seg):
            env = 1.0 - (i / seg) * 0.4
            val = int(26000 * env * math.sin(2 * math.pi * freq * i / _SAMPLE_RATE))
            buf.append(max(-32768, min(32767, val)))
    return _make_sound_from_samples(buf)

def _make_death_sound() -> "pygame.mixer.Sound":
    """Descending tone with noise — a classic 'ouch' effect."""
    n   = int(_SAMPLE_RATE * 0.4)
    buf = _array.array("h")
    for i in range(n):
        t     = i / _SAMPLE_RATE
        freq  = 400 - 300 * (i / n)       # 400 Hz → 100 Hz
        noise = _random.uniform(-0.25, 0.25)
        env   = 1.0 - (i / n) * 0.8
        val   = int(24000 * env * (math.sin(2 * math.pi * freq * t) + noise))
        buf.append(max(-32768, min(32767, val)))
    return _make_sound_from_samples(buf)

def _try_make_sound(fn):
    """Return a Sound object, or None if audio init failed."""
    try:
        return fn()
    except Exception:
        return None


# ── GameEngine ───────────────────────────────────────────────────────────────

class GameEngine:
    STATE_MENU      = "menu"
    STATE_PLAYING   = "playing"
    STATE_GAME_OVER = "game_over"

    TERMINAL_VY = 18  # max fall speed in px/frame

    def __init__(self, width, height):
        self.width  = width
        self.height = height

        self.font       = pygame.font.SysFont("Arial", 30)
        self.font_big   = pygame.font.SysFont("Arial", 52, bold=True)
        self.font_small = pygame.font.SysFont("Arial", 22)

        # Sounds are generated in a background thread so startup is instant.
        # Each attribute starts as None and is filled in once the thread finishes.
        self.snd_jump  = None
        self.snd_goal  = None
        self.snd_death = None
        threading.Thread(target=self._load_sounds, daemon=True).start()

        # Start on the difficulty-select / main menu
        self.state      = self.STATE_MENU
        self.difficulty = "Medium"
        self._menu_options   = list(DIFFICULTIES.keys())
        self._menu_selection = 1  # default index → "Medium"

        self._init_level()

    def _load_sounds(self):
        """Runs in a background thread — generates sounds without blocking the menu."""
        pygame.mixer.init(frequency=44100, size=-16, channels=1, buffer=512)
        self.snd_jump  = _try_make_sound(_make_jump_sound)
        self.snd_goal  = _try_make_sound(_make_goal_sound)
        self.snd_death = _try_make_sound(_make_death_sound)

    # ── level / player setup ─────────────────────────────────────────────────

    def _init_level(self):
        gravity, jump_strength, speed = DIFFICULTIES[self.difficulty]
        self.gravity = gravity

        self.start_x, self.start_y = 40, self.height - 120
        self.player = Player(self.start_x, self.start_y)
        self.player.jump_strength = jump_strength
        self.player.speed         = speed

        ground_y = self.height - 40
        self.platforms = [
            Platform(0,   ground_y,        160),
            Platform(220, ground_y,        140),
            Platform(420, ground_y - 60,   120),
            Platform(600, ground_y,        180),
        ]
        # Hazard count scales with difficulty.
        # Easy:   1 small hazard — on the last platform only, easy to dodge
        # Medium: 2 hazards — last platform + elevated platform
        # Hard:   3 hazards — all three platforms have one
        all_hazards = [
            Hazard(670, ground_y - 14,         22),   # last ground platform
            Hazard(455, ground_y - 60 - 14,    22),   # elevated middle platform
            Hazard(320, ground_y - 14,         22),   # second ground platform
        ]
        counts = {"Easy": 1, "Medium": 2, "Hard": 3}
        self.hazards = all_hazards[: counts[self.difficulty]]
        self.goal_x   = 740
        self.score    = 0

    # ── event / input handling ───────────────────────────────────────────────

    def handle_event(self, event):
        if self.state == self.STATE_MENU:
            self._menu_event(event)
        elif self.state == self.STATE_PLAYING:
            if event.type == pygame.KEYDOWN and event.key in (
                pygame.K_SPACE, pygame.K_UP, pygame.K_w
            ):
                jumped = self.player.jump()
                if jumped and self.snd_jump:
                    self.snd_jump.play()
        elif self.state == self.STATE_GAME_OVER:
            self._game_over_event(event)

    def _menu_event(self, event):
        if event.type != pygame.KEYDOWN:
            return
        if event.key in (pygame.K_UP, pygame.K_w):
            self._menu_selection = (self._menu_selection - 1) % len(self._menu_options)
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self._menu_selection = (self._menu_selection + 1) % len(self._menu_options)
        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
            self.difficulty = self._menu_options[self._menu_selection]
            self._init_level()
            self.state = self.STATE_PLAYING

    def _game_over_event(self, event):
        if event.type != pygame.KEYDOWN:
            return
        if event.key == pygame.K_r:
            # Go back to menu to pick difficulty again
            self.state = self.STATE_MENU
        elif event.key == pygame.K_ESCAPE:
            pygame.event.post(pygame.event.Event(pygame.QUIT))

    def handle_input(self):
        if self.state != self.STATE_PLAYING:
            return
        keys = pygame.key.get_pressed()
        self.player.vx = 0
        if keys[pygame.K_LEFT]  or keys[pygame.K_a]:
            self.player.vx = -self.player.speed
        if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
            self.player.vx =  self.player.speed

    # ── update ───────────────────────────────────────────────────────────────

    def update(self):
        if self.state != self.STATE_PLAYING:
            return

        p = self.player

        # --- horizontal movement (clamp to screen left edge) -----------------
        p.x = max(0, p.x + p.vx)

        # --- vertical movement with sub-step collision -----------------------
        p.vy = min(p.vy + self.gravity, self.TERMINAL_VY)  # gravity + cap

        # Break the vertical displacement into 1-px steps so the player can
        # never tunnel through a thin platform in a single frame.
        steps    = max(1, int(math.ceil(abs(p.vy))))
        step_dy  = p.vy / steps
        p.on_ground = False

        for _ in range(steps):
            p.y += step_dy
            for platform in self.platforms:
                if p.rect().colliderect(platform.rect()):
                    if step_dy > 0:  # falling onto top of platform
                        p.y       = platform.y - p.height
                        p.vy      = 0
                        step_dy   = 0
                        p.on_ground = True
                    elif step_dy < 0:  # jumping into underside
                        p.y     = platform.y + platform.height
                        p.vy    = 0
                        step_dy = 0

        # --- hazard collision -------------------------------------------------
        for hazard in self.hazards:
            if p.rect().colliderect(hazard.rect()):
                self._trigger_death()
                return

        # --- fell off screen -------------------------------------------------
        if p.y > self.height:
            self._trigger_death()
            return

        # --- reached goal ----------------------------------------------------
        if p.x + p.width >= self.goal_x:
            self.score += 1
            if self.snd_goal:
                self.snd_goal.play()
            p.x, p.y = self.start_x, self.start_y
            p.vx = p.vy = 0

    def _trigger_death(self):
        if self.snd_death:
            self.snd_death.play()
        self.state = self.STATE_GAME_OVER

    # ── render ───────────────────────────────────────────────────────────────

    def render(self, screen):
        if self.state == self.STATE_MENU:
            self._render_menu(screen)
        elif self.state == self.STATE_PLAYING:
            self._render_game(screen)
        elif self.state == self.STATE_GAME_OVER:
            self._render_game(screen)   # show level in background
            self._render_game_over(screen)

    def _render_game(self, screen):
        for platform in self.platforms:
            pygame.draw.rect(screen, BROWN, platform.rect())
        for hazard in self.hazards:
            pygame.draw.rect(screen, RED, hazard.rect())

        goal_rect = pygame.Rect(self.goal_x, 0, 6, self.height)
        pygame.draw.rect(screen, GREEN, goal_rect)

        pygame.draw.rect(screen, WHITE, self.player.rect())

        score_surf = self.font.render(f"Score: {self.score}", True, WHITE)
        screen.blit(score_surf, (10, 10))

        diff_surf = self.font_small.render(f"Difficulty: {self.difficulty}", True, WHITE)
        screen.blit(diff_surf, (10, 45))

    def _render_menu(self, screen):
        screen.fill((30, 30, 60))

        title = self.font_big.render("Simple Platformer", True, WHITE)
        screen.blit(title, (self.width // 2 - title.get_width() // 2, 100))

        sub = self.font.render("Select Difficulty", True, (200, 200, 200))
        screen.blit(sub, (self.width // 2 - sub.get_width() // 2, 180))

        for i, label in enumerate(self._menu_options):
            colour = GREEN if i == self._menu_selection else WHITE
            opt = self.font.render(label, True, colour)
            screen.blit(opt, (self.width // 2 - opt.get_width() // 2, 240 + i * 50))

        hint = self.font_small.render("Up/Down to choose  |  Enter / Space to start", True, (160, 160, 160))
        screen.blit(hint, (self.width // 2 - hint.get_width() // 2, 420))

    def _render_game_over(self, screen):
        # dim overlay
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        screen.blit(overlay, (0, 0))

        go_text = self.font_big.render("GAME OVER", True, RED)
        screen.blit(go_text, (self.width // 2 - go_text.get_width() // 2, 140))

        score_text = self.font.render(f"Final Score: {self.score}", True, WHITE)
        screen.blit(score_text, (self.width // 2 - score_text.get_width() // 2, 230))

        replay = self.font.render("R  — Play Again", True, GREEN)
        quit_  = self.font.render("Esc — Quit",       True, (220, 100, 100))
        screen.blit(replay, (self.width // 2 - replay.get_width() // 2, 300))
        screen.blit(quit_,  (self.width // 2 - quit_.get_width()  // 2, 350))
