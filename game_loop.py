"""game_loop.py — the thin runner.

Just wiring: pygame/window setup, then the main loop that pumps events into the
scene, updates, and draws. No app logic lives here — every class and helper comes
from canvas.py. If you find yourself writing real behavior in this file, it belongs
in canvas.py instead.
"""

import ctypes
import math
import os
import platform

import pygame

import canvas  # all the classes/helpers live here


# ---- Setup -----------------------------------------------------------------
if platform.system() == "Windows":
    # Distinct AppUserModelID so Windows uses our taskbar icon, not python's.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "Yaakov.CanvasApp"
    )

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
menubar = canvas.MenuBar()
inspector = canvas.InspectorPanel()
builder = canvas.TrainBuilder()     # interactive train placement mode
wall_builder = canvas.WallBuilder() # drag-to-draw wall placement mode
marquee = canvas.MarqueeSelect()    # left-drag on empty canvas selects many
active_modal = None         # a TextInputModal / ConfirmModal, or None

panning = False
dt = 0.0
was_moving = False          # tracks arrow-key movement so we save on release
last_click_time = 0         # for hand-rolled double-click detection
last_click_shape = None
key_repeat_on = False       # held keys repeat only while a text box is open


def handle_menu_command(command):
    """Turn a menu selection into an app action (opening a modal or jumping)."""
    global active_modal
    if command == "add_button":
        active_modal = canvas.TextInputModal("New button text:", purpose="add_button")
    elif command == "add_nametag":
        active_modal = canvas.TextInputModal("New name tag text:", purpose="add_nametag")
    elif command == "edit_name":
        sel = scene.selected
        if isinstance(sel, (canvas.Node, canvas.Wall)):   # things with a name
            active_modal = canvas.TextInputModal("Edit text:", purpose="edit",
                                                 target=sel, value=sel.text)
    elif command == "copy":
        new = canvas.copy_object(scene.selected)   # None for trains / nothing selected
        if new is not None:
            scene.add(new)
            scene.select_only(new)
    elif command == "delete_node":
        sel = scene.selection                   # works for any object (incl. trains)
        if sel:
            title = (f"Delete '{sel[0].display_name}'?" if len(sel) == 1
                     else f"Delete {len(sel)} objects?")
            active_modal = canvas.ConfirmModal(title, "Are you sure you want to delete?",
                                               target=sel)
    elif command == "add_train":
        scene.select_only(None)     # nothing selected while drawing a route
        builder.begin()
    elif command == "add_wall":
        scene.select_only(None)
        wall_builder.begin()
    elif command == "add_agent":
        agent = canvas.spawn_agent(camera, screen.get_size())
        scene.add(agent)
        scene.select_only(agent)
    elif command == "add_neuron":
        neuron = canvas.spawn_neuron(camera, screen.get_size())
        scene.add(neuron)
        scene.select_only(neuron)
    elif command == "goto":
        sel = scene.selected
        if isinstance(sel, canvas.Node):        # goto centers on a node's position
            camera.look_at(sel.world_center, canvas.GOTO_ZOOM, screen.get_size())


