import struct
import unittest

import pythoncom
import win32clipboard
import winerror
from win32com.server.exception import COMException
import tempfile
import gc
from pathlib import Path
from unittest.mock import patch

import pythoncom
from win32com.server import util

from app.windows_virtual_files import (
    FILEDESCRIPTORW_SIZE,
    VirtualFile,
    VirtualFileDataObject,
    VirtualFileSet,
    build_file_group_descriptor,
    parse_file_group_descriptor,
    open_file_stream,
    open_callback_stream,
    CallbackStream,
)


DROPEFFECT_NONE = 0
DROPEFFECT_COPY = 1


class FileGroupDescriptorTests(unittest.TestCase):
    def test_builds_unicode_descriptors_with_size_and_attributes(self):
        files = [
            VirtualFile("notes.txt", 5, lambda: b"hello"),
            VirtualFile("資料/report.csv", 4, lambda: b"a,b\n"),
        ]

        packed = build_file_group_descriptor(files)
        parsed = parse_file_group_descriptor(packed)

        self.assertEqual(struct.unpack_from("<I", packed)[0], 2)
        self.assertEqual(len(packed), 4 + (2 * FILEDESCRIPTORW_SIZE))
        self.assertEqual(
            [(item.name, item.size, item.is_directory) for item in parsed],
            [("notes.txt", 5, False), ("資料/report.csv", 4, False)],
        )

    def test_rejects_names_that_do_not_fit_windows_descriptor(self):
        with self.assertRaisesRegex(ValueError, "259 characters"):
            VirtualFile("x" * 260, 0, lambda: b"")

    def test_directory_descriptor_has_no_file_contents(self):
        files = [VirtualFile("folder", 0, None, is_directory=True)]
        file_set = VirtualFileSet(files)

        descriptor = parse_file_group_descriptor(file_set.descriptor_bytes())[0]

        self.assertTrue(descriptor.is_directory)
        with self.assertRaisesRegex(ValueError, "directory"):
            file_set.content_bytes(0)


class VirtualFileSetTests(unittest.TestCase):
    def test_returns_content_by_descriptor_index(self):
        file_set = VirtualFileSet(
            [
                VirtualFile("first.txt", 3, lambda: b"one"),
                VirtualFile("second.txt", 3, lambda: b"two"),
            ]
        )

        self.assertEqual(file_set.content_bytes(0), b"one")
        self.assertEqual(file_set.content_bytes(1), b"two")

    def test_rejects_content_whose_size_changed(self):
        file_set = VirtualFileSet([VirtualFile("changed.txt", 3, lambda: b"longer")])

        with self.assertRaisesRegex(ValueError, "expected 3 bytes"):
            file_set.content_bytes(0)

    def test_rejects_invalid_stream_index(self):
        file_set = VirtualFileSet([VirtualFile("one.txt", 1, lambda: b"1")])

        with self.assertRaises(IndexError):
            file_set.content_bytes(1)


