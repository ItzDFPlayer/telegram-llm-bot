"""
Feature plugins.

Each plugin module exposes a `register(app)` function that wires its own
handlers/jobs onto the Application. Add new features by creating a module
here and appending it to PLUGINS.
"""
from . import basic_commands, whitelist_admin, chat_handler, remember, status

PLUGINS = (basic_commands, whitelist_admin, chat_handler, remember, status)


def register_all(app):
    for plugin in PLUGINS:
        plugin.register(app)
