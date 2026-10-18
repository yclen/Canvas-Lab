"""game_loop.py — the thin runner.

Just wiring: pygame/window setup, the home screen, then the main loop that
pumps events into the scene, updates, and draws. No app logic lives here —
every class and helper comes from canvas.py (and the object catalog in
things.py). If you find yourself writing real behavior in this file, it
belongs in canvas.py instead; and nothing here should mention a specific
object type.

The window opens to the home screen (canvas.HomeScreen):
- Join world: the world runs on its own in world_server.py; this window gets a
  copy of it (world_link) and draws it, and everything you change is sent to
  the world to do (canvas.JoinedApp) — the loop below is the same either way.
- Work on my own: the canvas as it always was — this window loads Nodes.json,
  ticks and saves it. Only while no world is running.
"""

import ctypes
import os
import platform
import sys

import pygame

import canvas  # all the classes/helpers live here
import instruments  # the link to the instrument server (devices)
import things  # noqa: F401 — defining the catalog's classes registers them
import world_link  # the link to the world (world_server.py)


# ---- Setup -----------------------------------------------------------------
if platform.system() == "Windows":
    # Distinct AppUserModelID so Windows uses our taskbar icon, not python's.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "Yaakov.CanvasApp"
    )

current_scene = None        # what a crash saves (only a scene this window owns saves anything)


def _on_crash(exc_type, exc, tb):
    """Uncaught error: log it to errors.log (there's no console) and save the
    canvas before the app closes."""
    canvas.report_error("crash", exc)
    try:
        if current_scene is not None:
            current_scene.save()
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _on_crash
pygame.init()

icon = pygame.image.load(os.path.join(canvas.ASSETS_DIR, "icon.ico"))
pygame.display.set_icon(icon)
_desktop = (pygame.display.get_desktop_sizes() or [(1280, 800)])[0]
pygame.display.set_mode((min(1180, int(_desktop[0] * 0.85)), min(740, int(_desktop[1] * 0.8))), pygame.RESIZABLE)
pygame.display.set_caption(canvas.APP_NAME)
clock = pygame.time.Clock()

RIGHT_CLICK_SLOP = 5        # px the mouse may move during a right-click before it counts as a pan