class VirtualFileDataObjectTests(unittest.TestCase):
    def test_performed_drop_notification_is_accepted_and_reported(self):
        observed = []
        data_object = VirtualFileDataObject(
            VirtualFileSet([VirtualFile("a.txt", 1, lambda: b"a")]),
            on_performed_drop=lambda *effects: observed.extend(effects),
        )
        performed_drop = win32clipboard.RegisterClipboardFormat("Performed DropEffect")
        medium = pythoncom.STGMEDIUM()
        medium.set(pythoncom.TYMED_HGLOBAL, struct.pack("<I", DROPEFFECT_NONE))

        data_object.SetData(
            (performed_drop, None, pythoncom.DVASPECT_CONTENT, -1, pythoncom.TYMED_HGLOBAL),
            medium,
            False,
        )

        self.assertEqual(observed, [DROPEFFECT_NONE])

    def test_preferred_copy_effect_is_advertised_and_returned(self):
        data_object = VirtualFileDataObject(
            VirtualFileSet([VirtualFile("a.txt", 1, lambda: b"a")])
        )

        preferred_format = getattr(data_object, "preferred_drop_format_etc", None)
        self.assertIsNotNone(preferred_format)
        format_etc = preferred_format()
        data_object.QueryGetData(format_etc)
        medium = data_object.GetData(format_etc)
        formats = list(data_object.EnumFormatEtc(pythoncom.DATADIR_GET))

        self.assertEqual(medium.tymed, pythoncom.TYMED_HGLOBAL)
        value = bytes(medium.data)
        self.assertEqual(struct.unpack("<I", value[:4])[0], DROPEFFECT_COPY)
        self.assertFalse(any(value[4:]))
        self.assertIn(format_etc, formats)

    def test_malformed_performed_drop_is_rejected_without_callback(self):
        observed = []
        data_object = VirtualFileDataObject(
            VirtualFileSet([VirtualFile("a.txt", 1, lambda: b"a")]),
            on_performed_drop=observed.append,
        )
        medium = pythoncom.STGMEDIUM()
        medium.set(pythoncom.TYMED_HGLOBAL, b"\x01\x00")
        performed_format = getattr(data_object, "performed_drop_format_etc", None)
        self.assertIsNotNone(performed_format)

        with self.assertRaises(COMException) as raised:
            data_object.SetData(
                performed_format(), medium, False
            )

        self.assertEqual(raised.exception.hresult, winerror.DV_E_FORMATETC)
        self.assertEqual(observed, [])

    def test_non_copy_performed_effect_is_rejected(self):
        observed = []
        data_object = VirtualFileDataObject(
            VirtualFileSet([VirtualFile("a.txt", 1, lambda: b"a")]),
            on_performed_drop=observed.append,
        )
        medium = pythoncom.STGMEDIUM()
        medium.set(pythoncom.TYMED_HGLOBAL, struct.pack("<I", 2))
        performed_format = getattr(data_object, "performed_drop_format_etc", None)
        self.assertIsNotNone(performed_format)

        with self.assertRaises(COMException) as raised:
            data_object.SetData(
                performed_format(), medium, False
            )

        self.assertEqual(raised.exception.hresult, winerror.DV_E_FORMATETC)
        self.assertEqual(observed, [])

    def setUp(self):
        pythoncom.OleInitialize()
        self.addCleanup(pythoncom.CoUninitialize)
        self.file_set = VirtualFileSet(
            [
                VirtualFile("first.txt", 3, lambda: b"one"),
                VirtualFile("second.txt", 3, lambda: b"two"),
            ]
        )
        self.data_object = VirtualFileDataObject(self.file_set)

    def test_com_gateway_returns_descriptor_hglobal(self):
        wrapped = util.wrap(self.data_object, pythoncom.IID_IDataObject)
        format_etc = self.data_object.descriptor_format_etc()

        wrapped.QueryGetData(format_etc)
        medium = wrapped.GetData(format_etc)

        self.assertEqual(medium.tymed, pythoncom.TYMED_HGLOBAL)
        self.assertEqual(parse_file_group_descriptor(medium.data)[-1].name, "second.txt")

    def test_com_gateway_returns_indexed_content_stream(self):
        wrapped = util.wrap(self.data_object, pythoncom.IID_IDataObject)

        medium = wrapped.GetData(self.data_object.content_format_etc(1))

        self.assertEqual(medium.tymed, pythoncom.TYMED_ISTREAM)
        self.assertEqual(medium.data.Read(3), b"two")

    def test_data_object_maps_cancelled_stream_open_to_com_error(self):
        class CancelledOpen(OSError):
            winerror = 1223

        file_set = VirtualFileSet([
            VirtualFile(
                "cancelled.bin",
                1,
                None,
                open_stream=lambda: (_ for _ in ()).throw(
                    CancelledOpen("private cancellation detail")
                ),
            )
        ])
        data_object = VirtualFileDataObject(file_set)

        with self.assertRaises(COMException) as raised:
            data_object.GetData(data_object.content_format_etc(0))

        self.assertEqual(raised.exception.hresult, -2147023673)
        self.assertNotIn("private", str(raised.exception))

    def test_com_gateway_enumerates_descriptor_and_contents_formats(self):
        wrapped = util.wrap(self.data_object, pythoncom.IID_IDataObject)

        formats = wrapped.EnumFormatEtc(pythoncom.DATADIR_GET).Next(10)

        self.assertEqual(formats[0], self.data_object.descriptor_format_etc())
        self.assertEqual(formats[1], self.data_object.content_format_etc(0))
        self.assertEqual(formats[2], self.data_object.content_format_etc(1))

    def test_com_gateway_streams_file_without_loading_content_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.bin"
            path.write_bytes(b"0123456789")
            file_set = VirtualFileSet([
                VirtualFile(
                    "large.bin",
                    10,
                    None,
                    open_stream=lambda: open_file_stream(path, 10),
                )
            ])
            data_object = VirtualFileDataObject(file_set)
            wrapped = util.wrap(data_object, pythoncom.IID_IDataObject)

            stream = wrapped.GetData(data_object.content_format_etc(0)).data

            self.assertEqual(stream.Read(4), b"0123")
            stream.Seek(2, 0)
            self.assertEqual(stream.Read(3), b"234")
            del stream, wrapped, data_object
            gc.collect()

    def test_com_gateway_reads_from_growing_callback_stream(self):
        content = b"growing bytes"
        reads = []

        def read_range(offset, count):
            reads.append((offset, count))
            return content[offset:offset + count]

        file_set = VirtualFileSet([
            VirtualFile(
                "remote.bin",
                len(content),
                None,
                open_stream=lambda: open_callback_stream(read_range, len(content)),
            )
        ])
        data_object = VirtualFileDataObject(file_set)
        wrapped = util.wrap(data_object, pythoncom.IID_IDataObject)
        stream = wrapped.GetData(data_object.content_format_etc(0)).data

        self.assertEqual(stream.Read(7), b"growing")
        self.assertEqual(stream.Read(6), b" bytes")
        self.assertEqual(reads, [(0, 7), (7, 6)])

    def test_callback_stream_reports_only_successfully_returned_ranges(self):
        content = b"abcdefghij"
        consumed = []
        stream = CallbackStream(
            lambda offset, count: content[offset:offset + count],
            len(content),
            on_read=lambda offset, count: consumed.append((offset, count)),
        )

        self.assertEqual(stream.Read(4), b"abcd")
        stream.Seek(2, 0)
        self.assertEqual(stream.Read(3), b"cde")
        self.assertEqual(consumed, [(0, 4), (2, 3)])

    def test_callback_stream_maps_intentional_cancel_to_windows_cancel_hresult(self):
        class CancelledRead(OSError):
            winerror = 1223

        stream = CallbackStream(
            lambda offset, count: (_ for _ in ()).throw(CancelledRead("cancelled")),
            10,
        )

        with self.assertRaises(COMException) as raised:
            stream.Read(4)

        self.assertEqual(raised.exception.hresult, -2147023673)

    def test_callback_stream_reports_open_and_close_exactly_once(self):
        events = []
        stream = CallbackStream(
            lambda offset, count: b"data"[offset:offset + count],
            4,
            on_open=lambda: events.append("open"),
            on_close=lambda: events.append("close"),
        )

        stream.close()
        stream.close()

        self.assertEqual(events, ["open", "close"])

    def test_callback_stream_clone_preserves_progress_callback(self):
        content = b"abcdefghij"
        consumed = []
        stream = CallbackStream(
            lambda offset, count: content[offset:offset + count],
            len(content),
            position=4,
            on_read=lambda offset, count: consumed.append((offset, count)),
        )

        clone = stream.Clone()
        self.assertEqual(clone.Read(3), b"efg")
        self.assertEqual(consumed, [(4, 3)])

    def test_callback_stream_clone_preserves_lifetime_callbacks(self):
        on_open = lambda: None
        on_close = lambda: None
        stream = CallbackStream(
            lambda offset, count: b"data"[offset:offset + count],
            4,
            on_open=on_open,
            on_close=on_close,
        )

        with patch("app.windows_virtual_files.open_callback_stream") as opener:
            stream.Clone()

        self.assertIs(opener.call_args.kwargs["on_open"], on_open)
        self.assertIs(opener.call_args.kwargs["on_close"], on_close)


if __name__ == "__main__":
    unittest.main()
