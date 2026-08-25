import unittest

from app.machine_identity import stable_machine_id


class StableMachineIdentityTests(unittest.TestCase):
    def test_machine_guid_survives_a_windows_computer_rename(self):
        first = stable_machine_id(
            machine_guid="A1B2-C3D4",
            fallback="ParthPC",
        )
        renamed = stable_machine_id(
            machine_guid="A1B2-C3D4",
            fallback="RenamedPC",
        )

        self.assertEqual(first, renamed)

    def test_identity_does_not_expose_the_machine_guid_or_name(self):
        machine_id = stable_machine_id(
            machine_guid="SECRET-GUID",
            fallback="ParthPC",
        )

        self.assertTrue(machine_id.startswith("windows:"))
        self.assertNotIn("SECRET-GUID", machine_id)
        self.assertNotIn("ParthPC", machine_id)

    def test_different_machine_guids_produce_different_identities(self):
        first = stable_machine_id("first-guid", "fallback")
        second = stable_machine_id("second-guid", "fallback")

        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
