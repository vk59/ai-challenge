#!/usr/bin/env python3
"""День 1: то же самое, но нативным окном macOS.

    ../../.venv/bin/python app.py

Внутри крутится тот же локальный сервер из web.py и та же страница ui.html,
только показывает её системный WebKit в обычном окне Cocoa: своя иконка в доке,
привычные ⌘Q / ⌘W / ⌘N, системная светлая-тёмная тема.

Нужен PyObjC — он ставится в .venv в корне репозитория, см. build_app.sh.
"""

import os
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

from llm import DEFAULT_MODEL, LLMError, load_env
from web import Handler

WINDOW_TITLE = "Первый запрос к LLM"

# PyObjC не удерживает питоновские объекты за нас — если их не сохранить,
# сборщик мусора снесёт окно вместе с делегатами прямо во время работы.
_alive: list = []


def start_server() -> int:
    """Поднимает web.py на свободном порту в фоновом потоке, возвращает порт."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _alive.append(server)
    return server.server_address[1]


class AppDelegate(NSObject):
    def applicationShouldTerminateAfterLastWindowClosed_(self, sender) -> bool:
        return True


class Actions(NSObject):
    """Пункты меню, которым нужен не системный обработчик, а наш."""

    webview = None

    def newConversation_(self, sender) -> None:
        self.webview.evaluateJavaScript_completionHandler_("resetConversation()", None)


def build_menu(actions: Actions) -> None:
    """Стандартное меню macOS. Без него в окне не заработают даже ⌘C / ⌘V."""
    main = NSMenu.alloc().init()

    def submenu(title: str) -> NSMenu:
        holder = NSMenuItem.alloc().init()
        menu = NSMenu.alloc().initWithTitle_(title)
        holder.setSubmenu_(menu)
        main.addItem_(holder)
        return menu

    def item(menu: NSMenu, title: str, selector: str, key: str, target=None) -> None:
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


def build_window(port: int, model: str) -> NSWindow:
    """Собирает окно с WKWebView внутри и загружает в него нашу страницу."""
    style = (
        NSWindowStyleMaskTitled
        | NSWindowStyleMaskClosable
        | NSWindowStyleMaskMiniaturizable
        | NSWindowStyleMaskResizable
    )
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, 820, 700), style, NSBackingStoreBuffered, False
    )
    window.setTitle_(WINDOW_TITLE)
    window.setSubtitle_(model)          # вторая строка в титлбаре, macOS 11+
    window.setMinSize_(NSMakeSize(430, 480))
    window.center()

    config = WKWebViewConfiguration.alloc().init()
    webview = WKWebView.alloc().initWithFrame_configuration_(
        window.contentView().bounds(), config
    )
    webview.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    window.setContentView_(webview)

    url = NSURL.URLWithString_(f"http://127.0.0.1:{port}/?native=1")
    webview.loadRequest_(NSURLRequest.requestWithURL_(url))

    actions = Actions.alloc().init()
    actions.webview = webview
    build_menu(actions)

    _alive.extend([window, webview, actions])
    return window


def build() -> NSWindow:
    """Всё, кроме запуска цикла событий — так это можно проверить тестом."""
    # Проблемы с .env не должны мешать окну открыться: без ключа приложение
    # всё равно запускается и скажет об этом внятно на первом же запросе.
    try:
        load_env()
    except LLMError:
        pass
    model = os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)
    port = start_server()

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)

    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    _alive.append(delegate)

    return build_window(port, model)


def main() -> None:
    window = build()
    window.makeKeyAndOrderFront_(None)

    app = NSApplication.sharedApplication()
    app.activateIgnoringOtherApps_(True)
    app.run()


if __name__ == "__main__":
    main()
