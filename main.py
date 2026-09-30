import pygame
from game.game_engine import GameEngine

pygame.init()

WIDTH, HEIGHT = 800, 500
SCREEN = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Simple Platformer")

SKY   = (100, 160, 220)
clock = pygame.time.Clock()
FPS   = 60

engine = GameEngine(WIDTH, HEIGHT)

def main():
    running = True
    while running:
        # Fill with sky colour each frame (menu/game-over overlay on top)
        SCREEN.fill(SKY)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            engine.handle_event(event)

        engine.handle_input()
        engine.update()
        engine.render(SCREEN)

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()

if __name__ == "__main__":
    main()
