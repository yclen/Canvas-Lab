"""game_loop.py — the thin runner.

Just wiring: pygame/window setup, then the main loop that pumps events into the
scene, updates, and draws. No app logic lives here — every class and helper comes
from canvas.py (and the object catalog in things.py). If you find yourself
writing real behavior in this file, it belongs in canvas.py instead; and nothing
here should mention a specific object type.
"""

import ctypes
import os
import platform
import sys

import pygame

import canvas  # all the classes/helpers live here
import instruments  # the link to the instrument server (devices)
import things  # noqa: F401 — defining the catalog's classes registers them


# ---- Setup -----------------------------------------------------------------
if platform.system() == "Windows":
    # Distinct AppUserModelID so Windows uses our taskbar icon, not python's.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "Yaakov.CanvasApp"
    )


def _on_crash(exc_type, exc, tb):
    """Uncaught error: log it to errors.log (there's no console) and save the
    canvas before the app closes."""
    canvas.report_error("crash", exc)
    try:
        scene.save()
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _on_crash
pygame.init()

icon = pygame.image.load(os.path.join(canvas.SCRIPT_DIR, "icon.ico"))
pygame.display.set_icon(icon)
screen = pygame.display.set_mode((640, 480), pygame.RESIZABLE)
pygame.display.set_caption("Canvas App")
clock = pygame.time.Clock()


# ---- State -----------------------------------------------------------------
scene = canvas.Scene.load()
camera = scene.camera
scene.restore_view(screen.get_size())   # reopen where you were at last close
app = canvas.App(scene, screen.get_size())   # what object hooks / callbacks receive
instruments.client.start()          # connects to instrument_server.py in the background
builder = app.train_builder         # interactive train placement mode
wall_builder = app.wall_builder     # drag-to-draw wall placement mode
placer = app.placer                 # holding a picked object, to drop with a click
picker = app.picker                 # waiting for a click on an object (e.g. a readout's source)
menubar = canvas.MenuBar()
inspector = canvas.InspectorPanel()
marquee = canvas.MarqueeSelect()    # left-drag on empty canvas selects many

panning = False
right_down = None           # where the right button went down: a click (not a pan) opens the menu
RIGHT_CLICK_SLOP = 5        # px the mouse may move during a right-click before it counts as a pan
dt = 0.0
was_moving = False          # tracks arrow-key movement so we save on release
last_click_time = 0         # for hand-rolled double-click detection
last_click_shape = None
key_repeat_on = False       # held keys repeat only while a text box is open
held = None                 # object that took the last left-click: gets drag/release until the button's up


def handle_menu_command(command):
    """Turn a menu selection into an app action."""
    sel = scene.selected
    if command.startswith("add:"):              # Add ▸ <type>: the type decides how
        canvas.OBJECT_TYPES[command[4:]].create(app)
    elif command == "inventory":
        app.open_inventory()
    elif command == "edit_name":
        if sel is not None and getattr(sel, "text", None) is not None:   # things with a name
            def rename(text, obj=sel):
                if text.strip():
                    obj.text = text.strip()
                    scene.save()
            app.ask("Edit text:", rename, value=sel.text)
    elif command == "copy":
        new = canvas.copy_object(sel)           # None for trains / nothing selected
        if new is not None:
            app.place(new)
    elif command == "delete_node":
        objs = scene.selection                  # works for any object (incl. trains)
        if objs:
            title = (f"Delete '{objs[0].display_name}'?" if len(objs) == 1
                     else f"Delete {len(objs)} objects?")
            app.confirm(title, "Are you sure you want to delete?",
                        lambda: scene.delete_many(objs))
    elif command == "goto":
        if sel is not None and sel.center is not None:
            camera.look_at(sel.center, canvas.GOTO_ZOOM, screen.get_size())


def open_context_menu(pos):
    """Right-click menu: for the selection (right-clicking an unselected
    object selects just it first). Nothing selected, nothing opens."""
    if pos[1] <= canvas.MENU_BAR_HEIGHT or (scene.selected is not None
                                            and inspector.contains(pos, screen.get_size())):
        return
    clicked = scene.hit_test(pos, camera)
    if clicked is not None and clicked.selectable and not clicked.is_selected:
        scene.select_only(clicked)
    objs = scene.selection
    if not objs:
        return
    label = (f"Delete '{objs[0].display_name}'" if len(objs) == 1
             else f"Delete {len(objs)} objects")
    app.context_menu(pos, [(label, lambda: handle_menu_command("delete_node"), "danger")])