def apply_text_modal(modal):
    """Commit a TextInputModal on submit, per its purpose."""
    if modal.purpose == "add_button":
        scene.add(canvas.spawn_button(modal.value, camera, screen.get_size()))
    elif modal.purpose == "add_nametag":
        scene.add(canvas.spawn_nametag(modal.value, camera, screen.get_size()))
    elif modal.purpose == "add_wall":
        rect = getattr(modal, "wall_rect", None)
        if rect is not None:
            x, y, w, h = rect
            scene.add(canvas.Wall(x, y, w, h, modal.value.strip() or "Wall"))
    elif modal.purpose == "edit":
        new_text = modal.value.strip()
        if new_text:
            modal.target.text = new_text
            scene.save()
    elif modal.purpose == "set_command":
        target = modal.target
        command = modal.value.strip()
        if command:
            target.action = "run_command"
            target.params = dict(target.params, command=command)
        else:                       # cleared: node no longer launches anything
            target.action = None
            target.params = {}
        scene.save()
    elif modal.purpose == "set_firing":
        modal.target.set_equation(modal.value)   # restarts the neuron's clock at t = 0
        scene.save()
    elif modal.purpose == "set_weight":
        try:
            value = float(modal.value.strip())
        except ValueError:
            value = None                         # not a number: leave the W unchanged
        if value is not None and math.isfinite(value) and modal.weight_id in modal.target.weights:
            modal.target.weights[modal.weight_id] = value
            scene.save()
    elif modal.purpose == "set_trigger":
        modal.target.set_trigger(modal.value)    # resets the clock; waits for the trigger
        scene.save()
    elif modal.purpose == "set_t_end":
        text = modal.value.strip().lower()
        if text in ("", "inf", "infinity"):
            value = math.inf
        else:
            try:
                value = float(text)
            except ValueError:
                value = None                     # not a number: leave it unchanged
        if value is not None and value >= 0 and not math.isnan(value):
            modal.target.t_end = value           # raising it past t resumes the clock
            scene.save()
    elif modal.purpose == "set_train_speed":
        try:
            value = float(modal.value.strip())
        except ValueError:
            value = None
        if value and value > 0:
            modal.target.speed = value
            scene.save()


