#!/usr/bin/env python3
"""День 11: модель памяти агента в нативном окне macOS.

    ../../.venv/bin/python app.py

Тот же сервер из web.py и та же страница ui.html, только в обычном окне Cocoa.
Три панели памяти справа — главное, ради чего этот день вообще про окно,
а не про скрипт: видно, что куда попадает, прямо по ходу разговора.

Нужен PyObjC, см. build_app.sh.
"""

import threading
from http.server import ThreadingHTTPServer

from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSMenu,
    NSMenuItem,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSURL, NSMakeRect, NSMakeSize, NSObject, NSURLRequest
from WebKit import WKWebView, WKWebViewConfiguration

from web import AGENT, Handler

WINDOW_TITLE = "Модель памяти агента"
_alive: list = []


def start_server() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _alive.append(server)
    return server.server_address[1]


class AppDelegate(NSObject):
    def applicationShouldTerminateAfterLastWindowClosed_(self, sender) -> bool:
        return True


class Actions(NSObject):
    webview = None

    def newConversation_(self, sender) -> None:
        self.webview.evaluateJavaScript_completionHandler_("newConversation()", None)


def build_menu(actions: Actions) -> None:
    main = NSMenu.alloc().init()

    def submenu(title: str) -> NSMenu:
        holder = NSMenuItem.alloc().init()
        menu = NSMenu.alloc().initWithTitle_(title)
        holder.setSubmenu_(menu)
        main.addItem_(holder)
        return menu

    def item(menu, title, selector, key, target=None):
        entry = menu.addItemWithTitle_action_keyEquivalent_(title, selector, key)
        if target is not None:
            entry.setTarget_(target)

    app_menu = submenu("App")
    item(app_menu, "Скрыть", "hide:", "h")
    app_menu.addItem_(NSMenuItem.separatorItem())
    item(app_menu, "Выйти", "terminate:", "q")

    edit_menu = submenu("Правка")
    item(edit_menu, "Отменить", "undo:", "z")
    item(edit_menu, "Повторить", "redo:", "Z")
    edit_menu.addItem_(NSMenuItem.separatorItem())
    item(edit_menu, "Вырезать", "cut:", "x")
    item(edit_menu, "Копировать", "copy:", "c")
    item(edit_menu, "Вставить", "paste:", "v")
    item(edit_menu, "Выделить всё", "selectAll:", "a")

    dialog_menu = submenu("Диалог")
    item(dialog_menu, "Новый диалог", "newConversation:", "n", actions)

    window_menu = submenu("Окно")
    item(window_menu, "Свернуть", "performMiniaturize:", "m")
    item(window_menu, "Закрыть", "performClose:", "w")

    NSApplication.sharedApplication().setMainMenu_(main)


def build_window(port: int, subtitle: str) -> NSWindow:
    style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
             | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, 1080, 720), style, NSBackingStoreBuffered, False)
    window.setTitle_(WINDOW_TITLE)
    window.setSubtitle_(subtitle)
    window.setMinSize_(NSMakeSize(760, 520))
    window.center()

    config = WKWebViewConfiguration.alloc().init()
    webview = WKWebView.alloc().initWithFrame_configuration_(
        window.contentView().bounds(), config)
    webview.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    window.setContentView_(webview)
    webview.loadRequest_(NSURLRequest.requestWithURL_(
        NSURL.URLWithString_(f"http://127.0.0.1:{port}/?native=1")))

    actions = Actions.alloc().init()
    actions.webview = webview
    build_menu(actions)
    _alive.extend([window, webview, actions])
    return window


def build() -> NSWindow:
    subtitle = (f"{AGENT.model} · долговременная память: "
                f"{len(AGENT.memos)} записей")
    port = start_server()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    _alive.append(delegate)
    return build_window(port, subtitle)


def main() -> None:
    window = build()
    window.makeKeyAndOrderFront_(None)
    app = NSApplication.sharedApplication()
    app.activateIgnoringOtherApps_(True)
    app.run()


if __name__ == "__main__":
    main()
