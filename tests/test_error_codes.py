import ast
from pathlib import Path
import unittest


class ErrorCodeCatalogTests(unittest.TestCase):
    def test_catalog_has_unique_codes_one_through_twenty_two(self):
        from app import error_codes

        values = [
            value
            for name, value in vars(error_codes).items()
            if name.isupper() and value not in {"OK", "XX"}
        ]

        self.assertEqual(sorted(map(int, values)), list(range(1, 23)))
        self.assertEqual(len(values), len(set(values)))

    def test_same_message_causes_have_distinct_codes(self):
        from app.error_codes import (
            CLIENT_INVALID_PORT,
            CLIENT_PASSWORD_REQUIRED,
            SERVER_INVALID_PORT,
            SERVER_PASSWORD_REQUIRED,
        )

        self.assertNotEqual(SERVER_INVALID_PORT, CLIENT_INVALID_PORT)
        self.assertNotEqual(SERVER_PASSWORD_REQUIRED, CLIENT_PASSWORD_REQUIRED)

    def test_client_code_uses_uppercase_first_initial(self):
        from app.error_codes import format_error_code

        self.assertEqual(
            format_error_code("14", client_name="  xavier", client_specific=True),
            "14.X",
        )
        self.assertEqual(
            format_error_code("19", client_name="bedroom", client_specific=True),
            "19.B",
        )

    def test_client_code_uses_x_when_name_is_unavailable(self):
        from app.error_codes import format_error_code

        self.assertEqual(
            format_error_code("14", client_name=None, client_specific=True),
            "14.X",
        )

    def test_invalid_codes_fall_back_to_xx(self):
        from app.error_codes import format_error_code

        for value in (None, "", "bad", 0, -1, True):
            with self.subTest(value=value):
                self.assertEqual(format_error_code(value), "XX")

    def test_every_production_status_call_supplies_error_code(self):
        root = Path(__file__).resolve().parents[1]
        missing = []
        for relative in ("app/gui.py", "app/remote_view.py"):
            tree = ast.parse((root / relative).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr != "_set_status":
                    continue
                if not any(item.arg == "error_code" for item in node.keywords):
                    missing.append(f"{relative}:{node.lineno}")

        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
