"""Endpoint glue between one desktop bridge and authenticated Conduit control."""

import logging

from .protocol import BrowserHandoffProtocolError, validate_result


logger = logging.getLogger(__name__)


def _request_id(message):
    value = message.get("request_id")
    return value if isinstance(value, str) and len(value) == 32 and all(c in "0123456789abcdef" for c in value) else "invalid"


def report_source_result(message):
    """Report an authenticated router result without opening or replaying tabs."""
    if type(message) is not dict or message.get("type") != "browser_handoff_result":
        return False
    try:
        result = validate_result({key: value for key, value in message.items() if key != "type"})
    except BrowserHandoffProtocolError:
        return False
    logger.info("browser_handoff stage=transfer_result request=%s status=%s opened=%d total=%d",
                result.request_id, result.status, result.opened_count, result.total_count)
    return True


class BrowserHandoffEndpoint:
    """Expose no browser authority to the network beyond an opaque capability."""

    def __init__(self, *, machine_id, desktop, send_control):
        if not isinstance(machine_id, str) or not machine_id:
            raise ValueError("machine_id is invalid")
        self.machine_id = machine_id
        self.desktop = desktop
        self.send_control = send_control
        self._capabilities = {}
        desktop.on_capability = self._announce_capability
        desktop.on_result = self._forward_result

    def on_control_message(self, message):
        if type(message) is dict and message.get("type") == "browser_handoff_result":
            return report_source_result(message)
        if type(message) is not dict or message.get("type") != "browser_handoff_request":
            return False
        instance = message.get("browser_instance_id")
        request = message.get("request")
        if type(instance) is not str or type(request) is not dict:
            return False
        accepted = bool(self.desktop.submit_receiver_request(instance, request))
        logger.info("browser_handoff stage=receiver_request_received request=%s bridge_accepted=%s",
                    _request_id(request), accepted)
        return accepted

    def _announce_capability(self, browser_instance_id, receiver_epoch):
        if not all(isinstance(value, str) and value for value in (browser_instance_id, receiver_epoch)):
            return False
        self._capabilities[browser_instance_id] = receiver_epoch
        return self._send_capability(browser_instance_id, receiver_epoch)

    def announce_all(self):
        delivered = False
        for instance, epoch in tuple(self._capabilities.items()):
            delivered = self._send_capability(instance, epoch) or delivered
        return delivered

    def _send_capability(self, instance, epoch):
        sent = self._send(self._capability_message(instance, epoch))
        logger.info("browser_handoff stage=capability_sent sent=%s", sent)
        return sent

    @staticmethod
    def _capability_message(browser_instance_id, receiver_epoch):
        return {
            "type": "browser_handoff_capabilities",
            "browser_instance_id": browser_instance_id,
            "receiver_epoch": receiver_epoch,
        }

    def _forward_result(self, browser_instance_id, message):
        if type(message) is not dict or message.get("type") != "browser_handoff_result":
            return False
        # browser_instance_id is authoritative only within the local bridge;
        # the Server verifies the receiving authenticated session and epoch.
        sent = self._send(dict(message))
        logger.info("browser_handoff stage=receiver_result_sent request=%s sent=%s",
                    _request_id(message), sent)
        return sent

    def _send(self, message):
        try:
            return bool(self.send_control(message))
        except Exception:
            return False
