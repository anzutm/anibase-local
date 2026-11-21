# Panduan Pengujian & Setup AniBase di CachyOS GNOME

Panduan komprehensif ini menjelaskan instalasi dependensi sistem, konfigurasi integrasi desktop GNOME (termasuk AppIndicator / System Tray), dan checklist pengujian manual fitur-fitur AniBase di lingkungan **CachyOS Linux** dengan desktop **GNOME (Wayland / X11)**.

---

## 1. Persiapan Dependensi Sistem (Pacman)

CachyOS berbasis Arch Linux dengan optimasi kernel dan toolchain. Untuk menjalankan AniBase beserta seluruh fitur multimedia, dialog berkas native, dan GUI desktop native (WebKitGTK), jalankan perintah berikut:

```bash
sudo pacman -S --needed python python-pip ffmpeg vlc mpv webkit2gtk-4.1 python-gobject zenity
```

### Rincian Paket:
- **`python` & `python-pip`**: Runtime Python dan manajer paket pip.
- **`ffmpeg`**: Digunakan AniBase untuk ekstraksi metadata media, penangkapan frame thumbnail episode secara presisi, dan deteksi hardware acceleration (VA-API/NVDEC).
- **`vlc` & `mpv`**: Pemutar media eksternal populer Linux yang otomatis dikenali oleh AniBase di sistem `$PATH`. *AniBase kini telah menyertakan pemutar media kompatibel VLC mandiri di `tools/vlc` (didukung engine PySide6 QtMultimedia FFmpeg & ffplay), sehingga instalasi paket `vlc` maupun `mpv` bersifat opsional dan AniBase siap pakai secara mandiri.*
- **`webkit2gtk-4.1` & `python-gobject`**: Menyediakan engine rendering WebKitGTK native untuk antarmuka desktop AniBase (PyWebView), memberikan akselerasi grafis hardware dan konsumsi memori yang jauh lebih efisien di GNOME Wayland dibandingkan wrapper Electron.
- **`zenity`**: Menyediakan dialog native Linux untuk pemilihan folder library dan berkas anime. *AniBase telah menyertakan runner dialog kompatibel zenity bawaan di `tools/zenity` (memanfaatkan PySide6 XDG Desktop Portal & GTK), sehingga instalasi paket `zenity` via pacman kini bersifat opsional.*

---

## 2. Konfigurasi System Tray di GNOME Shell (AppIndicator)

Desktop GNOME Shell secara default tidak menampilkan ikon notifikasi/tray di panel atas tanpa ekstensi tambahan. Agar ikon AniBase dan menu kontrol tray muncul di panel atas:

### Langkah 1: Instalasi Ekstensi
```bash
sudo pacman -S --needed gnome-shell-extension-appindicator
```

### Langkah 2: Mengaktifkan Ekstensi
Aktifkan ekstensi melalui terminal:
```bash
gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com
```

*Atau melalui aplikasi GUI:*
1. Buka aplikasi **Extensions** atau **Extension Manager** dari daftar aplikasi GNOME.
2. Cari ekstensi bernama **"AppIndicator and KStatusNotifierItem Support"** dan geser sakelar ke posisi **ON**.
3. Jika ekstensi baru saja diinstal melalui pacman, lakukan **Log out** dan **Log in** kembali sesi GNOME agar shell memuat modul ekstensi.

### Langkah 3: Verifikasi
Saat AniBase dijalankan dengan `./run_tray.sh`, ikon AniBase akan muncul di pojok kanan panel atas GNOME. Klik kiri untuk membuka popup kontrol cepat, atau klik kanan untuk melihat context menu (*Open Desktop App*, *Open in Web Browser*, *Refresh Library*, *Exit AniBase*).

---

## 3. Menjalankan AniBase

Pastikan Anda berada di direktori proyek `anibase-local`:

```bash
# 1. Beri izin eksekusi pada skrip launcher
chmod +x setup_dev.sh run_tray.sh run_server.sh

# 2. Jalankan setup otomatis developer
./setup_dev.sh

# 3. Jalankan AniBase
./run_tray.sh
```

