from unittest import mock

from django.test import SimpleTestCase, override_settings

from . import pdf_convert


class FindLibreOfficeTests(SimpleTestCase):
    @override_settings(LIBREOFFICE_PATH=r"D:\Tools\LO\soffice.exe")
    def test_configured_path_wins_when_it_exists(self):
        with mock.patch("os.path.isfile", return_value=True):
            self.assertEqual(pdf_convert.find_libreoffice(), r"D:\Tools\LO\soffice.exe")

    @override_settings(LIBREOFFICE_PATH=r"D:\Nope\soffice.exe")
    def test_configured_path_that_does_not_exist_is_an_error_not_a_silent_fallback(self):
        with mock.patch("os.path.isfile", return_value=False), mock.patch(
            "shutil.which", return_value="/usr/bin/libreoffice"
        ):
            with self.assertRaisesMessage(RuntimeError, "BDH_HRIS_LIBREOFFICE_PATH"):
                pdf_convert.find_libreoffice()

    @override_settings(LIBREOFFICE_PATH="")
    def test_falls_back_to_soffice_on_path(self):
        def fake_which(name):
            return r"C:\Tools\soffice.exe" if name == "soffice" else None

        with mock.patch("shutil.which", side_effect=fake_which):
            self.assertEqual(pdf_convert.find_libreoffice(), r"C:\Tools\soffice.exe")

    @override_settings(LIBREOFFICE_PATH="")
    def test_falls_back_to_windows_install_folder(self):
        wanted = pdf_convert._WINDOWS_DEFAULTS[0]
        with mock.patch("shutil.which", return_value=None), mock.patch(
            "os.path.isfile", side_effect=lambda p: p == wanted
        ):
            self.assertEqual(pdf_convert.find_libreoffice(), wanted)

    @override_settings(LIBREOFFICE_PATH="")
    def test_not_found_gives_an_actionable_message(self):
        with mock.patch("shutil.which", return_value=None), mock.patch(
            "os.path.isfile", return_value=False
        ):
            with self.assertRaisesMessage(RuntimeError, "Install LibreOffice"):
                pdf_convert.find_libreoffice()
