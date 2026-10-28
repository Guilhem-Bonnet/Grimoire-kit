"""P76 : la console du dock se redimensionne vers le haut, comme dans VS Code."""

from __future__ import annotations

from playwright.sync_api import Page

from .conftest import open_atelier


def _height(page: Page) -> float:
    return page.evaluate("document.getElementById('dock').getBoundingClientRect().height")


def _drag_up(page: Page, dy: float) -> None:
    box = page.locator("#dock-resize").bounding_box()
    assert box is not None
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y - dy, steps=8)
    page.mouse.up()


def test_drag_grows_dock_and_is_bounded(workspace: Page) -> None:
    open_atelier(workspace)
    before = _height(workspace)
    _drag_up(workspace, 200)
    assert _height(workspace) >= before + 150
    _drag_up(workspace, 5000)
    limit = workspace.evaluate("window.innerHeight") * 0.8
    assert _height(workspace) <= limit + 1


def test_height_survives_reload_and_double_click_resets(workspace: Page) -> None:
    open_atelier(workspace)
    default = _height(workspace)
    _drag_up(workspace, 200)
    chosen = _height(workspace)
    assert chosen > default
    workspace.reload(wait_until="domcontentloaded")
    workspace.wait_for_selector("body[data-ready='1']", timeout=30_000)
    open_atelier(workspace)
    assert abs(_height(workspace) - chosen) <= 1
    workspace.locator("#dock-resize").dblclick()
    assert abs(_height(workspace) - default) <= 1


def test_separator_semantics_and_keyboard(workspace: Page) -> None:
    open_atelier(workspace)
    handle = workspace.locator("#dock-resize")
    assert handle.get_attribute("role") == "separator"
    assert handle.get_attribute("aria-orientation") == "horizontal"
    start = _height(workspace)
    handle.focus()
    workspace.keyboard.press("ArrowUp")
    assert _height(workspace) > start
    grown = _height(workspace)
    workspace.keyboard.press("ArrowDown")
    assert _height(workspace) < grown
