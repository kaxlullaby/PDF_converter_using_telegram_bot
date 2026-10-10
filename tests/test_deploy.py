"""Berkas deployment & dokumentasi: harus konsisten dengan kode dan valid secara sintaks."""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

import doctor

ROOT = Path(__file__).resolve().parent.parent
read = lambda name: (ROOT / name).read_text(encoding="utf-8")


class TestDocumentationMatchesCode(unittest.TestCase):
    def test_semua_variabel_lingkungan_terdokumentasi(self):
        names = set(re.findall(r'(?:getenv|_get_int)\("([A-Z_]+)"', read("config.py")))
        self.assertGreaterEqual(len(names), 16)
        for name in sorted(names):
            with self.subTest(name=name):
                self.assertRegex(read(".env.example"), rf"(?m)^#?\s*{name}=", name + " tidak ada di .env.example")
                self.assertIn(f"`{name}`", read("README.md"), name + " tidak ada di README")

    def test_semua_modul_ada_di_struktur_project_readme(self):
        readme = read("README.md")
        for folder in ("handlers", "services", "utils", "database"):
            for path in sorted((ROOT / folder).glob("*.py")):
                if path.name != "__init__.py":
                    with self.subTest(path=f"{folder}/{path.name}"):
                        self.assertIn(path.name, readme)
        for name in ("bot.py", "config.py", "doctor.py", "Dockerfile", "docker-compose.yml", "docbot.service", "install_ubuntu.sh", "windows_task.ps1"):
            self.assertIn(name, readme)

    def test_dua_belas_bagian_spesifikasi_ada(self):
        """Kedua belas butir README yang diminta spesifikasi harus punya judul sendiri."""
        headings = re.findall(r"(?m)^#{2,3}\s+(?:\d+\.\s+)?(.+)$", read("README.md").lower())
        for wanted in ("project overview", "fitur", "persyaratan", "instalasi", "variabel lingkungan", "botfather",
                       "menjalankan bot", "struktur project", "cara menggunakan", "keterbatasan", "keamanan", "deployment"):
            with self.subTest(heading=wanted):
                self.assertTrue(any(wanted in h for h in headings), f"judul '{wanted}' tidak ada di README")

    def test_paket_yang_diperiksa_doctor_ada_di_requirements(self):
        requirements = read("requirements.txt").lower()
        for name, *_ in doctor.PACKAGES:
            self.assertIn(name.lower().split("[")[0], requirements)

    def test_batas_bawaan_di_readme_sesuai_kode(self):
        readme = read("README.md")
        for expected in ("`20971520`", "`104857600`", "`600`", "`300`", "`ind+eng`", "`data/bot.db`", "`logs/bot.log`"):
            self.assertIn(expected, readme)


class TestDeployFiles(unittest.TestCase):
    def test_rahasia_tidak_masuk_repo_atau_image(self):
        for name in (".gitignore", ".dockerignore"):
            self.assertIn(".env", read(name).splitlines(), name)
        self.assertNotRegex(read("Dockerfile"), r"COPY\s+\.env")

    def test_dockerfile_dan_compose(self):
        dockerfile, compose = read("Dockerfile"), read("docker-compose.yml")
        self.assertIn("USER docbot", dockerfile)                      # bukan root
        self.assertTrue(re.search(r'CMD \["python", "bot.py"\]', dockerfile))
        for package in ("libreoffice-writer", "tesseract-ocr-ind"):
            self.assertIn(package, dockerfile)
        for key in ("mem_limit", "pids_limit", "no-new-privileges", "cap_drop", "restart: unless-stopped", "env_file: .env"):
            self.assertIn(key, compose)

    def test_unit_systemd_punya_perlindungan_penting(self):
        unit = read("deploy/docbot.service")
        for key in ("Restart=always", "MemoryMax=", "NoNewPrivileges=true", "WantedBy=multi-user.target", "TimeoutStopSec="):
            self.assertIn(key, unit)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze tidak tersedia")
    def test_unit_systemd_valid_menurut_systemd(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            text = read("deploy/docbot.service")
            for old, new in (("User=docbot", "User=root"), ("Group=docbot", "Group=root"),
                             ("WorkingDirectory=/opt/telegram-document-bot", "WorkingDirectory=/tmp"),
                             ("ExecStart=/opt/telegram-document-bot/.venv/bin/python bot.py", "ExecStart=/usr/bin/python3 bot.py")):
                text = text.replace(old, new)
            for variant, content in (("bawaan", text), ("ketat", re.sub(r"^#(Protect|ReadWrite|Restrict)", r"\1", text, flags=re.M))):
                path = Path(tmp) / f"{variant}.service"; path.write_text(content)
                result = subprocess.run(["systemd-analyze", "verify", str(path)], capture_output=True, text=True)
                self.assertEqual((result.stdout + result.stderr).strip(), "", f"unit {variant}: {result.stderr}")

    @unittest.skipUnless(shutil.which("bash"), "bash tidak tersedia")
    def test_skrip_pemasangan_sintaks_dan_dry_run(self):
        script = ROOT / "deploy" / "install_ubuntu.sh"
        self.assertEqual(subprocess.run(["bash", "-n", str(script)]).returncode, 0)
        result = subprocess.run(["bash", str(script)], capture_output=True, text=True, env={"DRY_RUN": "1", "APP_DIR": "/opt/uji", "PATH": "/usr/bin:/bin"})
        self.assertEqual(result.returncode, 0, result.stderr)
        for step in ("1/6", "2/6", "3/6", "4/6", "5/6", "6/6"):
            self.assertIn(step, result.stdout)
        self.assertTrue(all(line.startswith("[dry-run]") or not line.strip() or line.startswith(("==>", " ", "Pemeriksaan", "Pemasangan"))
                            or re.match(r"\d\.", line.strip()) for line in result.stdout.splitlines()), "dry-run tidak boleh menjalankan perintah sungguhan")
        self.assertIn("--exclude .env", result.stdout)                 # rsync tidak menimpa token
        self.assertIn("chmod 600 /opt/uji/.env", result.stdout)        # .env hanya bisa dibaca pemiliknya
        self.assertIn("install -m 644", result.stdout)

    def test_skrip_windows_punya_opsi_hapus_dan_pengecekan(self):
        script = read("deploy/windows_task.ps1")
        for key in ("-Remove", "Register-ScheduledTask", "-AtStartup", "SYSTEM", ".env", "RestartCount"):
            self.assertIn(key, script)


if __name__ == "__main__":
    unittest.main()