# ---- Main loop -------------------------------------------------------------
running = True
while running:
    mouse_pos = pygame.mouse.get_pos()
    mouse_pressed = pygame.mouse.get_pressed()[0]

    # Key-repeat on while typing (held Backspace / arrows keep going), off on
    # the canvas (so holding C or Delete doesn't fire over and over).
    want_repeat = isinstance(active_modal, canvas.TextInputModal)
    if want_repeat != key_repeat_on:
        pygame.key.set_repeat(*(canvas.TEXT_KEY_REPEAT if want_repeat else ()))
        key_repeat_on = want_repeat

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
            continue
        if event.type == pygame.VIDEORESIZE:
            screen = pygame.display.set_mode((event.w, event.h), pygame.RESIZABLE)
            continue

        # A modal, while open, swallows every other event.
        if active_modal is not None:
            result = active_modal.handle_event(event, screen.get_size())
            if result == "submit":
                apply_text_modal(active_modal)
                active_modal = None
            elif result == "confirm":
                target = active_modal.target
                active_modal = None
                scene.delete_many(target)
            elif result == "cancel":
                active_modal = None
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
                    active_modal = canvas.TextInputModal("Wall name:", purpose="add_wall")
                    active_modal.wall_rect = box
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
                pass          # wheel over the neuron's W list scrolls it instead of zooming
            else:
                # LOD lives in each object's draw(), so no post-zoom sync needed.
                camera.zoom_at(getattr(event, "pos", mouse_pos), 1 + event.y * canvas.SCROLL_STEP)

        elif (event.type == pygame.KEYDOWN and event.key == pygame.K_c
                and not builder.active and not wall_builder.active):
            # C: drop a copy of the selection at the mouse cursor.
            new = canvas.copy_object(scene.selected, at=camera.screen_to_world(mouse_pos))
            if new is not None:
                scene.add(new)
                scene.select_only(new)

        elif (event.type == pygame.KEYDOWN and event.key == pygame.K_DELETE
                and not builder.active and not wall_builder.active):
            handle_menu_command("delete_node")   # same confirm as Edit ▸ Delete

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            panning = True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 3:
            panning = False
        elif event.type == pygame.MOUSEMOTION and panning:
            camera.pan(*event.rel)

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            consumed, command = menubar.handle_click(event.pos)
            if command:
                handle_menu_command(command)
            if not consumed:
                selected = scene.selected
                if (isinstance(selected, (canvas.Node, canvas.Train))
                        and inspector.contains(event.pos, screen.get_size())):
                    # Click landed on the inspector overlay — never the canvas.
                    panel_action = inspector.handle_click(event.pos, selected, screen.get_size())
                    if panel_action == "activate":
                        canvas.run_action(selected,
                                          canvas.ActionContext(scene, camera, screen.get_size()))
                    elif panel_action == "set_command":
                        active_modal = canvas.TextInputModal(
                            "Command to run:", purpose="set_command",
                            target=selected, value=selected.params.get("command", ""))
                    elif panel_action == "set_firing":
                        active_modal = canvas.TextInputModal(
                            "Firing equation (e.g. every(1), poisson(0.5)):",
                            purpose="set_firing", target=selected, value=selected.equation,
                            q_pastes=False)          # so 'sqrt' can be typed
                    elif panel_action == "edit_weight":
                        other_id = inspector.clicked_weight
                        other = scene.by_id.get(other_id)
                        name = other.text if other is not None and other.text else other_id
                        active_modal = canvas.TextInputModal(
                            f"W to {name}:", purpose="set_weight", target=selected,
                            value=f"{selected.weights.get(other_id, 0.0):g}")
                        active_modal.weight_id = other_id
                    elif panel_action == "set_trigger":
                        active_modal = canvas.TextInputModal(
                            "Clock trigger, e.g. W > 3 (empty = none):", purpose="set_trigger",
                            target=selected, value=selected.trigger, q_pastes=False)
                    elif panel_action == "edit_t_end":
                        t_end = selected.t_end
                        active_modal = canvas.TextInputModal(
                            "Clock end, in seconds (or INF for never):", purpose="set_t_end",
                            target=selected, value="INF" if math.isinf(t_end) else f"{t_end:g}")
                    elif panel_action in ("spike_down", "spike_up"):
                        step = 1 if panel_action == "spike_up" else -1
                        selected.spike_frames = max(1, selected.spike_frames + step)
                        scene.save()
                    elif panel_action == "adjust_speed":
                        active_modal = canvas.TextInputModal(
                            "Train speed (units/sec):", purpose="set_train_speed",
                            target=selected, value=str(int(selected.speed)))
                else:
                    clicked = next((o for o in scene.objects if o.hit(event.pos, camera)), None)
                    ctx = canvas.ActionContext(scene, camera, screen.get_size())
                    if clicked is not None and clicked.on_click(event.pos, ctx):
                        pass          # object handled it (e.g. train arrow teleport)
                    else:
                        scene.select_only(clicked)   # deselects all if clicked is None
                        if clicked is None:
                            marquee.begin(event.pos, camera)   # maybe a drag-box

                        now = pygame.time.get_ticks()
                        double = (clicked is not None and clicked is last_click_shape
                                  and now - last_click_time <= canvas.DOUBLE_CLICK_MS)
                        if double:
                            if isinstance(clicked, canvas.Node):    # Go To centers on a node
                                camera.look_at(clicked.world_center, canvas.GOTO_ZOOM, screen.get_size())
                            last_click_time, last_click_shape = 0, None
                        else:
                            last_click_time, last_click_shape = now, clicked

    # Arrow keys move every selected object (screen-space speed, so /zoom).
    # Suspended while a modal is capturing keystrokes or a train is being drawn.
    if active_modal is None and not builder.active and not wall_builder.active:
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
    scene.update(mouse_pos, mouse_pressed, camera, dt)

    screen.fill(canvas.BG_COLOR)
    scene.draw(screen, camera)
    builder.draw(screen, camera, scene)
    wall_builder.draw(screen, camera)
    marquee.draw(screen, camera)
    menubar.draw(screen)
    sel = scene.selected
    inspector.draw(screen, sel if isinstance(sel, (canvas.Node, canvas.Train)) else None, screen.get_size())
    if active_modal is not None:
        active_modal.draw(screen)
    pygame.display.flip()
    dt = clock.tick(60) / 1000.0

scene.remember_view(screen.get_size())  # save where you are, for next launch
scene.save()
pygame.quit()