def play(scene, player, link=None):
    """Run the canvas on `scene` as `player` until the window closes ("quit")
    or, in the world, the world goes away ("home"). With a link, this window
    is in the world: it applies what the world sends instead of ticking, and
    its App (JoinedApp) sends every change to the world."""
    global current_scene
    current_scene = scene
    screen = pygame.display.get_surface()
    camera = player.camera
    player.restore_view(screen.get_size())  # reopen where you were at last close
    if link is None:                        # what object hooks / callbacks receive
        app = canvas.App(player, screen.get_size())
    else:
        app = canvas.JoinedApp(player, screen.get_size(), link)
    builder = app.train_builder         # interactive train placement mode
    wall_builder = app.wall_builder     # drag-to-draw wall placement mode
    placer = app.placer                 # holding a picked object, to drop with a click
    picker = app.picker                 # waiting for a click on an object (e.g. a readout's source)
    menubar = canvas.MenuBar()
    inspector = canvas.InspectorPanel()
    marquee = canvas.MarqueeSelect()    # left-drag on empty canvas selects many
    presence = canvas.OtherPlayers(link.name) if link else None    # their cursors / views, drawn
    sent_presence, presence_at = None, 0.0     # what this window last told the world about where it is
    pygame.display.set_caption(f"{canvas.APP_NAME} — {player.name}" if link else canvas.APP_NAME)

    panning = False
    right_down = None           # where the right button went down: a click (not a pan) opens the menu
    dt = 0.0
    was_moving = False          # tracks arrow-key movement so we save on release
    last_click_time = 0         # for hand-rolled double-click detection
    last_click_shape = None
    key_repeat_on = False       # held keys repeat only while a text box is open
    held = None                 # object that took the last left-click: gets drag/release until the button's up
    sent_selection = None       # the selection last told to the world

    def open_context_menu(pos):
        """Right-click menu: for the selection (right-clicking an unselected
        object selects just it first) — one object gets the same menu as its
        panel's ⋯ button. Nothing selected, nothing opens."""
        if pos[1] <= canvas.MENU_BAR_HEIGHT or (player.selected is not None
                                                and inspector.contains(pos, screen.get_size())):
            return
        clicked = scene.hit_test(pos, camera)
        if clicked is not None and clicked.selectable and not clicked.is_selected:
            player.select_only(clicked)
        objs = player.selection
        if len(objs) == 1:
            app.object_menu(objs[0], pos)
        elif objs:
            app.selection_menu(objs, pos)

    # ---- Main loop ---------------------------------------------------------
    while True:
        mouse_pos = pygame.mouse.get_pos()
        mouse_pressed = pygame.mouse.get_pressed()[0]
        app.screen_size = screen.get_size()

        # In the world: take in what it sent (changes, who's here), or go home if it's gone.
        if link is not None:
            for msg in link.poll():
                op = msg.get("op")
                if op == "changes":
                    world_link.apply_changes(scene, msg, canvas.object_from_dict)
                    player.refresh()                # new objects you've selected show it
                elif op == "ui":
                    app.handle_ui(msg)              # object code in the world wants to show something
                elif op == "players":
                    link.roster = msg.get("players", [])    # who's in the world (the list at the top-left)
                elif op == "presence":
                    presence.update(msg.get("players", []))
                elif op == "instruments":
                    instruments.client.mirror(msg)
                elif op == "stopping":
                    link.reason = "The world was stopped."
                    link.closed = True
            if link.closed:
                player.remember_view(screen.get_size())
                return "home"

        # Key-repeat on while typing (held Backspace / arrows keep going), off on
        # the canvas (so holding C or Delete doesn't fire over and over).
        want_repeat = isinstance(app.modal, canvas.TextInputModal)
        if want_repeat != key_repeat_on:
            pygame.key.set_repeat(*(canvas.TEXT_KEY_REPEAT if want_repeat else ()))
            key_repeat_on = want_repeat

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                player.remember_view(screen.get_size())  # save where you are (player.json), for next launch
                return "quit"
            if event.type == pygame.VIDEORESIZE:
                screen = pygame.display.set_mode((event.w, event.h), pygame.RESIZABLE)
                app.screen_size = screen.get_size()
                continue

            # An object that handled a left-click holds the mouse until release:
            # motion goes to its on_drag (a slider follows the cursor), and the
            # release to on_release. Driven by events, so it's exact and immediate.
            if held is not None:
                if event.type == pygame.MOUSEMOTION:
                    app.drag(held, event.pos)
                    continue
                if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    obj, held = held, None
                    app.release(obj, event.pos)
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
                    if player.selected is not None and inspector.contains(event.pos, screen.get_size()):
                        picker.cancel()               # a click on the panel isn't a pick
                    else:
                        picker.click(scene, event.pos, camera)
                    continue

            # A wire in hand (canvas.WireMode): dragged, it links where it's let go;
            # clicked, the next click links it or lays a relay; right-click / Esc lets go.
            wire = app.wire
            if wire.active:
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    wire.cancel()
                    continue
                if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and wire.press is not None:
                    press, wire.press = wire.press, None
                    if (pygame.Vector2(event.pos) - press).length() > canvas.WIRE_DRAG_SLOP:
                        wire.release(app, event.pos)
                    continue
                if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                        and event.pos[1] > canvas.MENU_BAR_HEIGHT and not menubar.open_menu):
                    if player.selected is not None and inspector.contains(event.pos, screen.get_size()):
                        wire.cancel()                 # a click on the panel lets go of it
                    else:
                        wire.click(app, event.pos, keep=bool(pygame.key.get_mods() & pygame.KMOD_SHIFT))
                    continue
                if (event.type == pygame.MOUSEBUTTONUP and event.button == 3 and right_down is not None
                        and (pygame.Vector2(event.pos) - right_down).length() <= RIGHT_CLICK_SLOP):
                    wire.cancel()                     # a right-click lets go (a right-drag still pans)
                    panning, right_down = False, None
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
                    placer.place(camera.screen_to_world(event.pos), player, keep_going)
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
                        app.add([train])
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
                                lambda text, box=box: app.add([canvas.Wall(*box, text.strip() or "Wall")]))
                    continue
                # wheel-zoom falls through so you can zoom while sizing a box

            # Drag-box selection: track the cursor and finish on release. Not
            # exclusive — panning / zoom below keep working mid-drag.
            if marquee.active:
                if event.type == pygame.MOUSEMOTION:
                    marquee.on_move(event.pos, camera)
                elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    marquee.on_move(event.pos, camera)
                    marquee.finish(player)

            # Any direct camera input interrupts an in-progress ride.
            if event.type in (pygame.MOUSEWHEEL, pygame.MOUSEBUTTONDOWN):
                camera.cancel_ride()

            if event.type == pygame.MOUSEWHEEL:
                if inspector.scroll(mouse_pos, event.y):
                    pass          # wheel over the inspector scrolls it instead of zooming
                elif ((under := scene.hit_test(mouse_pos, camera)) is not None
                        and under.on_wheel(mouse_pos, event.y, app)):
                    pass          # an object that scrolls (a data collector's rows) took it
                else:
                    # LOD lives in each object's draw(), so no post-zoom sync needed.
                    camera.zoom_at(getattr(event, "pos", mouse_pos), 1 + event.y * canvas.SCROLL_STEP)

            elif event.type == pygame.KEYDOWN and event.key == pygame.K_c and not app.building:
                # C: drop a copy of the selection at the mouse cursor.
                new = canvas.copy_object(player.selected, at=camera.screen_to_world(mouse_pos))
                if new is not None:
                    app.place(new)

            elif event.type == pygame.KEYDOWN and event.key == pygame.K_DELETE and not app.building:
                app.menu_command("delete_node")   # deletes at once, like Edit ▸ Delete

            elif (event.type == pygame.KEYDOWN and event.key == pygame.K_z
                    and event.mod & pygame.KMOD_CTRL and not app.building):
                app.menu_command("undo")          # bring back the last delete

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
                    app.menu_command(command)
                if not consumed:
                    if player.selected is not None and inspector.contains(event.pos, screen.get_size()):
                        # Click landed on the inspector overlay — never the canvas.
                        hot = inspector.hot_at(event.pos)
                        if hot is not None:
                            app.panel_click(player.selected, hot)
                    else:
                        clicked = scene.hit_test(event.pos, camera)
                        # Ctrl+click just selects — e.g. an input you'd otherwise press.
                        interact = not pygame.key.get_mods() & pygame.KMOD_CTRL
                        nub = scene.give_at(event.pos, camera) if interact else None
                        if clicked is None and interact:      # an input's dot, just off its object's edge
                            dot = scene.take_at(event.pos, camera)
                            clicked = dot[0] if dot is not None else None
                        if nub is not None:
                            app.hold_wire("out", *nub)           # picked up an output's wire
                        elif clicked is not None and interact and app.click(clicked, event.pos):
                            # it handled the click (e.g. a press): it gets drag/release — unless
                            # the click picked up a wire (an input's chip), which gets them instead
                            held = None if app.wire.active else clicked
                        else:
                            player.select_only(clicked)   # deselects all if clicked is None
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

        # Arrow keys / WASD move every selected object (screen-space speed, so /zoom).
        # Suspended while a modal is capturing keystrokes or a build mode is on;
        # WASD also while Ctrl is down, so a Ctrl shortcut never moves anything.
        if app.modal is None and not app.building:
            keys = pygame.key.get_pressed()
            wasd = not pygame.key.get_mods() & pygame.KMOD_CTRL
            move_dx = (bool(keys[pygame.K_RIGHT] or wasd and keys[pygame.K_d])
                       - bool(keys[pygame.K_LEFT] or wasd and keys[pygame.K_a]))
            move_dy = (bool(keys[pygame.K_DOWN] or wasd and keys[pygame.K_s])
                       - bool(keys[pygame.K_UP] or wasd and keys[pygame.K_w]))
            movers = [o for o in player.selection if o.movable]
            is_moving = bool(move_dx or move_dy) and bool(movers)
            if is_moving:
                camera.cancel_ride()          # moving something interrupts a ride too
                step = canvas.MOVE_SPEED * dt / camera.zoom
                app.move_objects(movers, move_dx * step, move_dy * step)
            elif was_moving:
                app.moved()       # settle point: save once on release, not per frame
            was_moving = is_moving

        camera.update_ride(dt, screen.get_size())   # advance an in-progress ride
        if placer.active:                           # held object sticks to the cursor (even mid-zoom)
            placer.follow(camera.screen_to_world(mouse_pos))
        if link is None:
            scene.tick(dt)                          # the world moves on (in the world, it does that itself)
        else:
            ids = sorted(o.id for o in player.selection)
            if ids != sent_selection:               # tell the world what you've selected
                link.send({"op": "select", "ids": ids})
                sent_selection = ids
            # ... and where you are: your cursor and the area your window shows (world units)
            now = pygame.time.get_ticks() / 1000.0
            if now - presence_at >= canvas.PRESENCE_SEND_S:
                w, h = screen.get_size()
                left, top = camera.screen_to_world((0, 0))
                cursor = camera.screen_to_world(mouse_pos) if pygame.mouse.get_focused() else None
                here = {"cursor": [round(c, 1) for c in cursor] if cursor else None,
                        "view": [round(left, 1), round(top, 1), round(w / camera.zoom, 1), round(h / camera.zoom, 1)],
                        "down": bool(mouse_pressed)}
                if here != sent_presence:
                    link.send(dict(here, op="presence"))
                    sent_presence, presence_at = here, now
            presence.tick(dt)
        player.hover(mouse_pos, mouse_pressed)      # this view: what the mouse is over

        screen.fill(canvas.BG_COLOR)
        player.draw(screen)
        if presence is not None:
            presence.draw(screen, camera, scene)    # the other players: views, selections, cursors
        builder.draw(screen, camera, scene)
        wall_builder.draw(screen, camera)
        marquee.draw(screen, camera)
        placer.draw(screen, camera)
        picker.draw(screen, camera, scene, mouse_pos)
        app.wire.draw(screen, camera, scene, mouse_pos)
        if app.modal is None and not app.building and not (player.selected is not None
                                                           and inspector.contains(mouse_pos, screen.get_size())):
            canvas.draw_port_hover(screen, camera, scene, mouse_pos)   # a ring on the port under the mouse
        menubar.draw(screen)
        inspector.draw(screen, player.selected, app)
        if link is not None:
            # the player list, top-left — below a mode's hint while one is showing
            top = canvas.MENU_BAR_HEIGHT + (48 if app.building else 8)
            canvas.draw_world_roster(screen, link.name, link.roster, top)
        if app.modal is not None:
            app.modal.draw(screen)
        pygame.display.flip()
        dt = clock.tick(60) / 1000.0


# ---- Home screen, then the canvas ------------------------------------------
message = ""
while True:
    choice = canvas.HomeScreen(message).run()
    message = ""
    if choice[0] == "quit":
        break
    if choice[0] == "solo":
        lock = world_link.hold_solo_lock()       # the world won't start while this is held
        if lock is None:
            message = "Another window is already working on its own."
            continue
        scene = canvas.Scene.load()              # this window owns the world: loads, ticks, saves it
        instruments.client.start()               # connects to instrument_server.py in the background
        play(scene, canvas.Player(scene, choice[1]))
        scene.save()
        lock.close()
        break                                    # closing the window closes the app, as always
    _, link, welcome = choice                    # joined the world
    scene = canvas.Scene.copy_of_world(welcome)
    instruments.client.mirror(welcome.get("instruments") or {})
    result = play(scene, canvas.Player(scene, link.name), link)
    current_scene = None
    message = link.reason if result == "home" else ""
    link.close()
    instruments.client.mirror({})
    pygame.display.set_caption(canvas.APP_NAME)
    if result == "quit":
        break

pygame.quit()
