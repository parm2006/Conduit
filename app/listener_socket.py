"""Prevent two Windows Conduit instances from sharing a listening port."""
import socket
import sys


def configure_listener_socket(listener):
    # On Windows SO_REUSEADDR allows a second process to steal traffic from
    # an existing listener. Exclusive binding must be set before bind().
    option = socket.SO_EXCLUSIVEADDRUSE if sys.platform == 'win32' else socket.SO_REUSEADDR
    listener.setsockopt(socket.SOL_SOCKET, option, 1)
