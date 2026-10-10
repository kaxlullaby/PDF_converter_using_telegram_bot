"""doctor.py: pemeriksa kesiapan sebelum deployment."""
from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from importlib import metadata
from unittest.mock import patch

import config as cfg
import doctor
from tests.fakes import TmpCase

TOKEN = "123456789:AAH-abcdefghijklmnopqrstuvwxyz012345"


class DoctorCase(TmpCase):
    KEYS = ("BOT_TOKEN", "DATABASE_PATH", "LOG_FILE", "ADMIN_IDS", "OCR_LANGUAGES")

    def setUp(self):
        super().setUp()
        saved = {k: os.environ.get(k) for k in self.KEYS}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        for key in self.KEYS:
            os.environ.pop(key, None)
        os.environ.update(BOT_TOKEN=TOKEN, DATABASE_PATH=str(self.tmp / "data" / "b.db"), LOG_FILE=str(self.tmp / "logs" / "b.log"))

    @staticmethod
    def levels(findings):
        return [f.level for f in findings]


class TestChecks(DoctorCase):
    def test_python_dan_paket(self):
        self.assertEqual(self.levels(doctor.check_python()), [doctor.OK])
        original = metadata.version

        def fake_version(name):
            if name in ("pypdf", "PyMuPDF"):
                raise metadata.PackageNotFoundError(name)
            return original(name) if name != "python-telegram-bot" else "22.0"

        with patch.object(doctor.metadata, "version", fake_version):
            findings = {f.text.split()[0]: f.level for f in doctor.check_packages()}
        self.assertEqual(findings["pypdf"], doctor.FAIL)       # wajib
        self.assertEqual(findings["PyMuPDF"], doctor.WARN)     # opsional: hanya mematikan 4 fitur

    def test_konfigurasi(self):
        config, error = doctor.load_settings()
        self.assertIsNone(error)
        self.assertEqual(self.levels(doctor.check_config(config, error)), [doctor.OK, doctor.WARN])  # ADMIN_IDS kosong
        os.environ["ADMIN_IDS"] = "111"; os.environ["BOT_TOKEN"] = "bukan-token"
        config, error = doctor.load_settings()
        self.assertEqual(self.levels(doctor.check_config(config, error)), [doctor.OK, doctor.WARN])  # bentuk token aneh
        os.environ["BOT_TOKEN"] = "your_telegram_bot_token"
        config, error = doctor.load_settings()
        result = doctor.check_config(config, error)
        self.assertTrue(result[0].level == doctor.FAIL and "BOT_TOKEN" in result[0].text)

    def test_folder_dan_disk(self):
        config, _ = doctor.load_settings()
        findings = doctor.check_paths(config)
        self.assertNotIn(doctor.FAIL, self.levels(findings))
        self.assertTrue((self.tmp / "data").exists() and (self.tmp / "logs").exists())
        self.assertFalse(list(self.tmp.rglob(".doctor_probe")))  # berkas uji dibersihkan

    def test_folder_tidak_bisa_ditulis(self):
        blocker = self.tmp / "file_biasa"; blocker.write_text("x")
        self.assertIsNotNone(doctor._writable(blocker / "anak"))  # folder tak mungkin dibuat di bawah sebuah file

    def test_program_pendukung_tidak_crash(self):
        config, _ = doctor.load_settings()
        findings = doctor.check_programs(config)
        self.assertTrue(findings and all(f.level in (doctor.OK, doctor.WARN) for f in findings))
        with patch("services.word_to_pdf.find_libreoffice", lambda *_: None), patch("services.pdf_to_word.find_tesseract", lambda *_: None):
            levels = self.levels(doctor.check_programs(config))
        self.assertEqual(levels, [doctor.WARN, doctor.WARN])


class FakeResponse(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *exc): return False


class TestOnline(DoctorCase):
    def run_online(self, **patch_kwargs):
        config, _ = doctor.load_settings()
        with patch("urllib.request.urlopen", **patch_kwargs):
            return doctor.check_online(config)

    def test_token_valid(self):
        body = json.dumps({"ok": True, "result": {"username": "docbot_test"}}).encode()
        findings = self.run_online(return_value=FakeResponse(body))
        self.assertEqual((findings[0].level, "@docbot_test" in findings[0].text), (doctor.OK, True))

    def test_token_ditolak_tidak_membocorkan_token(self):
        error = urllib.error.HTTPError("https://api.telegram.org/botXXX/getMe", 401, "Unauthorized", {}, None)
        finding = self.run_online(side_effect=error)[0]
        self.assertEqual(finding.level, doctor.FAIL); self.assertNotIn(TOKEN, finding.text)

    def test_tanpa_internet(self):
        finding = self.run_online(side_effect=urllib.error.URLError("Name or service not known"))[0]
        self.assertTrue(finding.level == doctor.FAIL and "internet" in finding.text.lower())
        self.assertNotIn(TOKEN, finding.text)

    def test_dns_gagal_pesan_spesifik(self):
        finding = self.run_online(side_effect=urllib.error.URLError("[Errno 11001] getaddrinfo failed"))[0]
        self.assertTrue(finding.level == doctor.FAIL and "DNS" in finding.text and "8.8.8.8" in finding.text)
        self.assertNotIn(TOKEN, finding.text)

    def test_balasan_rusak(self):
        self.assertEqual(self.run_online(return_value=FakeResponse(b"bukan json"))[0].level, doctor.FAIL)


class TestMain(DoctorCase):
    def test_kode_keluar(self):
        with patch.object(doctor, "run_checks", lambda online: [doctor.Finding(doctor.OK, "a"), doctor.Finding(doctor.WARN, "b")]):
            self.assertEqual(doctor.main([]), 0)
        with patch.object(doctor, "run_checks", lambda online: [doctor.Finding(doctor.FAIL, "x")]):
            self.assertEqual(doctor.main([]), 1)

    def test_online_hanya_jika_diminta(self):
        calls = []
        with patch.object(doctor, "run_checks", lambda online: calls.append(online) or []):
            doctor.main([]); doctor.main(["--online"])
        self.assertEqual(calls, [False, True])

    def test_run_checks_utuh_tanpa_error(self):
        findings = doctor.run_checks(online=False)
        self.assertTrue(findings and {f.level for f in findings} <= {doctor.OK, doctor.WARN, doctor.FAIL})


if __name__ == "__main__":
    unittest.main()