# ---- Main loop -------------------------------------------------------------
running = True
while running:
    mouse_pos = pygame.mouse.get_pos()
    mouse_pressed = pygame.mouse.get_pressed()[0]
    app.screen_size = screen.get_size()

    # Key-repeat on while typing (held Backspace / arrows keep going), off on
    # the canvas (so holding C or Delete doesn't fire over and over).
    want_repeat = isinstance(app.modal, canvas.TextInputModal)
    if want_repeat != key_repeat_on:
        pygame.key.set_repeat(*(canvas.TEXT_KEY_REPEAT if want_repeat else ()))
        key_repeat_on = want_repeat

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
            continue
        if event.type == pygame.VIDEORESIZE:
            screen = pygame.display.set_mode((event.w, event.h), pygame.RESIZABLE)
            app.screen_size = screen.get_size()
            continue

        # An object that handled a left-click holds the mouse until release:
        # motion goes to its on_drag (a slider follows the cursor), and the
        # release to on_release. Driven by events, so it's exact and immediate.
        if held is not None:
            if event.type == pygame.MOUSEMOTION:
                held.on_drag(event.pos, app)
                continue
            if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                obj, held = held, None
                obj.on_release(event.pos, app)
                continue

        # A modal, while open, swallows every other event.
        if app.modal is not None:
            modal = app.modal
            result = modal.handle_event(event, screen.get_size())
            if result in ("submit", "confirm"):
                app.modal = None
                modal.commit()        # runs its callback (which may open another modal)
            elif result == "cancel":
                app.modal = None
            continue

        # E opens the object menu from anywhere (it drops any active mode).
        if event.type == pygame.KEYDOWN and event.key == pygame.K_e:
            app.open_inventory()
            continue

        # Pick mode: the next left-click chooses an object (or cancels); Esc cancels.
        if picker.active:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                picker.cancel()
                continue
            if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                    and event.pos[1] > canvas.MENU_BAR_HEIGHT and not menubar.open_menu):
                if scene.selected is not None and inspector.contains(event.pos, screen.get_size()):
                    picker.cancel()               # a click on the panel isn't a pick
                else:
                    picker.click(scene, event.pos, camera)
                continue

        # Place mode: left-click drops the held object (Shift = keep placing),
        # Esc puts it back. Clicks on the menu bar and pan/zoom fall through.
        if placer.active:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                placer.cancel()
                continue
            if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                    and event.pos[1] > canvas.MENU_BAR_HEIGHT and not menubar.open_menu):
                keep_going = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
                placer.place(camera.screen_to_world(event.pos), scene, keep_going)
                continue

        # Train build mode: claims left-click / mouse-move / Esc / Backspace,
        # but lets wheel-zoom and right-drag-pan fall through so the camera
        # stays live while you draw the route.
        if builder.active:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                builder.cancel()
                continue
            if event.type == pygame.KEYDOWN and event.key == pygame.K_BACKSPACE:
                builder.undo()
                continue
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                node = next((o for o in scene.objects
                             if isinstance(o, canvas.Node) and o.hit(event.pos, camera)), None)
                train = builder.on_left_click(node, camera.screen_to_world(event.pos))
                if train is not None:
                    scene.add(train)
                    builder.cancel()
                continue
            if event.type == pygame.MOUSEMOTION:
                hover = next((o for o in scene.objects
                              if isinstance(o, canvas.Node) and o.hit(event.pos, camera)), None)
                builder.on_mouse_move(camera.screen_to_world(event.pos), hover)
                # fall through: right-drag panning below still applies

        # Wall build mode: drag out a box (press → move → release); Esc cancels.
        if wall_builder.active:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                wall_builder.cancel()
                continue
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                wall_builder.on_press(camera.screen_to_world(event.pos))
                continue
            if event.type == pygame.MOUSEMOTION:
                wall_builder.on_move(camera.screen_to_world(event.pos))
                continue
            if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                # Ignore the release of the menu click that opened this mode;
                # only finish if an actual drag was started.
                if wall_builder.start is None:
                    continue
                box = wall_builder.rect()
                wall_builder.cancel()
                if box is not None:
                    app.ask("Wall name:",
                            lambda text, box=box: scene.add(canvas.Wall(*box, text.strip() or "Wall")))
                continue
            # wheel-zoom falls through so you can zoom while sizing a box

        # Drag-box selection: track the cursor and finish on release. Not
        # exclusive — panning / zoom below keep working mid-drag.
        if marquee.active:
            if event.type == pygame.MOUSEMOTION:
                marquee.on_move(event.pos, camera)
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                marquee.on_move(event.pos, camera)
                marquee.finish(scene)

        # Any direct camera input interrupts an in-progress ride.
        if event.type in (pygame.MOUSEWHEEL, pygame.MOUSEBUTTONDOWN):
            camera.cancel_ride()

        if event.type == pygame.MOUSEWHEEL:
            if inspector.scroll_list(mouse_pos, event.y):
                pass          # wheel over the inspector's list scrolls it instead of zooming
            else:
                # LOD lives in each object's draw(), so no post-zoom sync needed.
                camera.zoom_at(getattr(event, "pos", mouse_pos), 1 + event.y * canvas.SCROLL_STEP)

        elif event.type == pygame.KEYDOWN and event.key == pygame.K_c and not app.building:
            # C: drop a copy of the selection at the mouse cursor.
            new = canvas.copy_object(scene.selected, at=camera.screen_to_world(mouse_pos))
            if new is not None:
                app.place(new)

        elif event.type == pygame.KEYDOWN and event.key == pygame.K_DELETE and not app.building:
            handle_menu_command("delete_node")   # same confirm as Edit ▸ Delete

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            panning = True
            right_down = event.pos
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 3:
            panning = False
            if (right_down is not None and not app.building
                    and (pygame.Vector2(event.pos) - right_down).length() <= RIGHT_CLICK_SLOP):
                open_context_menu(event.pos)      # a right-click, not a right-drag pan
            right_down = None
        elif event.type == pygame.MOUSEMOTION and panning:
            camera.pan(*event.rel)

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            consumed, command = menubar.handle_click(event.pos, screen.get_size())
            if command:
                handle_menu_command(command)
            if not consumed:
                if scene.selected is not None and inspector.contains(event.pos, screen.get_size()):
                    # Click landed on the inspector overlay — never the canvas.
                    callback = inspector.handle_click(event.pos)
                    if callback is not None:
                        callback(app)
                else:
                    clicked = scene.hit_test(event.pos, camera)
                    # Ctrl+click just selects — e.g. an input you'd otherwise press.
                    interact = not pygame.key.get_mods() & pygame.KMOD_CTRL
                    if clicked is not None and interact and clicked.on_click(event.pos, app):
                        held = clicked    # it handled the click (e.g. a press): it gets drag/release
                    else:
                        scene.select_only(clicked)   # deselects all if clicked is None
                        if clicked is None:
                            marquee.begin(event.pos, camera)   # maybe a drag-box

                        now = pygame.time.get_ticks()
                        double = (clicked is not None and clicked is last_click_shape
                                  and now - last_click_time <= canvas.DOUBLE_CLICK_MS)
                        if double:
                            if clicked.center is not None:    # Go To centers on it
                                camera.look_at(clicked.center, canvas.GOTO_ZOOM, screen.get_size())
                            last_click_time, last_click_shape = 0, None
                        else:
                            last_click_time, last_click_shape = now, clicked

    # Arrow keys move every selected object (screen-space speed, so /zoom).
    # Suspended while a modal is capturing keystrokes or a build mode is on.
    if app.modal is None and not app.building:
        keys = pygame.key.get_pressed()
        move_dx = keys[pygame.K_RIGHT] - keys[pygame.K_LEFT]
        move_dy = keys[pygame.K_DOWN] - keys[pygame.K_UP]
        movers = [o for o in scene.selection if o.movable]
        is_moving = bool(move_dx or move_dy) and bool(movers)
        if is_moving:
            camera.cancel_ride()          # moving something interrupts a ride too
            step = canvas.MOVE_SPEED * dt / camera.zoom
            for o in movers:
                o.move(move_dx * step, move_dy * step)
        elif was_moving:
            scene.save()      # settle point: save once on release, not per frame
        was_moving = is_moving

    camera.update_ride(dt, screen.get_size())   # advance an in-progress ride
    if placer.active:                           # held object sticks to the cursor (even mid-zoom)
        placer.follow(camera.screen_to_world(mouse_pos))
    scene.update(mouse_pos, mouse_pressed, camera, dt)

    screen.fill(canvas.BG_COLOR)
    scene.draw(screen, camera)
    builder.draw(screen, camera, scene)
    wall_builder.draw(screen, camera)
    marquee.draw(screen, camera)
    placer.draw(screen, camera)
    picker.draw(screen, camera, scene, mouse_pos)
    menubar.draw(screen)
    inspector.draw(screen, scene.selected, app)
    if app.modal is not None:
        app.modal.draw(screen)
    pygame.display.flip()
    dt = clock.tick(60) / 1000.0

scene.remember_view(screen.get_size())  # save where you are, for next launch
scene.save()
pygame.quit()
