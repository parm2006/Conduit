"""Endpoint glue between one desktop bridge and authenticated Conduit control."""


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
        if type(message) is not dict or message.get("type") != "browser_handoff_request":
            return False
        instance = message.get("browser_instance_id")
        request = message.get("request")
        if type(instance) is not str or type(request) is not dict:
            return False
        return bool(self.desktop.submit_receiver_request(instance, request))

    def _announce_capability(self, browser_instance_id, receiver_epoch):
        if not all(isinstance(value, str) and value for value in (browser_instance_id, receiver_epoch)):
            return False
        self._capabilities[browser_instance_id] = receiver_epoch
        return self._send(self._capability_message(browser_instance_id, receiver_epoch))

    def announce_all(self):
        delivered = False
        for instance, epoch in tuple(self._capabilities.items()):
            delivered = self._send(self._capability_message(instance, epoch)) or delivered
        return delivered

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
        return self._send(dict(message))

    def _send(self, message):
        try:
            return bool(self.send_control(message))
        except Exception:
            return False