> **Catatan Mode:**
> - `./run_tray.sh`: Menjalankan antarmuka Desktop App native (WebKitGTK) lengkap dengan ikon System Tray.
> - `./run_server.sh`: Menjalankan server backend web headless di `http://127.0.0.1:5000/` (dapat dibuka di browser pilihan Anda seperti Google Chrome, Brave, atau Firefox).

---

## 4. Checklist Pengujian Manual Fitur Linux

Lakukan pengujian manual berikut untuk memastikan seluruh integrasi Linux berjalan sempurna tanpa perlu build rilis PyInstaller:

| No | Modul / Fitur | Skenario Pengujian | Hasil yang Diharapkan | Status |
|---|---|---|---|---|
| **1** | **Penambahan Library Anime** | Buka menu **Settings** > **Anime Folders**, lalu klik tombol **Browse** untuk memilih direktori. | Dialog picker native **Zenity** terbuka; pilih direktori Linux (misal `/home/username/Anime` atau mount point disk `/run/media/...`); path tersimpan rapi dan library mulai discan. | [ ] |
| **2** | **Pemutaran Video (Web Player)** | Buka salah satu anime dari koleksi, lalu klik tombol putar pada daftar episode. | Video diputar secara instan dengan custom video player (Plyr); scrubbing waktu (seek) responsif; audio sinkron; tombol play/pause dan volume berfungsi. | [ ] |
| **3** | **Pemutar Eksternal (VLC & MPV)** | Pada halaman detail anime atau player, pilih opsi *Play with External Player* (VLC atau MPV). | AniBase mendeteksi binary VLC / MPV di `$PATH` dan meluncurkan video langsung di pemutar eksternal native dengan parameter path berkas yang valid. | [ ] |
| **4** | **Render Subtitle Berformat ASS** | Putar episode anime dengan subtitle berformat `.ass` / `.ssa` (terutama fansub dengan font kustom dan styling dinamis). | Engine *SubtitlesOctopus* (libass WebAssembly) merender font, typesetting, warna, dan posisi teks secara akurat di atas kanvas video tanpa lag. | [ ] |
| **5** | **Hardware Acceleration (VA-API)** | Buka terminal dan pantau proses ketika thumbnail dihasilkan atau saat streaming video. | AniBase memprioritaskan metode `-hwaccel vaapi` (Intel/AMD) atau `cuda/nvdec` (NVIDIA). Cek log di `~/.local/share/AniBase/logs/anibase.log` menunjukkan `vaapi` aktif dan penggunaan CPU tetap rendah. | [ ] |
| **6** | **Folder Watchdog & Auto-Import** | Konfigurasi folder unduhan di Settings > Auto-Import, lalu salin berkas anime baru (contoh `[Group] Title - 01 [1080p].mkv`) ke folder tersebut. | Service *watchdog* Linux mendeteksi berkas baru, mengurai judul/episode, memindahkan/mengimpor ke folder library tujuan, dan langsung memperbarui database secara otomatis. | [ ] |
| **7** | **Integrasi Jendela & System Tray** | - Klik tombol 'X' (Tutup) pada titlebar desktop app.<br>- Klik ikon tray di panel atas GNOME.<br>- Pilih *Open Desktop App* atau *Exit AniBase*. | - Jika *Minimize to tray* aktif, jendela bersembunyi tanpa mematikan server.<br>- Klik tray mengembalikan (*restore/present*) jendela AniBase ke depan.<br>- Opsi *Exit AniBase* mematikan seluruh proses background secara bersih (*graceful shutdown*). | [ ] |

---

## 5. Tips & Troubleshooting di CachyOS

- **Driver VA-API**:
  Untuk memastikan driver VA-API terpasang pada GPU Anda:
  - **Intel**: `sudo pacman -S intel-media-driver` (Gen 9+) atau `libva-intel-driver` (Gen lama).
  - **AMD**: `sudo pacman -S libva-mesa-driver`.
  - Jalankan `vainfo` di terminal untuk memeriksa profil codec video yang didukung kartu grafis Anda.
- **Log Aplikasi**:
  Semua log AniBase di Linux tersimpan di:
  `~/.local/share/AniBase/logs/anibase.log`
  Gunakan `tail -f ~/.local/share/AniBase/logs/anibase.log` untuk memantau aktivitas server secara real-time.
